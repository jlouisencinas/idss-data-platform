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


def append_rows(spreadsheet_id: str, range_name: str, rows: list) -> bool:
    """
    Append rows to the bottom of a sheet (values().append).

    Args:
        spreadsheet_id: The spreadsheet ID.
        range_name:     A1 notation, e.g. "TERMINATED_AUDIT!A:E".
        rows:           List of row lists to append.

    Returns:
        True on success, False on error.
    """
    if not rows:
        return True
    try:
        service = _get_sheets_service()
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
        return True
    except HttpError as e:
        logger.error(f"Sheets append error [{range_name}]: {e}")
        return False


def ensure_sheet(spreadsheet_id: str, title: str, headers: list = None) -> None:
    """
    Create a sheet/tab if it doesn't exist, optionally writing a header row.
    No-op if the sheet already exists.
    """
    try:
        service = _get_sheets_service()
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        existing = [s["properties"]["title"] for s in meta.get("sheets", [])]
        if title in existing:
            return
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        ).execute()
        if headers:
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"{title}!A1",
                valueInputOption="USER_ENTERED",
                body={"values": [headers]},
            ).execute()
        logger.info(f"Created sheet '{title}'.")
    except HttpError as e:
        logger.error(f"Sheets ensure_sheet error [{title}]: {e}")


def get_sheet_id(spreadsheet_id: str, title: str):
    """Return the numeric sheetId (gid) for a tab title, or None if not found."""
    try:
        service = _get_sheets_service()
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for s in meta.get("sheets", []):
            if s["properties"]["title"] == title:
                return s["properties"]["sheetId"]
    except HttpError as e:
        logger.error(f"get_sheet_id error [{title}]: {e}")
    return None


def delete_rows(spreadsheet_id: str, sheet_title: str, row_numbers_1indexed: list) -> bool:
    """
    Delete entire rows from a sheet by their 1-indexed row numbers.

    Rows are deleted bottom-to-top so indices don't shift mid-operation.

    Args:
        spreadsheet_id:       The spreadsheet ID.
        sheet_title:          Tab name, e.g. "Database 2026".
        row_numbers_1indexed: List of 1-indexed sheet row numbers to delete.

    Returns:
        True on success, False on error.
    """
    if not row_numbers_1indexed:
        return True

    sheet_id = get_sheet_id(spreadsheet_id, sheet_title)
    if sheet_id is None:
        logger.error(f"delete_rows: sheet '{sheet_title}' not found.")
        return False

    requests = []
    for rn in sorted(set(row_numbers_1indexed), reverse=True):
        start = rn - 1  # API is 0-indexed, end exclusive
        requests.append({
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": start,
                    "endIndex": start + 1,
                }
            }
        })

    try:
        service = _get_sheets_service()
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests}
        ).execute()
        logger.info(f"Deleted {len(requests)} row(s) from '{sheet_title}'.")
        return True
    except HttpError as e:
        logger.error(f"delete_rows error [{sheet_title}]: {e}")
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
