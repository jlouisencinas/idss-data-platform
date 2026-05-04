"""
scripts/generate_prism_otp_token.py
-------------------------------------
ONE-TIME SETUP — Generate a Gmail OAuth2 token for the plukfloroespiritu
account that receives PRISM OTP emails.

Run this script ONCE locally (it will open a browser for you to log in),
then copy the output token JSON into:
  1. Your local .env  →  PRISM_OTP_TOKEN_JSON=<paste here>
  2. GitHub Secret    →  PRISM_OTP_TOKEN_JSON

Usage:
    python scripts/generate_prism_otp_token.py

Requirements:
    - credentials1.json (or credentials.json) in the IDSS Automation folder
    - You must log in as plukfloroespiritu@gmail.com in the browser window
      that opens
"""

import json
import os
import sys

# Add project root to path so we can import core modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Adjust this path to your credentials file if needed
BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDS_DIR    = os.path.join(os.path.dirname(BASE_DIR), "IDSS Automation")
CREDS_FILE   = os.path.join(CREDS_DIR, "credentials1.json")
OUTPUT_FILE  = os.path.join(BASE_DIR, "prism_otp_token.json")


def main():
    print("=" * 60)
    print("PRISM OTP Gmail Token Generator")
    print("=" * 60)
    print()

    if not os.path.exists(CREDS_FILE):
        print(f"ERROR: credentials file not found at:\n  {CREDS_FILE}")
        print()
        print("Make sure credentials1.json (Google OAuth client secret) exists")
        print("in your IDSS Automation folder.")
        sys.exit(1)

    print("A browser window will open. Log in as:  plukfloroespiritu@gmail.com")
    print("(This is the account that receives PRISM OTP emails)")
    print()
    input("Press Enter to continue...")

    flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    token_json = creds.to_json()

    # Save to file for reference
    with open(OUTPUT_FILE, "w") as f:
        f.write(token_json)

    print()
    print("=" * 60)
    print(f"Token saved to: {OUTPUT_FILE}")
    print()
    print("Next steps:")
    print()
    print("1. Copy the token into your .env file:")
    print("   PRISM_OTP_TOKEN_JSON=<single-line JSON contents>")
    print()
    print("   To get the single-line version, run:")
    print("   python -c \"import json; d=json.load(open('prism_otp_token.json')); print(json.dumps(d))\"")
    print()
    print("2. Add it as a GitHub Secret:")
    print("   Repository → Settings → Secrets and variables → Actions")
    print("   Name:  PRISM_OTP_TOKEN_JSON")
    print("   Value: <same single-line JSON>")
    print()
    print("3. Delete prism_otp_token.json from this folder after copying")
    print("   (it's gitignored but best not to leave credentials lying around)")
    print("=" * 60)


if __name__ == "__main__":
    main()
