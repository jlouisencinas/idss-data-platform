"""
scripts/delete_terminated.py
----------------------------
Read TERMINATED_AUDIT, find agents flagged TERMINATED, and delete their
entire row from Database 2026. Matches by AGENT CODE (falls back to AGENT NAME).

Defaults to a DRY RUN — it lists exactly which Database rows it would delete.
Re-run with --commit to actually delete them.

Usage:
  py scripts/delete_terminated.py            # dry run (deletes nothing)
  py scripts/delete_terminated.py --commit   # delete the matched rows
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config
from core.logger import get_logger
from connectors.sheets_connector import read_range, delete_rows

logger = get_logger("delete-terminated")

AUDIT_SHEET = "TERMINATED_AUDIT"
DB_SHEET    = "Database 2026"


def norm(s):
    return str(s or "").strip()


def norm_name(s):
    """Uppercase, drop trailing '*' and surrounding spaces — for name matching."""
    n = norm(s).upper()
    while n.endswith("*"):
        n = n[:-1].strip()
    return n


def main():
    ap = argparse.ArgumentParser(description="Delete terminated agents from Database 2026")
    ap.add_argument("--commit", action="store_true", help="Actually delete (default: dry run)")
    args = ap.parse_args()

    sid = config.SPREADSHEET_ID
    if not sid:
        logger.error("SPREADSHEET_ID not configured.")
        sys.exit(1)

    print(f"{'COMMIT' if args.commit else 'DRY RUN'} mode\n")

    # ── 1. Read TERMINATED_AUDIT → collect terminated codes/names ──────────────
    audit = read_range(sid, f"{AUDIT_SHEET}!A:E")
    if not audit or len(audit) < 2:
        logger.info(f"{AUDIT_SHEET} is empty — nothing to do.")
        return

    # Columns: A=AGENT CODE, B=AGENT NAME, C=STATUS, D=TERMINATED, E=CHECKED_AT
    term_codes, term_names = set(), set()
    for r in audit[1:]:
        code   = norm(r[0]) if len(r) > 0 else ""
        name   = norm(r[1]) if len(r) > 1 else ""
        status = norm(r[2]).upper() if len(r) > 2 else ""
        flag   = norm(r[3]).upper() if len(r) > 3 else ""
        if flag == "TRUE" or "TERMINAT" in status:
            if code:
                term_codes.add(code)
            if name:
                term_names.add(norm_name(name))

    logger.info(f"Terminated agents in audit: {len(term_codes)} code(s).")
    if not term_codes and not term_names:
        logger.info("No terminated agents flagged — nothing to delete.")
        return

    # ── 2. Read Database 2026 → find matching rows ─────────────────────────────
    db = read_range(sid, f"{DB_SHEET}!A:Z")
    if not db:
        logger.error(f"{DB_SHEET} is empty or unreadable.")
        return

    hdr = db[0]
    ci = hdr.index("AGENT CODE") if "AGENT CODE" in hdr else 6   # col G fallback
    ni = hdr.index("AGENT NAME") if "AGENT NAME" in hdr else 2

    to_delete = []  # (row_number_1indexed, code, name)
    for i, r in enumerate(db[1:], start=2):  # row 2 = first data row
        code  = norm(r[ci]) if len(r) > ci else ""
        name  = norm(r[ni]) if len(r) > ni else ""
        nameU = norm_name(name)
        if (code and code in term_codes) or (nameU and nameU in term_names):
            to_delete.append((i, code, name))

    if not to_delete:
        logger.info("No matching Database 2026 rows (already removed?).")
        return

    logger.info(f"{len(to_delete)} Database 2026 row(s) match terminated agents:")
    for rn, code, name in to_delete:
        logger.info(f"   row {rn}:  {code}  {name}")

    # ── 3. Delete (or not) ─────────────────────────────────────────────────────
    if not args.commit:
        logger.info("\nDRY RUN — re-run with --commit to delete these rows.")
        return

    ok = delete_rows(sid, DB_SHEET, [rn for rn, _, _ in to_delete])
    logger.info("✅ Deletion complete." if ok else "❌ Deletion failed — see error above.")


if __name__ == "__main__":
    main()
