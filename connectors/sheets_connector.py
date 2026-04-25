"""
connectors/sheets_connector.py
--------------------------------
CLOUD LAYER — Google Sheets API integration (placeholder).

Future: Write consolidated report data directly into
a Google Sheet for live dashboard consumption.
"""

from core.logger import get_logger

logger = get_logger(__name__)


def write_to_sheet(spreadsheet_id: str, range_name: str, values: list) -> None:
    """
    Write a list of rows to a Google Sheet.

    Args:
        spreadsheet_id: The ID of the target spreadsheet.
        range_name:     A1 notation range, e.g. "Sheet1!A1".
        values:         List of rows (each row is a list of cell values).
    """
    # TODO: implement using google-api-python-client Sheets v4
    raise NotImplementedError("Google Sheets connector — coming in next iteration.")
