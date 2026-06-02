"""
scripts/test_date_precheck.py
------------------------------
Safe test for the "skip if already processed" pre-check.

Does NOT download, process, upload, or trigger anything. It only:
  1. Unit-tests the date normalization (offline, no credentials needed)
  2. Reads the latest email report date (Gmail metadata only)
  3. Reads CLEANED_RAW!O1 (Sheets)
  4. Prints what the pipeline WOULD decide (skip vs run)

Run:
  py scripts/test_date_precheck.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timedelta


# ── Copy of the pipeline's normalizer (kept in sync with idss_pipeline.py) ─────
def to_yyyymmdd(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return (datetime(1899, 12, 30) + timedelta(days=float(v))).strftime("%Y%m%d")
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y%m%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    try:
        return (datetime(1899, 12, 30) + timedelta(days=float(s))).strftime("%Y%m%d")
    except (ValueError, TypeError):
        return None


def test_normalizer():
    print("── 1. Offline date normalization tests ──")
    cases = [
        ("May 31, 2026", "20260531"),
        ("May. 31, 2026".replace(".", ""), "20260531"),
        ("2026-05-31", "20260531"),
        ("5/31/2026", "20260531"),
        ("20260531", "20260531"),
        (46173, "20260531"),          # Sheets serial for May 31, 2026
        (46173.0, "20260531"),
        ("", None),
        (None, None),
    ]
    ok = True
    for raw, expected in cases:
        got = to_yyyymmdd(raw)
        mark = "✓" if got == expected else "✗"
        if got != expected:
            ok = False
        print(f"   {mark} {raw!r:>20} -> {got}  (expected {expected})")
    print("   PASS\n" if ok else "   *** FAIL ***\n")
    return ok


def test_comparison_logic():
    print("── 2. Skip-decision logic ──")
    cases = [
        ("20260531", "20260531", True,  "same date"),
        ("20260601", "20260531", False, "email newer"),
        ("20260530", "20260531", True,  "email older"),
        (None,       "20260531", False, "no email date"),
        ("20260601", None,       False, "empty sheet"),
    ]
    ok = True
    for email, sheet, should_skip, label in cases:
        skip = bool(email and sheet and email <= sheet)
        mark = "✓" if skip == should_skip else "✗"
        if skip != should_skip:
            ok = False
        print(f"   {mark} email={email} sheet={sheet} -> skip={skip}  ({label})")
    print("   PASS\n" if ok else "   *** FAIL ***\n")
    return ok


def test_live():
    print("── 3. Live read (Gmail + Sheets) ──")
    try:
        from core import config
        from connectors.gmail_connector import (
            BRANCH_REGEX, UNIT_REGEX, fetch_latest_messages, get_gmail_service,
        )
        from connectors.sheets_connector import read_cell
    except Exception as e:
        print(f"   ⚠️  Could not import connectors ({e}). Skipping live test.\n")
        return

    # Email date
    try:
        svc = get_gmail_service(config.CREDENTIALS_PATH, config.TOKEN_PATH, config.GMAIL_SCOPES)
        _, b = fetch_latest_messages(svc, BRANCH_REGEX, config.BRANCH_SUBJECT_QUERY, max_messages=config.MAX_MESSAGES)
        _, u = fetch_latest_messages(svc, UNIT_REGEX, config.UNIT_SUBJECT_QUERY, max_messages=config.MAX_MESSAGES)
        dates = [d for d in (b, u) if d]
        email_date = max(dates) if dates else None
        print(f"   Branch latest: {b}   Unit latest: {u}   -> email_date = {email_date}")
    except Exception as e:
        print(f"   ⚠️  Gmail read failed: {e}")
        email_date = None

    # Sheet date
    try:
        raw = read_cell(config.SPREADSHEET_ID, "CLEANED_RAW!O1", unformatted=True)
        sheet_date = to_yyyymmdd(raw)
        print(f"   CLEANED_RAW!O1 raw: {raw!r}   -> sheet_date = {sheet_date}")
    except Exception as e:
        print(f"   ⚠️  Sheets read failed: {e}")
        sheet_date = None

    # Decision
    print()
    if email_date and sheet_date and email_date <= sheet_date:
        print(f"   DECISION: SKIP  (email {email_date} <= sheet {sheet_date} — already processed)")
    else:
        print(f"   DECISION: RUN   (email {email_date} vs sheet {sheet_date})")
    print()


if __name__ == "__main__":
    a = test_normalizer()
    b = test_comparison_logic()
    test_live()
    print("=" * 50)
    print("Offline logic:", "ALL PASS ✅" if (a and b) else "FAILURES ✗")
    print("=" * 50)
