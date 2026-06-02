"""
connectors/sheets_connector.py
--------------------------------
CLOUD LAYER — Google Sheets API v4 integration.

Auth strategy:
  Local / CI → Service Account from SERVICE_ACCOUNT_JSON env var.
               The target spreadsheet must be shared with the
               service account email address (Editor access).

Usage:
  from connectors.sheets_connector import (
      get_pending_agents,
      find_agent_row,
      update_agent_prism_data,
      mark_pending_agent_done,
  )
"""

import json

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2 import service_account

from core import config
from core.logger import get_logger

logger = get_logger(__name__)

SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


# ─── Auth ─────────────────────────────────────────────────────────────────────

def _get_sheets_service():
    """
    Build a Google Sheets API v4 service client using the service account.

    The SERVICE_ACCOUNT_JSON environment variable must contain the full
    JSON contents of the service account key file.

    ⚠️  The target Google Spreadsheet must be shared with the service
        account email (find it in the JSON under "client_email") with
        Editor access — otherwise all writes will return 403.
    """
    if not config.SERVICE_ACCOUNT_JSON:
        raise EnvironmentError(
            "SERVICE_ACCOUNT_JSON is required for Sheets access. "
            "Set it as a GitHub Secret or in your local .env file."
        )

    sa_info = json.loads(config.SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=SHEETS_SCOPES
    )
    return build("sheets", "v4", credentials=creds)


# ─── Low-level helpers ────────────────────────────────────────────────────────

def read_range(spreadsheet_id: str, range_name: str) -> list:
    """
    Read a range from a Google Sheet.

    Args:
        spreadsheet_id: The spreadsheet ID from its URL.
        range_name:     A1 notation, e.g. "Database 2026!A:K".

    Returns:
        List of rows (each row is a list of cell values as strings).
        Empty list if no data found.
    """
    try:
        service = _get_sheets_service()
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=spreadsheet_id, range=range_name)
            .execute()
        )
        return result.get("values", [])
    except HttpError as e:
        logger.error(f"Sheets read error [{range_name}]: {e}")
        return []


def read_cell(spreadsheet_id: str, range_name: str, unformatted: bool = False):
    """
    Read a single cell value.

    Args:
        spreadsheet_id: The spreadsheet ID.
        range_name:     A1 notation for one cell, e.g. "CLEANED_RAW!O1".
        unformatted:    When True, returns the raw value (e.g. a date serial
                        number) instead of the display-formatted string.

    Returns:
        The cell value (str | int | float), or None if empty / on error.
    """
    try:
        service = _get_sheets_service()
        result = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueRenderOption="UNFORMATTED_VALUE" if unformatted else "FORMATTED_VALUE",
            )
            .execute()
        )
        vals = result.get("values", [])
        if vals and vals[0]:
            return vals[0][0]
        return None
    except HttpError as e:
        logger.error(f"Sheets read error [{range_name}]: {e}")
        return None


def batch_update(spreadsheet_id: str, updates: list) -> bool:
    """
    Batch-update multiple cell ranges in a single API call.

    Args:
        spreadsheet_id: The spreadsheet ID.
        updates: List of dicts:  {"range": "Sheet!A1", "values": [["value"]]}

    Returns:
        True on success, False on error.
    """
    if not updates:
        return True

    try:
        service = _get_sheets_service()
        body = {
            "valueInputOption": "USER_ENTERED",
            "data": [
                {"range": u["range"], "values": u["values"]}
                for u in updates
            ],
        }
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id, body=body
        ).execute()
        return True
    except HttpError as e:
        logger.error(f"Sheets batch update error: {e}")
        return False


# ─── Domain helpers ───────────────────────────────────────────────────────────

def get_pending_agents(spreadsheet_id: str) -> list[dict]:
    """
    Read PENDING_PRISM_UPDATE and return all agents with STATUS = "PENDING".

    Returns:
        List of dicts:
          {
            "agent_code": str,
            "agent_name": str,
            "sheet_row":  int,   # 1-indexed row number in the sheet
          }
    """
    rows = read_range(spreadsheet_id, "PENDING_PRISM_UPDATE!A:C")
    if not rows:
        logger.info("PENDING_PRISM_UPDATE sheet is empty or does not exist.")
        return []

    pending = []
    for i, row in enumerate(rows[1:], start=2):   # skip header row, rows are 1-indexed
        if not row:
            continue
        agent_code = str(row[0]).strip() if len(row) > 0 else ""
        agent_name = str(row[1]).strip() if len(row) > 1 else ""
        status     = str(row[2]).strip() if len(row) > 2 else "PENDING"

        if not agent_code:
            continue

        if status == "PENDING":
            pending.append({
                "agent_code": agent_code,
                "agent_name": agent_name,
                "sheet_row":  i,
            })

    logger.info(f"Found {len(pending)} pending agent(s) in PENDING_PRISM_UPDATE.")
    return pending


def find_agent_row(spreadsheet_id: str, agent_code: str) -> int | None:
    """
    Find the row number in 'Database 2026' where column G matches agent_code.

    Returns:
        1-indexed row number, or None if not found.
    """
    rows = read_range(spreadsheet_id, "Database 2026!G:G")
    for i, row in enumerate(rows, start=1):
        if row and str(row[0]).strip() == str(agent_code).strip():
            return i
    logger.warning(f"Agent code {agent_code} not found in Database 2026 column G.")
    return None


def update_agent_prism_data(
    spreadsheet_id: str,
    db_row: int,
    data: dict,
) -> bool:
    """
    Write the 5 Prism fields for one agent into 'Database 2026'.

    Database 2026 column mapping:
      B  — BRANCH          (replaces FOR_DB_UPDATE)
      D  — UM NAME         (from Prism: MANAGER)
      F  — RECRUITER NAME  (from Prism: RECRUITER)
      J  — DATE APPOINTED  (from Prism: APPOINTMENT DATE)
      K  — BIRTHDATE       (from Prism: DATE OF BIRTH)

    Args:
        spreadsheet_id: The spreadsheet ID.
        db_row:         The 1-indexed row in Database 2026.
        data: {
          "branch":          str,
          "um_name":         str,
          "recruiter_name":  str,
          "date_appointed":  str,
          "birthdate":       str,
        }

    Returns:
        True on success, False on error.
    """
    updates = [
        {
            "range":  f"Database 2026!B{db_row}",
            "values": [[data.get("branch", "")]],
        },
        {
            "range":  f"Database 2026!D{db_row}",
            "values": [[data.get("um_name", "")]],
        },
        {
            "range":  f"Database 2026!F{db_row}",
            "values": [[data.get("recruiter_name", "")]],
        },
        {
            "range":  f"Database 2026!J{db_row}",
            "values": [[data.get("date_appointed", "")]],
        },
        {
            "range":  f"Database 2026!K{db_row}",
            "values": [[data.get("birthdate", "")]],
        },
    ]

    success = batch_update(spreadsheet_id, updates)
    if success:
        logger.info(
            f"Updated Database 2026 row {db_row}: "
            f"branch={data.get('branch')}, "
            f"um={data.get('um_name')}, "
            f"recruiter={data.get('recruiter_name')}, "
            f"appointed={data.get('date_appointed')}, "
            f"dob={data.get('birthdate')}"
        )
    return success


def mark_pending_agent_done(
    spreadsheet_id: str,
    sheet_row: int,
    status: str = "DONE",
) -> bool:
    """
    Update STATUS column in PENDING_PRISM_UPDATE for one agent.

    Args:
        spreadsheet_id: The spreadsheet ID.
        sheet_row:      1-indexed row number in PENDING_PRISM_UPDATE.
        status:         "DONE", "NOT_FOUND", or "ERROR".

    Returns:
        True on success, False on error.
    """
    return batch_update(
        spreadsheet_id,
        [{"range": f"PENDING_PRISM_UPDATE!C{sheet_row}", "values": [[status]]}],
    )
