"""
core/config.py
--------------
Central configuration for the IDSS Data Platform.

Environment detection:
  - Local  → credentials loaded from the file system (IDSS Automation folder)
  - Cloud Run → credentials loaded from environment variables / Secret Manager

Cloud Run automatically sets the K_SERVICE environment variable.
"""

import os

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
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "1O00CGI9zSzsbGK4S_n3bTAehJ-TUcvrX")
DRIVE_SCOPES    = ["https://www.googleapis.com/auth/drive.file"]

# ─── Google Apps Script ───────────────────────────────────────────────────────
WEBAPP_URL = os.environ.get(
    "WEBAPP_URL",
    "https://script.google.com/macros/s/"
    "AKfycbzH2cXnuP8AVeMSrJKGqI0iHqjSZ90SdbbNWsNDsBIr1oDb-XTys-M8n1wpU_oMjnX6/exec",
)

# ─── AI ───────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_MODEL          = "claude-opus-4-6"
AI_MAX_TOKENS     = 4096

# ─── Ensure local directories exist ──────────────────────────────────────────
for _dir in (BRANCH_DIR, UNIT_DIR, OUTPUT_DIR, TEMP_DIR, LOG_DIR):
    os.makedirs(_dir, exist_ok=True)
