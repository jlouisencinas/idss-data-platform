"""
connectors/drive_connector.py
------------------------------
CLOUD LAYER — Google Drive API v3 integration.

Auth strategy:
  Local          → OAuth2 browser flow; token cached to drive_token.json
  Cloud Run      → Service Account from SERVICE_ACCOUNT_JSON environment variable
  GitHub Actions → Same as Cloud Run (GITHUB_ACTIONS env var detected)
"""

import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from core import config
from core.logger import get_logger

logger = get_logger(__name__)

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


# ─── Auth helpers ─────────────────────────────────────────────────────────────

def _get_drive_service_local(client_secret_path: str, token_path: str):
    """Local: OAuth2 browser flow with cached token file."""
    creds = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, DRIVE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, DRIVE_SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


def _get_drive_service_cloud():
    """
    Headless (Cloud Run / GitHub Actions) Drive authentication.

    Preference order:
      1. DRIVE_TOKEN_JSON — user OAuth token (recommended; has Drive quota).
         Generate once locally with: python scripts/generate_drive_token.py
      2. SERVICE_ACCOUNT_JSON — service account key (only works with Shared Drives).

    Service accounts have no personal Drive storage quota, so user OAuth
    is the correct approach for uploading to a regular My Drive folder.
    """
    if config.DRIVE_TOKEN_JSON:
        logger.info("Drive: authenticating with user OAuth token (DRIVE_TOKEN_JSON).")
        from google.auth.transport.requests import Request as AuthRequest
        token_data = json.loads(config.DRIVE_TOKEN_JSON)
        creds = Credentials.from_authorized_user_info(token_data, DRIVE_SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(AuthRequest())
            logger.info("Drive token refreshed.")
        return build("drive", "v3", credentials=creds)

    if config.SERVICE_ACCOUNT_JSON:
        logger.warning(
            "Drive: using service account — this only works with Shared Drives. "
            "Set DRIVE_TOKEN_JSON for regular My Drive uploads."
        )
        from google.oauth2 import service_account
        sa_info = json.loads(config.SERVICE_ACCOUNT_JSON)
        creds   = service_account.Credentials.from_service_account_info(
            sa_info, scopes=DRIVE_SCOPES
        )
        return build("drive", "v3", credentials=creds)

    raise EnvironmentError(
        "Headless Drive auth requires either DRIVE_TOKEN_JSON (user OAuth) "
        "or SERVICE_ACCOUNT_JSON (service account for Shared Drives). "
        "Run: python scripts/generate_drive_token.py to create DRIVE_TOKEN_JSON."
    )


# ─── Public API ───────────────────────────────────────────────────────────────

def upload_file(
    local_path: str,
    folder_id: str,
    client_secret_path: str,
    cred_path: str,
    mime_type: str = "text/csv",
) -> str:
    """
    Upload a local file to a Google Drive folder.

    Local:     OAuth2 (interactive on first run, token cached after).
    Cloud Run: Service Account (headless, no browser needed).

    Returns the uploaded file name.
    """
    if config.IS_HEADLESS:
        service = _get_drive_service_cloud()
    else:
        service = _get_drive_service_local(client_secret_path, cred_path)

    file_name     = os.path.basename(local_path)
    file_metadata = {"name": file_name, "parents": [folder_id]}
    media         = MediaFileUpload(local_path, mimetype=mime_type, resumable=True)

    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name",
    ).execute()

    logger.info(f"Uploaded to Drive: {uploaded.get('name')} (id={uploaded.get('id')})")
    return uploaded.get("name", file_name)
