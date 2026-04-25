"""
scripts/generate_drive_token.py
--------------------------------
One-time local script to generate a Drive OAuth token for use as the
DRIVE_TOKEN_JSON GitHub secret (or Secret Manager entry).

Run once:
    python scripts/generate_drive_token.py

This opens a browser window asking you to authorise Drive access.
The token is saved to drive_token.json — copy its contents into the
DRIVE_TOKEN_JSON secret in GitHub (Settings → Secrets → Actions).
"""

import os
import sys

# Allow imports from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_auth_oauthlib.flow import InstalledAppFlow
from core import config

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..",
    "IDSS Automation",
    "drive_token.json",
)
OUTPUT_PATH = os.path.normpath(OUTPUT_PATH)


def main():
    print(f"Using client secrets: {config.CLIENT_SECRET_PATH}")
    print(f"Token will be saved to: {OUTPUT_PATH}\n")

    flow = InstalledAppFlow.from_client_secrets_file(config.CLIENT_SECRET_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(OUTPUT_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"\nDone! Token saved to: {OUTPUT_PATH}")
    print("\nNext step:")
    print("  1. Open that file and copy ALL its contents.")
    print("  2. Go to GitHub → your repo → Settings → Secrets → Actions.")
    print("  3. Add a new secret named:  DRIVE_TOKEN_JSON")
    print("  4. Paste the file contents as the value.")
    print("  5. Re-run the GitHub Actions workflow.")


if __name__ == "__main__":
    main()
