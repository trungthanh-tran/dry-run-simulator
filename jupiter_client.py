# jupiter_client.py
import httpx
import json
import logging
from typing import Optional, Tuple
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed

from config import SOLANA_RPC_URL, JUPITER_API_URL, PRIVATE_KEY_BASE58, WSOL_MINT_ADDRESS, SOL_DECIMALS

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class JupiterSwapClient:
    def __init__(self):
        self.solana_client = Client(SOLANA_RPC_URL)
        self.jupiter_api_url = JUPITER_API_URL
        self.payer = Keypair.from_base58_string(PRIVATE_KEY_BASE58)
        self.http_client = httpx.AsyncClient()
        logging.info(f"JupiterSwapClient initialized for Payer: {self.payer.pubkey()}")

    async def _get_sol_price_usd(self) -> float:
        """Fetches the current price of SOL in USD."""
        try:
            response = await self.http_client.get("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd")
            response.raise_for_status()
            data = response.json()
            return data['solana']['usd']
        except httpx.HTTPStatusError as e:
            logging.error(f"HTTP error fetching SOL price: {e}")
            return 0.0
        except Exception as e:
            logging.error(f"Error fetching SOL price: {e}")
            return 0.0

    async def get_token_market_cap(self, mint_address: str) -> Optional[float]:
        """Fetches the market cap of a given token."""
        try:
            # First, get the token price in USD
            response = await self.http_client.get(f"https://api.coingecko.com/api/v3/simple/token_price/solana?contract_addresses={mint_address}&vs_currencies=usd")
            response.raise_for_status()
            token_price_data = response.json()

            token_price_usd = token_price_data.get(mint_address.lower(), {}).get('usd')
            if not token_price_usd:
                logging.warning(f"Could not fetch USD price for token {mint_address}.")
                return None

            # As CoinGecko's simple_token_price doesn't directly provide market cap,
            # we'll return None for market cap. For active trading, a more robust market
            # data source or direct on-chain supply fetching would be needed to calculate MC.
            return None # Placeholder, as CoinGecko simple_token_price doesn't provide market cap directly.

        except httpx.HTTPStatusError as e:
            logging.warning(f"HTTP error fetching token market cap for {mint_address}: {e}. Token might not be on CoinGecko or lack sufficient data.")
            return None
        except Exception as e:
            logging.error(f"Error fetching market cap for {mint_address}: {e}")
            return None

    async def get_sol_balance(self) -> float:
        """Fetches the SOL balance of the payer wallet."""
        try:
            balance_lamports = self.solana_client.get_balance(self.payer.pubkey()).value
            balance_sol = balance_lamports / (10**SOL_DECIMALS)
            return balance_sol
        except Exception as e:
            logging.error(f"Error fetching SOL balance: {e}")
            return 0.0

    async def get_quote_and_swap(self, input_mint: str, output_mint: str, amount: int) -> Tuple[Optional[str], float, float]:
        """
        Gets a quote from Jupiter and executes a swap.
        Returns (transaction_signature, sol_spent/received, ca_bought/sold)
        """
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
            
            if not quote:
                logging.error("Did not receive a quote from Jupiter.")
                return None, 0.0, 0.0

            # Determine expected output amount
            expected_output_amount_raw = int(quote['outAmount'])
            output_mint_decimals = int(quote['outputMintDecimals'])
            expected_output_amount_normalized = expected_output_amount_raw / (10**output_mint_decimals)

            logging.info(f"Received quote. Expected output: {expected_output_amount_normalized:.4f} {output_mint} (with input {amount / (10**int(quote['inputMintDecimals']))} {input_mint})")

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
            from solders.hash import Hash
            from base64 import b64decode

            raw_tx = b64decode(swap_data['swapTransaction'])
            transaction = VersionedTransaction.from_bytes(raw_tx)
            
            # The transaction from Jupiter is usually ready to be signed.
            # Sign the transaction
            signed_transaction = self.payer.sign_versioned_transaction(transaction) # Use this for VersionedTransaction

            # 4. Send Transaction
            tx_signature = self.solana_client.send_raw_transaction(signed_transaction.serialize(), opts=Confirmed).value
            
            logging.info(f"Transaction sent: {tx_signature}")

            # 5. Confirm Transaction
            confirmation = self.solana_client.confirm_transaction(tx_signature, commitment=Confirmed)
            if confirmation.value.err:
                logging.error(f"Transaction confirmation failed: {confirmation.value.err}")
                return None, 0.0, 0.0
            
            logging.info(f"Transaction confirmed: {tx_signature}")

            # Calculate actual SOL spent/received and CA bought/sold
            if input_mint == WSOL_MINT_ADDRESS: # This was a buy
                sol_spent = amount / (10**SOL_DECIMALS)
                ca_amount = expected_output_amount_normalized
                return str(tx_signature), sol_spent, ca_amount
            else: # This was a sell
                sol_received = expected_output_amount_normalized
                ca_amount = amount / (10**int(quote['inputMintDecimals'])) # amount of CA sold
                return str(tx_signature), sol_received, ca_amount

        except httpx.HTTPStatusError as e:
            logging.error(f"HTTP error during swap: {e.response.text}")
            return None, 0.0, 0.0
        except Exception as e:
            logging.error(f"Error during swap process: {e}")
            return None, 0.0, 0.0