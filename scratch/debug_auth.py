import os
from dotenv import load_dotenv
from fyers_apiv3 import fyersModel

ENV_PATH = "/Users/sritejpalika/Sritej Trading/fyers-mcp-server/.env"
load_dotenv(ENV_PATH)

client_id = os.getenv("FYERS_CLIENT_ID")
access_token = os.getenv("FYERS_ACCESS_TOKEN")

print(f"Client ID: {client_id}")
# print(f"Token: {access_token[:20]}...")

fyers = fyersModel.FyersModel(
    client_id=client_id,
    is_async=False,
    token=access_token,
    log_path=""
)

profile = fyers.get_profile()
print(f"Profile Response: {profile}")
