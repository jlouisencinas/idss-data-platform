"""
connectors/gmail_connector.py
------------------------------
CLOUD LAYER — Gmail API integration.

Auth strategy:
  Local     → OAuth2 browser flow; tokens cached to token.json
  Cloud Run → Token loaded from GMAIL_TOKEN_JSON environment variable
              (export your local token.json content into that secret)
"""

import base64
import json
import os
import re
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from core import config
from core.logger import get_logger

logger = get_logger(__name__)

BRANCH_REGEX = re.compile(
    r"^(Branch) Production Reports as of (\d{8})(?: \((.+)\))?$", re.IGNORECASE
)
UNIT_REGEX = re.compile(
    r"^(Unit) Production Reports as of (\d{8})(?: \((.+)\))?$", re.IGNORECASE
)


# ─── Authentication ───────────────────────────────────────────────────────────

def get_gmail_service(credentials_path: str, token_path: str, scopes: list):
    """
    Authenticate with Gmail API.

    Local:      Standard OAuth2 file flow (opens browser on first run).
    Cloud Run:  Loads token from GMAIL_TOKEN_JSON env var — no browser needed.
    """
    if config.IS_CLOUD_RUN:
        return _get_gmail_service_cloud(scopes)
    return _get_gmail_service_local(credentials_path, token_path, scopes)


def _get_gmail_service_local(credentials_path: str, token_path: str, scopes: list):
    """Local: OAuth2 with cached token file."""
    creds = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, scopes)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _get_gmail_service_cloud(scopes: list):
    """
    Cloud Run: Load OAuth2 token from the GMAIL_TOKEN_JSON environment variable.

    The value should be the raw contents of your local token.json.
    Store it in Google Secret Manager and mount it as an env var in Cloud Run.
    """
    if not config.GMAIL_TOKEN_JSON:
        raise EnvironmentError(
            "Cloud Run requires GMAIL_TOKEN_JSON env var. "
            "Export the contents of your local token.json into that secret."
        )

    token_data = json.loads(config.GMAIL_TOKEN_JSON)
    creds = Credentials.from_authorized_user_info(token_data, scopes)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        logger.info("Gmail token refreshed in Cloud Run.")

    return build("gmail", "v1", credentials=creds)


# ─── Message Fetching ─────────────────────────────────────────────────────────

def fetch_latest_messages(
    service,
    subject_regex,
    query_subject: str,
    max_messages: int = 30,
    retries: int = 3,
):
    """
    Search Gmail for messages matching subject_regex.
    Returns (list_of_message_ids, latest_date_str).
    """
    for attempt in range(1, retries + 1):
        try:
            results = service.users().messages().list(
                userId="me",
                q=f"subject:{query_subject}",
                maxResults=max_messages,
            ).execute()

            messages = results.get("messages", [])
            reports = []

            for msg in messages:
                meta = service.users().messages().get(
                    userId="me",
                    id=msg["id"],
                    format="metadata",
                    metadataHeaders=["Subject"],
                ).execute()

                headers = meta["payload"].get("headers", [])
                subject = next(
                    (h["value"] for h in headers if h["name"] == "Subject"), ""
                ).strip()

                match = subject_regex.match(subject)
                if not match:
                    continue

                report_date = match.groups()[1]
                reports.append({"id": msg["id"], "date": report_date})

            if not reports:
                return [], None

            latest_date = max(r["date"] for r in reports)
            latest_ids  = [r["id"] for r in reports if r["date"] == latest_date]
            return latest_ids, latest_date

        except HttpError as e:
            logger.warning(f"Attempt {attempt} failed fetching messages: {e}")
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                logger.error("Failed to fetch messages after all retries.")
                return [], None

    return [], None


# ─── Attachment Download ──────────────────────────────────────────────────────

def _download_single_attachment(service, msg_id: str, part: dict, folder: str, retries: int = 3):
    filename = part.get("filename", "")
    if not filename.lower().endswith(".zip"):
        return None

    path = os.path.join(folder, filename)
    if os.path.exists(path):
        logger.info(f"Already downloaded, skipping: {filename}")
        return None

    attachment_id = part.get("body", {}).get("attachmentId")
    if not attachment_id:
        return None

    for attempt in range(1, retries + 1):
        try:
            attachment = service.users().messages().attachments().get(
                userId="me", messageId=msg_id, id=attachment_id
            ).execute()

            data = base64.urlsafe_b64decode(attachment["data"])
            temp_path = path + ".part"
            with open(temp_path, "wb") as f:
                f.write(data)
            os.replace(temp_path, path)
            logger.info(f"Downloaded: {filename}")
            return filename

        except HttpError as e:
            logger.warning(f"Attempt {attempt} failed for {filename}: {e}")
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                logger.error(f"Failed to download {filename} after all retries.")
                return None

    return None


def download_latest_zips(service, message_ids: list, folder: str, max_workers: int = 3) -> list:
    """Download ZIP attachments from all message IDs. Parallel per message."""
    downloaded = []

    for msg_id in message_ids:
        message = None
        for attempt in range(1, 4):
            try:
                message = service.users().messages().get(
                    userId="me", id=msg_id, format="full"
                ).execute()
                break
            except (HttpError, ssl.SSLError) as e:
                logger.warning(f"Attempt {attempt} failed for message {msg_id}: {e}")
                time.sleep(2 ** attempt)

        if not message:
            logger.error(f"Could not fetch message {msg_id}, skipping.")
            continue

        parts = message["payload"].get("parts", [])
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(_download_single_attachment, service, msg_id, part, folder)
                for part in parts
            ]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    downloaded.append(result)

    return downloaded
