"""
core/config.py
--------------
Central configuration for the IDSS Data Platform.

Environment detection:
  - Local  → credentials loaded from the file system (IDSS Automation folder)
              + environment variables loaded from .env (via python-dotenv)
  - Cloud Run → credentials loaded from environment variables / Secret Manager

Cloud Run automatically sets the K_SERVICE environment variable.

To update the Apps Script deployment ID locally:
  1. Edit WEBAPP_URL in .env
  2. Update the WEBAPP_URL secret in GitHub Actions (Settings → Secrets → Actions)
"""

import os

from dotenv import load_dotenv

# Load .env for local development (no-op in CI/GitHub Actions where env vars
# are already injected; safe to call regardless of environment).
load_dotenv()

# ─── Environment ──────────────────────────────────────────────────────────────
# Cloud Run sets K_SERVICE; GitHub Actions sets GITHUB_ACTIONS=true.
# Both are headless — credentials come from env vars, not local files.
IS_CLOUD_RUN      = bool(os.environ.get("K_SERVICE"))
IS_GITHUB_ACTIONS = bool(os.environ.get("GITHUB_ACTIONS"))
IS_HEADLESS       = IS_CLOUD_RUN or IS_GITHUB_ACTIONS
ENV_NAME = (
    "cloud-run"      if IS_CLOUD_RUN      else
    "github-actions" if IS_GITHUB_ACTIONS else
    "local"
)

# ─── Paths (same in both environments) ────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRANCH_DIR = os.path.join(BASE_DIR, "data", "downloads", "branch")
UNIT_DIR   = os.path.join(BASE_DIR, "data", "downloads", "unit")
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "output")
TEMP_DIR   = os.path.join(BASE_DIR, "data", "temp")
LOG_DIR    = os.path.join(BASE_DIR, "logs")

# ─── Auth — Local (file-based) ────────────────────────────────────────────────
# Credentials live in the legacy IDSS Automation folder (sibling of this project)
_CREDS_DIR           = os.path.join(os.path.dirname(BASE_DIR), "IDSS Automation")
CREDENTIALS_PATH     = os.path.join(_CREDS_DIR, "credentials1.json")
TOKEN_PATH           = os.path.join(_CREDS_DIR, "token.json")
CLIENT_SECRET_PATH   = os.path.join(_CREDS_DIR, "client_secrets.json")
DRIVE_CRED_PATH      = os.path.join(_CREDS_DIR, "drive_token.json")
SERVICE_ACCOUNT_PATH = os.path.join(_CREDS_DIR, "service_account.json")

# ─── Auth — Cloud Run (env-var based) ─────────────────────────────────────────
# These are populated in Cloud Run via Secret Manager or env var overrides.
# Each holds the raw JSON string of the corresponding credential file.
GMAIL_TOKEN_JSON     = os.environ.get("GMAIL_TOKEN_JSON", "")       # contents of token.json
SERVICE_ACCOUNT_JSON = os.environ.get("SERVICE_ACCOUNT_JSON", "")   # contents of service_account.json
DRIVE_TOKEN_JSON     = os.environ.get("DRIVE_TOKEN_JSON", "")        # contents of drive_token.json (user OAuth for Drive)

# ─── Gmail ────────────────────────────────────────────────────────────────────
GMAIL_SCOPES         = ["https://www.googleapis.com/auth/gmail.readonly"]
BRANCH_SUBJECT_QUERY = "Branch Production Reports"
UNIT_SUBJECT_QUERY   = "Unit Production Reports"
MAX_MESSAGES         = 30
DOWNLOAD_RETRIES     = 3
PARALLEL_WORKERS     = 3

# ─── ZIP / PDF ────────────────────────────────────────────────────────────────
ZIP_PASSWORD = b"5fb85964"

# ─── Google Drive ─────────────────────────────────────────────────────────────
# Set in .env (local) or GitHub Secret DRIVE_FOLDER_ID (CI)
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "")
DRIVE_SCOPES    = ["https://www.googleapis.com/auth/drive.file"]

# ─── Google Apps Script ───────────────────────────────────────────────────────
# Set in .env (local) or GitHub Secret WEBAPP_URL (CI).
# Format: https://script.google.com/macros/s/<DEPLOYMENT_ID>/exec
# ⚠️  Update BOTH .env AND the GitHub Secret whenever the Apps Script
#     is redeployed with a NEW deployment (new deployment ID).
#     To keep the ID stable: always redeploy to the EXISTING deployment
#     in Apps Script → Manage Deployments → Edit (pencil icon) → Deploy.
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")

# ─── Google Sheets ────────────────────────────────────────────────────────────
# The ID from the spreadsheet URL:
# https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit
# ⚠️  Share the sheet with your service account email (Editor access).
#     Find the SA email in SERVICE_ACCOUNT_JSON under "client_email".
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

# ─── PRISM Portal ─────────────────────────────────────────────────────────────
# Credentials for https://prism.prulifeuk.com.ph
PRISM_USERNAME = os.environ.get("PRISM_USERNAME", "")
PRISM_PASSWORD = os.environ.get("PRISM_PASSWORD", "")

# Gmail OAuth2 token for the plukfloroespiritu account that receives OTP emails.
# Generate once locally with: python scripts/generate_prism_otp_token.py
# Then add file contents as GitHub Secret PRISM_OTP_TOKEN_JSON.
PRISM_OTP_TOKEN_JSON = os.environ.get("PRISM_OTP_TOKEN_JSON", "")

# ─── AI ───────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_MODEL          = "claude-opus-4-6"
AI_MAX_TOKENS     = 4096

# ─── Ensure local directories exist ──────────────────────────────────────────
for _dir in (BRANCH_DIR, UNIT_DIR, OUTPUT_DIR, TEMP_DIR, LOG_DIR):
    os.makedirs(_dir, exist_ok=True)
