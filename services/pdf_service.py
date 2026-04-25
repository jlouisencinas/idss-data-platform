"""
services/pdf_service.py
------------------------
DATA LAYER — PDF extraction service.

Extracts text, tables, and orphan rows from
IDSS Branch Production Report PDFs using pdfplumber.
"""

import re
from datetime import datetime

import pdfplumber

from core.logger import get_logger

logger = get_logger(__name__)


def extract_report_date(pdf_path: str) -> str:
    """
    Read the report date from the PDF text.
    Falls back to today's date if not found.

    Returns a date string in 'Month DD, YYYY' format.
    """
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            match = re.search(r"Production Report as of (\d{2}/\d{2}/\d{4})", text)
            if match:
                return datetime.strptime(match.group(1), "%m/%d/%Y").strftime("%B %d, %Y")

    logger.warning(f"Could not find report date in {pdf_path}; defaulting to today.")
    return datetime.today().strftime("%B %d, %Y")


def extract_raw_rows(pdf_path: str) -> tuple:
    """
    Extract all table rows and orphan (non-table) rows from a PDF.

    Returns:
        (table_rows, orphan_rows) — both as lists of string-lists.
    """
    table_rows = []
    orphan_rows = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):

            # ── Table extraction ─────────────────────────────────────────────
            for table in page.extract_tables():
                for row in table:
                    if any(cell for cell in row):
                        table_rows.append([cell.strip() if cell else "" for cell in row])

            # ── Orphan row extraction (last 6 lines of page text) ────────────
            text = page.extract_text() or ""
            lines = text.split("\n")[-6:]

            for line in lines:
                if not isinstance(line, str):
                    continue
                parts = line.strip().split()
                if len(parts) < 12:
                    continue
                if not re.fullmatch(r"\d{7,}", parts[0]):
                    continue
                num_candidates = [p for p in parts if re.match(r"^-?\d[\d,]*\.?\d*$", p)]
                if len(num_candidates) < 8:
                    continue

                numeric_parts = parts[-10:]
                name = " ".join(parts[1:-10])
                orphan_rows.append([parts[0], name] + numeric_parts)
                logger.debug(f"Page {page_num} orphan row captured: agent={parts[0]}")

    return table_rows, orphan_rows
