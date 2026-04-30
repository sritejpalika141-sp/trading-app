import os
import json
from dotenv import load_dotenv
from fyers_apiv3 import fyersModel

ENV_PATH = "/Users/sritejpalika/Sritej Trading/fyers-mcp-server/.env"
load_dotenv(ENV_PATH)

client_id = os.getenv("FYERS_CLIENT_ID")
access_token = os.getenv("FYERS_ACCESS_TOKEN")

client = fyersModel.FyersModel(
    client_id=client_id,
    is_async=False,
    token=access_token,
    log_path=""
)

resp = client.funds()
print(f"Funds Response: {json.dumps(resp, indent=2)}")
