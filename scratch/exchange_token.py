import os
from fyers_apiv3 import fyersModel

# Credentials
CLIENT_ID = "SENTD5B9M0-200"
SECRET_KEY = "JrjthZkbHu8f6LLU"
REDIRECT_URI = "https://127.0.0.1:8080"
AUTH_CODE = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHBfaWQiOiJTRU5URDVCOU0wIiwidXVpZCI6IjllNzQ1NDQwZTg3ODQ5MTM4YzQ4ODg0NGE3MTgyMmRjIiwiaXBBZGRyIjoiIiwibm9uY2UiOiIiLCJzY29wZSI6IiIsImRpc3BsYXlfbmFtZSI6IlhTNTAwMDkiLCJvbXMiOiJLMSIsImhzbV9rZXkiOiIwOWZiN2Q5MzRiNGFiZDI4Nzk0YWJkNzNlMDUyNzFjZTYwZGUwZDUxYmRlMzFlOWNiN2YwNjBjZiIsImlzRGRwaUVuYWJsZWQiOiJOIiwiaXNNdGZFbmFibGVkIjoiTiIsImF1ZCI6IltcImQ6MVwiLFwiZDoyXCIsXCJ4OjBcIixcIng6MVwiLFwieDoyXCJdIiwiZXhwIjoxNzc2NzczNzUwLCJpYXQiOjE3NzY3NDM3NTAsImlzcyI6ImFwaS5sb2dpbi5meWVycy5pbiIsIm5iZiI6MTc3Njc0Mzc1MCwic3ViIjoiYXV0aF9jb2RlIn0.88eQphhdjTtyD0eIGeJlhM6F3fdeKAbwJIlespnQDUE"

print(f"Exchanging auth_code for access_token...")

session = fyersModel.SessionModel(
    client_id=CLIENT_ID,
    secret_key=SECRET_KEY,
    redirect_uri=REDIRECT_URI,
    response_type="code",
    grant_type="authorization_code"
)

session.set_token(AUTH_CODE)
response = session.generate_token()

if response.get("code") == 200:
    access_token = response.get("access_token")
    print(f"✅ Success! New access_token generated.")
    
    # Update .env file
    ENV_PATH = "/Users/sritejpalika/Sritej Trading/fyers-mcp-server/.env"
    
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, 'r') as f:
            lines = f.readlines()
        
        new_lines = []
        token_found = False
        for line in lines:
            if line.startswith("FYERS_ACCESS_TOKEN="):
                new_lines.append(f"FYERS_ACCESS_TOKEN={access_token}\n")
                token_found = True
            else:
                new_lines.append(line)
        
        if not token_found:
            new_lines.append(f"FYERS_ACCESS_TOKEN={access_token}\n")
            
        with open(ENV_PATH, 'w') as f:
            f.writelines(new_lines)
        
        print(f"✅ Updated {ENV_PATH}")
    else:
        print(f"❌ Error: {ENV_PATH} not found.")
else:
    print(f"❌ Error exchanging token: {response}")
