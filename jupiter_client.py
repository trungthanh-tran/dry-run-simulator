# jupiter_client.py
import httpx
import json
import logging
from typing import Optional, Tuple
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed
from datetime import datetime

from config import SOLANA_RPC_URL, JUPITER_API_URL, PRIVATE_KEY_BASE58, WSOL_MINT_ADDRESS, SOL_DECIMALS, DRY_RUN

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class JupiterSwapClient:
    def __init__(self):
        self.solana_client = Client(SOLANA_RPC_URL)
        self.jupiter_api_url = JUPITER_API_URL
        self.http_client = httpx.AsyncClient()
        self.sol_price_cache = {"price": 0.0, "timestamp": 0} # Cache for SOL price

        if DRY_RUN:
            if not PRIVATE_KEY_BASE58:
                self.payer = Keypair() # Create a new random dummy keypair for simulation
                logging.warning("DRY_RUN is True and PRIVATE_KEY_BASE58 is not set. Using a dummy keypair for simulation.")
            else:
                self.payer = Keypair.from_base58_string(PRIVATE_KEY_BASE58)
                logging.info("DRY_RUN is True. Using provided private key for public address, but no actual transactions will be sent.")
        else:
            if not PRIVATE_KEY_BASE58:
                raise ValueError("PRIVATE_KEY_BASE58 must be set when DRY_RUN is False.")
            self.payer = Keypair.from_base58_string(PRIVATE_KEY_BASE58)
            logging.info(f"JupiterSwapClient initialized for Payer: {self.payer.pubkey()}")

    async def _get_sol_price_usd(self) -> float:
        """Fetches the current price of SOL in USD with caching."""
        current_time = datetime.now().timestamp()
        if current_time - self.sol_price_cache["timestamp"] < 300 and self.sol_price_cache["price"] > 0: # Cache for 5 minutes
            return self.sol_price_cache["price"]

        try:
            response = await self.http_client.get("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd")
            response.raise_for_status()
            data = response.json()
            price = data['solana']['usd']
            self.sol_price_cache = {"price": price, "timestamp": current_time}
            return price
        except httpx.HTTPStatusError as e:
            logging.error(f"HTTP error fetching SOL price: {e}")
            return 0.0
        except Exception as e:
            logging.error(f"Error fetching SOL price: {e}")
            return 0.0

    async def get_token_market_cap_and_price(self, mint_address: str) -> Tuple[Optional[float], Optional[float]]:
        """
        Fetches the market cap and price of a given token from CoinGecko.
        Returns (market_cap_usd, token_price_usd)
        """
        if mint_address == WSOL_MINT_ADDRESS: # For SOL, market cap is for SOL itself
            sol_price = await self._get_sol_price_usd()
            # This is a rough estimate for SOL market cap based on circulating supply
            # A more accurate figure would require a separate endpoint or fixed value.
            # Using a large placeholder for now if actual SOL market cap is needed
            sol_market_cap = 50_000_000_000.0 # Example: 50 Billion USD (approx)
            return sol_market_cap, sol_price

        try:
            # Use CoinGecko's coins/{id} endpoint for detailed data including market cap
            # First, find the CoinGecko ID for the token's mint address
            # This requires a mapping or searching CoinGecko's API, which can be complex.
            # For simplicity, we'll try a direct lookup using /simple/token_price,
            # but it only gives price, not market cap directly for a contract address.
            # To get market cap, you often need the CoinGecko 'id' of the token.
            # For a proper solution, you'd need a separate mechanism to map CA to CoinGecko ID.

            # As a workaround, let's just try to get price for market cap calculation.
            # This is NOT ideal for getting actual market cap, but for this bot's purpose
            # of *calculating* market cap from price and supply, it might suffice
            # if we get supply elsewhere.
            # CoinGecko's /simple/token_price only gives price.
            # A better approach for MC would be /coins/{id} after finding the ID.

            # For now, let's assume we can get a price and that MC can be derived if total supply is known.
            # This function needs refinement if actual MC from CoinGecko is critical.
            # For a quick fix, let's try getting price only for calculation.
            response = await self.http_client.get(
                f"https://api.coingecko.com/api/v3/simple/token_price/solana?contract_addresses={mint_address}&vs_currencies=usd"
            )
            response.raise_for_status()
            token_price_data = response.json()
            token_price_usd = token_price_data.get(mint_address, {}).get('usd')
            if not token_price_usd:
                logging.warning(f"Could not fetch USD price for token {mint_address} from CoinGecko simple/token_price.")
                return None, None

            # To get market cap, you'd typically need total supply (from chain or another API)
            # and multiply by token_price_usd. CoinGecko's simple/token_price doesn't give supply.
            # This part requires more advanced CoinGecko API usage or other data sources.
            # For now, return price, and MC will be None, or estimated elsewhere.
            # Let's return a dummy large market cap if price is found, for simulation purposes
            # where actual MC isn't needed beyond being a large number.
            return None, token_price_usd # Return price, but market cap remains None without total supply

        except httpx.HTTPStatusError as e:
            logging.warning(f"HTTP error fetching token data for {mint_address} from CoinGecko: {e}. Token might not be on CoinGecko or lack data.")
            return None, None
        except Exception as e:
            logging.error(f"Error fetching token market cap and price for {mint_address}: {e}")
            return None, None

    async def get_sol_balance(self) -> float:
        """Fetches the SOL balance of the payer wallet."""
        if DRY_RUN:
            # In DRY_RUN, return a large mock balance to simulate sufficient funds.
            return 1000.0 # Simulate 1000 SOL available for dry run
        try:
            balance_lamports = self.solana_client.get_balance(self.payer.pubkey()).value
            balance_sol = balance_lamports / (10**SOL_DECIMALS)
            return balance_sol
        except Exception as e:
            logging.error(f"Error fetching SOL balance for {self.payer.pubkey()}: {e}")
            return 0.0

    async def get_token_balance(self, mint_address: str) -> float:
        """Fetches the balance of a specific SPL token for the payer wallet."""
        if DRY_RUN:
            # In DRY_RUN, simulate having a large amount of a token if we expect to sell it.
            # This could be refined to check if the CA is "held" in the simulated environment.
            # For now, assume a large balance for any queried token to allow sell simulations.
            logging.info(f"DRY_RUN: Simulating token balance for {mint_address}")
            return 1_000_000_000.0 # Simulate 1 billion units of the token
        try:
            # Get token accounts for the payer's public key
            response = self.solana_client.get_token_accounts_by_owner(
                self.payer.pubkey(),
                {"mint": Pubkey.from_string(mint_address)},
                Confirmed
            )
            if response.value:
                # Assuming the first account holds the balance
                token_account_info = response.value[0].account.data.parsed['info']
                token_balance = int(token_account_info['tokenAmount']['amount'])
                token_decimals = int(token_account_info['tokenAmount']['decimals'])
                return token_balance / (10**token_decimals)
            return 0.0
        except Exception as e:
            logging.error(f"Error fetching token balance for {mint_address}: {e}")
            return 0.0

    async def get_quote_and_swap(self, input_mint: str, output_mint: str, amount: int) -> Tuple[Optional[str], float, float]:
        """
        Gets a quote from Jupiter and executes a swap.
        Returns (transaction_signature, sol_spent/received, ca_bought/sold)
        amount is in lamports/raw token units.
        """
        # In DRY_RUN mode, we simulate the swap without actual on-chain transaction
        if DRY_RUN:
            logging.info(f"DRY_RUN: Simulating swap from {input_mint} to {output_mint} with raw amount {amount}.")
            try:
                # Get a real quote from Jupiter to accurately simulate the output amount
                quote_params = {
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": amount,
                    "slippageBps": 50 # 0.5% slippage
                }
                quote_response = await self.http_client.get(f"{self.jupiter_api_url}/quote", params=quote_params)
                quote_response.raise_for_status()
                quote = quote_response.json()

                if not quote or not quote.get('outAmount'):
                    logging.error("DRY_RUN: Did not receive a valid quote from Jupiter for simulation.")
                    return None, 0.0, 0.0

                expected_output_amount_raw = int(quote['outAmount'])
                output_mint_decimals = int(quote['outputMintDecimals'])
                input_mint_decimals = int(quote['inputMintDecimals'])

                expected_output_amount_normalized = expected_output_amount_raw / (10**output_mint_decimals)
                input_amount_normalized = amount / (10**input_mint_decimals)

                logging.info(f"DRY_RUN: Quote received. Simulated output: {expected_output_amount_normalized:.4f} {output_mint}.")

                tx_sig = "DRY_RUN_SIMULATED_TX"
                if input_mint == WSOL_MINT_ADDRESS: # This was a buy (SOL -> CA)
                    sol_spent = input_amount_normalized
                    ca_amount = expected_output_amount_normalized
                    logging.info(f"DRY_RUN Buy: Spent {sol_spent:.6f} SOL, Received {ca_amount:.4f} CA tokens.")
                    return tx_sig, sol_spent, ca_amount
                else: # This was a sell (CA -> SOL)
                    sol_received = expected_output_amount_normalized
                    ca_amount = input_amount_normalized
                    logging.info(f"DRY_RUN Sell: Sold {ca_amount:.4f} CA tokens, Received {sol_received:.6f} SOL.")
                    return tx_sig, sol_received, ca_amount

            except httpx.HTTPStatusError as e:
                logging.error(f"DRY_RUN: HTTP error during simulated swap quote: {e.response.text}")
                return None, 0.0, 0.0
            except Exception as e:
                logging.error(f"DRY_RUN: Error during simulated swap quote process: {e}")
                return None, 0.0, 0.0
        
        # --- Real Swap Execution (when DRY_RUN is False) ---
        try:
            # 1. Get Quote
            quote_params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": 50 # 0.5% slippage
            }
            quote_response = await self.http_client.get(f"{self.jupiter_api_url}/quote", params=quote_params)
            quote_response.raise_for_status()
            quote = quote_response.json()
            
            if not quote or not quote.get('outAmount'):
                logging.error("Did not receive a valid quote from Jupiter.")
                return None, 0.0, 0.0

            # Determine expected output amount
            expected_output_amount_raw = int(quote['outAmount'])
            output_mint_decimals = int(quote['outputMintDecimals'])
            input_mint_decimals = int(quote['inputMintDecimals'])

            expected_output_amount_normalized = expected_output_amount_raw / (10**output_mint_decimals)
            input_amount_normalized = amount / (10**input_mint_decimals)


            logging.info(f"Received quote. Expected output: {expected_output_amount_normalized:.4f} {output_mint} (with input {input_amount_normalized} {input_mint})")

            # 2. Get Swap Transaction
            swap_params = {
                "quoteResponse": quote,
                "userPublicKey": str(self.payer.pubkey()),
                "wrapAndUnwrapSol": True # Automatically wrap/unwrap SOL
            }
            swap_response = await self.http_client.post(f"{self.jupiter_api_url}/swap", json=swap_params)
            swap_response.raise_for_status()
            swap_data = swap_response.json()
            
            if not swap_data or 'swapTransaction' not in swap_data:
                logging.error("Did not receive a swap transaction from Jupiter.")
                return None, 0.0, 0.0

            # 3. Deserialize and Sign Transaction
            from solders.transaction import VersionedTransaction
            from solders.hash import Hash # Not strictly needed for signing, but good practice
            from base64 import b64decode

            raw_tx = b64decode(swap_data['swapTransaction'])
            transaction = VersionedTransaction.from_bytes(raw_tx)
            
            signed_transaction = self.payer.sign_versioned_transaction(transaction)

            # 4. Send Transaction
            tx_signature = self.solana_client.send_raw_transaction(signed_transaction.serialize(), opts=Confirmed).value
            
            logging.info(f"Transaction sent: {tx_signature}")

            # 5. Confirm Transaction
            # Loop with timeout for confirmation
            timeout_seconds = 60
            start_time = datetime.now()
            while (datetime.now() - start_time).total_seconds() < timeout_seconds:
                confirmation = self.solana_client.confirm_transaction(tx_signature, commitment=Confirmed)
                if confirmation.value.err:
                    logging.error(f"Transaction confirmation failed: {confirmation.value.err}")
                    await self.notifier.send_message(f"🚨 Transaction `{tx_signature}` failed: `{confirmation.value.err}`")
                    return None, 0.0, 0.0
                if not confirmation.value.err: # Confirmed
                    logging.info(f"Transaction confirmed: {tx_signature}")
                    break
                await asyncio.sleep(2) # Wait 2 seconds before checking again
            else:
                logging.error(f"Transaction {tx_signature} timed out during confirmation.")
                await self.notifier.send_message(f"🚨 Transaction `{tx_signature}` timed out during confirmation.")
                return None, 0.0, 0.0


            # Calculate actual SOL spent/received and CA bought/sold
            if input_mint == WSOL_MINT_ADDRESS: # This was a buy
                sol_spent = input_amount_normalized
                ca_amount = expected_output_amount_normalized
                return str(tx_signature), sol_spent, ca_amount
            else: # This was a sell
                sol_received = expected_output_amount_normalized
                ca_amount = input_amount_normalized
                return str(tx_signature), sol_received, ca_amount

        except httpx.HTTPStatusError as e:
            logging.error(f"HTTP error during swap: {e.response.text}")
            return None, 0.0, 0.0
        except Exception as e:
            logging.error(f"Error during swap process: {e}", exc_info=True)
            return None, 0.0, 0.0

    async def get_token_supply(self, mint_address: str) -> Optional[int]:
        """Fetches the total supply of an SPL token."""
        try:
            response = self.solana_client.get_token_supply(Pubkey.from_string(mint_address))
            if response.value:
                return int(response.value.amount) # Returns raw amount
            return None
        except Exception as e:
            logging.error(f"Error fetching token supply for {mint_address}: {e}")
            return None