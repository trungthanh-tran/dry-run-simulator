from solana.rpc.api import Client, Pubkey
from spl.token._layouts import MINT_LAYOUT

# Establish a connection to the Solana cluster (e.g., mainnet-beta)
http_client = Client("https://api.mainnet-beta.solana.com")

# Define the Public Key of the token mint you want to query
# Replace with the actual mint address of the token
token_mint_address = Pubkey.from_string("BVG3BJH4ghUPJT9mCi7JbziNwx3dqRTzgo9x5poGpump") # Example: USDC mint address

# Get the account information for the token mint
account_info = http_client.get_account_info(token_mint_address)

print(f"{account_info}")
# Parse the account data using MINT_LAYOUT to extract the decimals
if account_info.value and account_info.value.data:
    mint_data = MINT_LAYOUT.parse(account_info.value.data)
    decimals = mint_data.decimals
    print(f"The token at {token_mint_address} has {decimals} decimals.")
else:
    print(f"Could not retrieve account information for {token_mint_address}.")