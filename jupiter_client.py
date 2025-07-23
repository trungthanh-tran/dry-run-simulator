import asyncio
import json
import logging
from typing import Optional, Tuple
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solders.hash import Hash
from datetime import datetime
import requests
from base64 import b64decode
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from spl.token._layouts import MINT_LAYOUT

from config import JUPITER_API_URL, PRIVATE_KEY_BASE58, WSOL_MINT_ADDRESS, SOL_DECIMALS, DRY_RUN

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
async def get_token_decimals(client: AsyncClient, mint_address: str) -> int:
    """
    Retrieves the number of decimals for a given Solana token mint.

    Args:
        client (AsyncClient): The asynchronous Solana client.
        mint_address (str): The public key address of the token mint.

    Returns:
        int: The number of decimals for the token.

    Raises:
        ValueError: If the mint address is invalid or account info cannot be retrieved.
    """
    try:
        # Convert string mint address to PublicKey
        token_mint_address = Pubkey(mint_address)
        
        # Get the account information for the token mint
        account_info = await client.get_account_info(token_mint_address)
        
        # Parse the account data to extract decimals
        if account_info.value and account_info.value.data:
            mint_data = MINT_LAYOUT.parse(account_info.value.data)
            return mint_data.decimals
        else:
            raise ValueError(f"Could not retrieve account information for {mint_address}")
            
    except ValueError as e:
        raise ValueError(f"Invalid mint address or failed to fetch data: {str(e)}")

class JupiterSwapClient:
    def __init__(self):
        self.jupiter_api_url = JUPITER_API_URL
        self.sol_price_cache = {"price": 0.0, "timestamp": 0}  # Cache for SOL price

        if DRY_RUN:
            if not PRIVATE_KEY_BASE58:
                self.payer = Keypair()  # Create a new random dummy keypair for simulation
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
        """Fetches the current price of SOL in USD using Jupiter's quote API with caching."""
        current_time = datetime.now().timestamp()
        if current_time - self.sol_price_cache["timestamp"] < 300 and self.sol_price_cache["price"] > 0:  # Cache for 5 minutes
            return self.sol_price_cache["price"]

        try:
            # Fetch SOL price in USDC using Jupiter's /quote endpoint
            params = {
                "inputMint": WSOL_MINT_ADDRESS,
                "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC mint
                "amount": 10**SOL_DECIMALS,  # 1 SOL in lamports
                "slippageBps": 50
            }
            response = requests.get(f"{self.jupiter_api_url}/quote", params=params)
            response.raise_for_status()
            quote = response.json()
            price = float(quote.get("outAmount", 0)) / (10**6)  # USDC has 6 decimals
            if price <= 0:
                logging.error("Invalid SOL price received from Jupiter API.")
                return 0.0
            self.sol_price_cache = {"price": price, "timestamp": current_time}
            return price
        except requests.RequestException as e:
            logging.error(f"Error fetching SOL price: {e}")
            return 0.0

    async def get_token_market_cap_and_price(self, mint_address: str) -> Tuple[Optional[float], Optional[float]]:
        """
        Fetches the market cap and price of a given token using Jupiter API for price and HTTP APIs for supply.
        Returns (market_cap_usd, token_price_usd)
        """
        if mint_address == WSOL_MINT_ADDRESS:  # For SOL, use cached SOL price
            sol_price = await self._get_sol_price_usd()
            # Placeholder for SOL market cap (rough estimate)
            sol_market_cap = 50_000_000_000.0  # Example: 50 Billion USD
            return sol_market_cap, sol_price

        try:
            # Fetch token price using Jupiter's /quote endpoint
            params = {
                "inputMint": mint_address,
                "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC mint
                "amount": 10**6,  # Assume 6 decimals for input token (adjust as needed)
                "slippageBps": 50
            }
            response = requests.get(f"{self.jupiter_api_url}/quote", params=params)
            response.raise_for_status()
            quote = response.json()
            token_price_usd = float(quote.get("outAmount", 0)) / (10**6)  # USDC has 6 decimals
            if token_price_usd <= 0:
                logging.warning(f"Could not fetch USD price for token {mint_address} from Jupiter API.")
                return None, None

            # Fetch circulating supply
            circulating_supply = None
            # Try CoinGecko first
            coingecko_id = None
            if mint_address == "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4":
                coingecko_id = "jupiter"
            elif mint_address == "BVG3BJH4ghUPJT9mCi7JbziNwx3dqRTzgo9x5poGpump":
                coingecko_id = None  # Pump.fun tokens often not on CoinGecko
                logging.warning(f"No CoinGecko ID for {mint_address}. Falling back to Solscan.")
            else:
                logging.warning(f"No CoinGecko ID mapped for {mint_address}. Falling back to Solscan.")

            if coingecko_id:
                coingecko_url = f"https://api.coingecko.com/api/v3/coins/{coingecko_id}"
                response = requests.get(coingecko_url)
                if response.status_code == 200:
                    data = response.json()
                    circulating_supply = float(data.get("market_data", {}).get("circulating_supply", 0))
                    if circulating_supply <= 0:
                        logging.warning(f"No circulating supply data for {coingecko_id} on CoinGecko.")
                        circulating_supply = None

            # Fallback to Solscan for supply (total supply as proxy)
            if circulating_supply is None:
                solscan_url = f"https://public-api.solscan.io/token/meta?tokenAddress={mint_address}"
                response = requests.get(solscan_url)
                if response.status_code == 200:
                    data = response.json()
                    total_supply = float(data.get("supply", 0))
                    decimals = int(data.get("decimals", 6))  # Default to 6 if not provided
                    circulating_supply = total_supply / (10**decimals)
                else:
                    logging.warning(f"Failed to fetch supply for {mint_address} from Solscan: {response.status_code}")
                    return None, token_price_usd

            if circulating_supply <= 0:
                logging.warning(f"No valid circulating supply for {mint_address}.")
                return None, token_price_usd

            market_cap_usd = token_price_usd * circulating_supply
            return market_cap_usd, token_price_usd
        except requests.RequestException as e:
            logging.error(f"Error fetching token market cap and price for {mint_address}: {e}")
            return None, None

    async def get_sol_balance(self) -> float:
        """Fetches the SOL balance of the payer wallet using Solscan API."""
        if DRY_RUN:
            return 1000.0  # Simulate 1000 SOL for dry run
        try:
            solscan_url = f"https://public-api.solscan.io/account/tokens?account={self.payer.pubkey()}"
            response = requests.get(solscan_url)
            response.raise_for_status()
            tokens = response.json()
            for token in tokens:
                if token.get("tokenAddress") == WSOL_MINT_ADDRESS:
                    return float(token.get("tokenAmount", {}).get("uiAmount", 0))
            return 0.0
        except requests.RequestException as e:
            logging.error(f"Error fetching SOL balance for {self.payer.pubkey()}: {e}")
            return 0.0

    async def get_token_balance(self, mint_address: str) -> float:
        """Fetches the balance of a specific SPL token for the payer wallet using Solscan API."""
        if DRY_RUN:
            logging.info(f"DRY_RUN: Simulating token balance for {mint_address}")
            return 1_000_000_000.0  # Simulate 1 billion units of the token
        try:
            solscan_url = f"https://public-api.solscan.io/account/tokens?account={self.payer.pubkey()}"
            response = requests.get(solscan_url)
            response.raise_for_status()
            tokens = response.json()
            for token in tokens:
                if token.get("tokenAddress") == mint_address:
                    return float(token.get("tokenAmount", {}).get("uiAmount", 0))
            return 0.0
        except requests.RequestException as e:
            logging.error(f"Error fetching token balance for {mint_address}: {e}")
            return 0.0

    async def get_quote_and_swap(self, input_mint: str, output_mint: str, amount: int) -> Tuple[Optional[str], float, float]:
        """
        Gets a quote from Jupiter and executes a swap using HTTP requests.
        Returns (transaction_signature, sol_spent/received, ca_bought/sold)
        amount is in lamports/raw token units.
        """
        solana_client = AsyncClient("https://api.mainnet-beta.solana.com")

        if DRY_RUN:
            logging.info(f"DRY_RUN: Simulating swap from {input_mint} to {output_mint} with raw amount {amount}.")
            try:
                # Get a quote using Jupiter's /quote endpoint
                quote_params = {
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": amount,
                    "slippageBps": 50  # 0.5% slippage
                }
                response = requests.get(f"{self.jupiter_api_url}/quote", params=quote_params)
                response.raise_for_status()
                quote = response.json()

                if not quote or not quote.get('outAmount'):
                    logging.error("DRY_RUN: Did not receive a valid quote from Jupiter API.")
                    return None, 0.0, 0.0

                expected_output_amount_raw = int(quote['outAmount'])
                output_mint_decimals = await get_token_decimals(solana_client, output_mint)
                input_mint_decimals = await get_token_decimals(solana_client, input_mint)
                expected_output_amount_normalized = expected_output_amount_raw / (10**output_mint_decimals)
                input_amount_normalized = amount / (10**input_mint_decimals)

                logging.info(f"DRY_RUN: Quote received. Simulated output: {expected_output_amount_normalized:.4f} {output_mint}.")

                tx_sig = "DRY_RUN_SIMULATED_TX"
                if input_mint == WSOL_MINT_ADDRESS:  # Buy (SOL -> CA)
                    sol_spent = input_amount_normalized
                    ca_amount = expected_output_amount_normalized
                    logging.info(f"DRY_RUN Buy: Spent {sol_spent:.6f} SOL, Received {ca_amount:.4f} CA tokens.")
                    return tx_sig, sol_spent, ca_amount
                else:  # Sell (CA -> SOL)
                    sol_received = expected_output_amount_normalized
                    ca_amount = input_amount_normalized
                    logging.info(f"DRY_RUN Sell: Sold {ca_amount:.4f} CA tokens, Received {sol_received:.6f} SOL.")
                    return tx_sig, sol_received, ca_amount
            except requests.RequestException as e:
                logging.error(f"DRY_RUN: Error during simulated swap quote: {e}")
                return None, 0.0, 0.0

        # Real Swap Execution
        try:
            # Get Quote
            quote_params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": 50
            }
            response = requests.get(f"{self.jupiter_api_url}/quote", params=quote_params)
            response.raise_for_status()
            quote = response.json()

            if not quote or not quote.get('outAmount'):
                logging.error("Did not receive a valid quote from Jupiter API.")
                return None, 0.0, 0.0

            expected_output_amount_raw = int(quote['outAmount'])
            output_mint_decimals = await get_token_decimals(solana_client, output_mint)
            input_mint_decimals = await get_token_decimals(solana_client, input_mint)

            expected_output_amount_normalized = expected_output_amount_raw / (10**output_mint_decimals)
            input_amount_normalized = amount / (10**input_mint_decimals)

            logging.info(f"Received quote. Expected output: {expected_output_amount_normalized:.4f} {output_mint} (with input {input_amount_normalized} {input_mint})")

            # Get Swap Transaction
            swap_params = {
                "quoteResponse": quote,
                "userPublicKey": str(self.payer.pubkey()),
                "wrapAndUnwrapSol": True
            }
            response = requests.post(f"{self.jupiter_api_url}/swap", json=swap_params)
            response.raise_for_status()
            swap_data = response.json()

            if not swap_data or 'swapTransaction' not in swap_data:
                logging.error("Did not receive a swap transaction from Jupiter API.")
                return None, 0.0, 0.0

            # Deserialize and Sign Transaction
            raw_tx = b64decode(swap_data['swapTransaction'])
            transaction = VersionedTransaction.from_bytes(raw_tx)
            signed_transaction = self.payer.sign_versioned_transaction(transaction)

            # Send Transaction (using HTTP-based Solana RPC)
            rpc_url = "https://api.mainnet-beta.solana.com"  # Public RPC, replace with private endpoint for production
            response = requests.post(rpc_url, json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [signed_transaction.serialize().hex()]
            })
            response.raise_for_status()
            tx_result = response.json()
            tx_signature = tx_result.get("result")
            if not tx_signature:
                logging.error("Failed to send transaction.")
                return None, 0.0, 0.0

            # Confirm Transaction
            timeout_seconds = 60
            start_time = datetime.now()
            while (datetime.now() - start_time).total_seconds() < timeout_seconds:
                response = requests.post(rpc_url, json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [tx_signature, {"commitment": "confirmed"}]
                })
                response.raise_for_status()
                confirmation = response.json()
                if confirmation.get("result"):
                    if confirmation["result"].get("meta", {}).get("err"):
                        logging.error(f"Transaction {tx_signature} failed: {confirmation['result']['meta']['err']}")
                        return None, 0.0, 0.0
                    logging.info(f"Transaction confirmed: {tx_signature}")
                    break
                await asyncio.sleep(2)
            else:
                logging.error(f"Transaction {tx_signature} timed out during confirmation.")
                return None, 0.0, 0.0

            # Calculate SOL spent/received and CA bought/sold
            if input_mint == WSOL_MINT_ADDRESS:  # Buy
                sol_spent = input_amount_normalized
                ca_amount = expected_output_amount_normalized
                return str(tx_signature), sol_spent, ca_amount
            else:  # Sell
                sol_received = expected_output_amount_normalized
                ca_amount = input_amount_normalized
                return str(tx_signature), sol_received, ca_amount
        except requests.RequestException as e:
            logging.error(f"Error during swap process: {e}")
            return None