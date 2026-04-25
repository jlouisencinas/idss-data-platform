"""
services/ingestion_service.py
------------------------------
DATA LAYER — Ingestion service.

Orchestrates the Gmail download flow:
fetches the latest Branch and Unit Production Report ZIPs
and saves them to local staging directories.
"""

from connectors.gmail_connector import (
    BRANCH_REGEX,
    UNIT_REGEX,
    download_latest_zips,
    fetch_latest_messages,
    get_gmail_service,
)
from core import config
from core.logger import get_logger

logger = get_logger(__name__)


def download_latest_reports() -> list:
    """
    Connect to Gmail, find the latest IDSS report emails,
    download all ZIP attachments, and return the list of
    downloaded filenames.
    """
    logger.info("Starting ingestion — connecting to Gmail...")

    service = get_gmail_service(
        credentials_path=config.CREDENTIALS_PATH,
        token_path=config.TOKEN_PATH,
        scopes=config.GMAIL_SCOPES,
    )

    # ── Branch reports ──────────────────────────────────────────────────────
    branch_ids, branch_date = fetch_latest_messages(
        service,
        BRANCH_REGEX,
        config.BRANCH_SUBJECT_QUERY,
        max_messages=config.MAX_MESSAGES,
        retries=config.DOWNLOAD_RETRIES,
    )

    if branch_ids:
        logger.info(f"Latest Branch report date: {branch_date} — downloading {len(branch_ids)} message(s)...")
        downloaded_branch = download_latest_zips(
            service, branch_ids, config.BRANCH_DIR, max_workers=config.PARALLEL_WORKERS
        )
        logger.info(f"Branch ZIPs downloaded: {downloaded_branch or 'none (already up to date)'}")
    else:
        logger.info("No Branch IDSS reports found in Gmail.")
        downloaded_branch = []

    # ── Unit reports ─────────────────────────────────────────────────────────
    unit_ids, unit_date = fetch_latest_messages(
        service,
        UNIT_REGEX,
        config.UNIT_SUBJECT_QUERY,
        max_messages=config.MAX_MESSAGES,
        retries=config.DOWNLOAD_RETRIES,
    )

    if unit_ids:
        logger.info(f"Latest Unit report date: {unit_date} — downloading {len(unit_ids)} message(s)...")
        downloaded_unit = download_latest_zips(
            service, unit_ids, config.UNIT_DIR, max_workers=config.PARALLEL_WORKERS
        )
        logger.info(f"Unit ZIPs downloaded: {downloaded_unit or 'none (already up to date)'}")
    else:
        logger.info("No Unit IDSS reports found in Gmail.")
        downloaded_unit = []

    return downloaded_branch + downloaded_unit
