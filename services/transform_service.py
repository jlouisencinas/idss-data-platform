"""
services/transform_service.py
------------------------------
DATA LAYER — Data transformation service.

Cleans, deduplicates, and structures raw PDF rows
into a well-typed pandas DataFrame ready for export.
"""

import re
from decimal import Decimal, InvalidOperation

import pandas as pd

from core.logger import get_logger

logger = get_logger(__name__)

# Columns produced after transformation
REPORT_COLUMNS = [
    "UNIT", "AGENT CODE", "AGENT NAME",
    "CC", "APE", "MTD CC", "MTD APE", "YTD APE",
    "LAPSES", "NAP", "YTD CC", "NET CC",
]

# Header / summary keywords to skip
_SKIP_KEYWORDS = ["NAME", "DAILY", "MONTH-TO-DATE", "PRU LIFE UK", "SUMMARY"]
_SKIP_PREFIXES = ("BM:", "DM:", "UM:", "AM:", "AUM", "RM:")
_SKIP_ROWS     = [["AGENT CODE", "AGENT NAME", "CC"]]


def _clean_numbers(values: list) -> list:
    """Convert raw string tokens to Decimal strings, skipping non-numeric."""
    result = []
    for val in values:
        parts = re.split(r"\s+", val) if isinstance(val, str) else [val]
        for part in parts:
            try:
                result.append(str(Decimal(str(part).replace(",", ""))))
            except (InvalidOperation, ValueError):
                continue
    return result


def _is_header_row(row: list) -> bool:
    """Return True if this row is a header / summary that should be skipped."""
    if len(row) < 2 or not row[1].strip():
        return True
    col1_upper = row[1].upper()
    if any(kw in col1_upper for kw in _SKIP_KEYWORDS):
        return True
    if any(row[1].startswith(p) for p in _SKIP_PREFIXES):
        return True
    if row[:3] == ["AGENT CODE", "AGENT NAME", "CC"]:
        return True
    return False


def _parse_agent_row(row: list) -> list | None:
    """
    Parse one raw row into [agent_code, agent_name, *numeric_values].
    Returns None if the row should be dropped.
    """
    if _is_header_row(row):
        return None

    # Try to split "CODE NAME" merged in col 1
    match = re.match(r"(\d{7,})\s+(.*)", row[1])
    if match:
        code, name = match.group(1), match.group(2).strip()
    elif len(row) > 2 and re.fullmatch(r"\d{7,}", row[0]):
        code, name = row[0], row[1]
    else:
        return None

    # NOTE: agents whose name ends with "*" are DELISTED. We intentionally keep
    # them (marker preserved on the name) so the Apps Script can detect and remove
    # their row from Database 2026. They are no longer dropped here.

    numeric = _clean_numbers(row[2:])
    cleaned = [code, name] + numeric

    # Pad / truncate to 11 fields (code + name + 9 numeric)
    if len(cleaned) < 11:
        cleaned += [""] * (11 - len(cleaned))
    else:
        cleaned = cleaned[:11]

    # Ensure last field is not empty
    if cleaned[-1] == "":
        cleaned[-1] = cleaned[-2]

    return cleaned


def build_dataframe(table_rows: list, orphan_rows: list, report_date: str) -> pd.DataFrame:
    """
    Combine table and orphan rows, clean them, deduplicate,
    and return a typed DataFrame.
    """
    all_rows = table_rows + orphan_rows
    cleaned = [r for row in all_rows if (r := _parse_agent_row(row)) is not None]

    numeric_cols = ["CC", "APE", "MTD CC", "MTD APE", "YTD APE", "LAPSES", "NAP", "YTD CC", "NET CC"]

    df = pd.DataFrame(cleaned, columns=["AGENT CODE", "AGENT NAME"] + numeric_cols)
    df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce").fillna(0)

    # ── Delisted (*) agents ──────────────────────────────────────────────────
    # Record which codes are delisted, then strip the "*" so grouping is clean.
    # The marker is re-applied after grouping so it survives the longest-name
    # selection (a longer non-"*" name variant would otherwise drop it).
    name_stripped  = df["AGENT NAME"].str.strip()
    delisted_codes = set(df.loc[name_stripped.str.endswith("*"), "AGENT CODE"])
    df["AGENT NAME"] = name_stripped.str.replace(r"\s*\*+\s*$", "", regex=True).str.strip()
    if delisted_codes:
        logger.info(f"Delisted (*) agents detected: {sorted(delisted_codes)}")

    # Deduplicate exact numeric duplicates (same agent + same values)
    df = df.drop_duplicates(subset=["AGENT CODE"] + numeric_cols)

    # Group by agent code — keep longest name, sum numerics
    df["_name_len"] = df["AGENT NAME"].str.len()
    df = df.sort_values("_name_len", ascending=False)
    df = df.groupby("AGENT CODE", as_index=False).agg(
        {"AGENT NAME": "first", **{c: "sum" for c in numeric_cols}}
    )
    df.drop(columns=["_name_len"], errors="ignore", inplace=True)

    # Re-apply the "*" marker to delisted agents (post-grouping, guaranteed)
    if delisted_codes:
        mask = df["AGENT CODE"].isin(delisted_codes)
        df.loc[mask, "AGENT NAME"] = df.loc[mask, "AGENT NAME"].str.rstrip() + "*"

    df.insert(0, "UNIT", "UNKNOWN")
    df["REPORT_DATE"] = report_date
    df = df.sort_values("AGENT CODE").reset_index(drop=True)

    logger.info(f"Transformed {len(df)} agent records for {report_date}")
    return df


def finalize_consolidated(df: pd.DataFrame) -> pd.DataFrame:
    """
    Post-process the concatenated DataFrame:
    - Re-deduplicate across files
    - Date becomes the COLUMN HEADER (e.g. "April 23, 2026"), values under it are blank
    - Two spacer columns precede the date header, matching the Google Sheets layout:
        ... NET CC | (blank) | (blank) | April 23, 2026
    """
    numeric_cols = ["CC", "APE", "MTD CC", "MTD APE", "YTD APE", "LAPSES", "NAP", "YTD CC", "NET CC"]

    # Drop UNIT before groupby (string column not in the agg spec)
    df = df.drop(columns=["UNIT"], errors="ignore")

    # Preserve delisted "*" markers across the cross-file grouping too
    name_stripped  = df["AGENT NAME"].str.strip()
    delisted_codes = set(df.loc[name_stripped.str.endswith("*"), "AGENT CODE"])
    df["AGENT NAME"] = name_stripped.str.replace(r"\s*\*+\s*$", "", regex=True).str.strip()

    df["_name_len"] = df["AGENT NAME"].str.len()
    df = df.sort_values("_name_len", ascending=False)
    df = df.groupby(["AGENT CODE", "REPORT_DATE"], as_index=False).agg(
        {"AGENT NAME": "first", **{c: "sum" for c in numeric_cols}}
    )
    df.drop(columns=["_name_len"], errors="ignore", inplace=True)

    if delisted_codes:
        mask = df["AGENT CODE"].isin(delisted_codes)
        df.loc[mask, "AGENT NAME"] = df.loc[mask, "AGENT NAME"].str.rstrip() + "*"

    unique_dates = df["REPORT_DATE"].unique()
    if len(unique_dates) != 1:
        raise ValueError(f"Expected exactly one REPORT_DATE; got: {list(unique_dates)}")

    # The date becomes the column header; values beneath it are left blank
    report_date_str = unique_dates[0]
    df[report_date_str] = ""
    df.drop(columns=["REPORT_DATE"], inplace=True)

    # Re-insert UNIT at front, add spacer columns before the date header
    df.insert(0, "UNIT", "UNKNOWN")
    df[""] = ""
    df["  "] = ""

    return df[[
        "UNIT", "AGENT CODE", "AGENT NAME",
        "CC", "APE", "MTD CC", "MTD APE", "YTD APE",
        "LAPSES", "NAP", "YTD CC", "NET CC",
        "", "  ", report_date_str,
    ]]
