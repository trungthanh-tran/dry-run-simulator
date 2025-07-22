# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# API and Wallet Configuration
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL")
JUPITER_API_URL = os.getenv("JUPITER_API_URL")
PRIVATE_KEY_BASE58 = os.getenv("PRIVATE_KEY_BASE58")
PNL_WALLET_ADDRESS = os.getenv("PNL_WALLET_ADDRESS")

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL")

# Bot Operation Settings
DRY_RUN = os.getenv("DRY_RUN", "False").lower() == "true"
SCHEDULED_TASK_INTERVAL_SECONDS = int(os.getenv("SCHEDULED_TASK_INTERVAL_SECONDS", 180)) # Default to 3 minutes

# Solana Constants
WSOL_MINT_ADDRESS = "So11111111111111111111111111111111111111112"
SOL_DECIMALS = 9 # SOL is 9 decimals

# Validate essential configurations
if not all([SOLANA_RPC_URL, JUPITER_API_URL, PRIVATE_KEY_BASE58, DATABASE_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, PNL_WALLET_ADDRESS]):
    raise ValueError("One or more environment variables are not set. Please check your .env file.")