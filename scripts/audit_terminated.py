"""
scripts/audit_terminated.py
---------------------------
STANDALONE PRISM termination audit — NOT part of the daily pipeline.

Purpose: catch agents in Database 2026 that are already TERMINATED in PRISM
but never showed a "*" in a report. Walks every agent code, looks each up in
PRISM, and records its STATUS in a new TERMINATED_AUDIT sheet.

Design for ~675 agents:
  • Logs into PRISM ONCE, reuses the session (re-logs in only on timeout)
  • Writes each result to the sheet immediately (incremental, crash-safe)
  • Resumable: on start, skips agent codes already in TERMINATED_AUDIT
  • --limit lets you process in batches; just re-run to continue
  • --recon dumps all fields for a few agents so you can confirm the
    exact STATUS label/value BEFORE committing to the full ~2-hour run

Usage:
  py scripts/audit_terminated.py --recon 5     # find the STATUS label (writes nothing)
  py scripts/audit_terminated.py               # full audit (resumable)
  py scripts/audit_terminated.py --limit 150   # do up to 150 new agents this run
  py scripts/audit_terminated.py --headful     # watch the browser

Prereqs (local .env or env vars):
  SPREADSHEET_ID, SERVICE_ACCOUNT_JSON, PRISM_USERNAME, PRISM_PASSWORD,
  PRISM_OTP_TOKEN_JSON  — and:  py -m playwright install chromium
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

from core import config
from core.logger import get_logger
from connectors.gmail_connector import get_otp_gmail_service
from connectors.sheets_connector import read_range, append_rows, ensure_sheet
from services.prism_service import (
    PrismPage, _do_login, AgentNotFoundInPrismError, SessionExpiredError,
)

logger = get_logger("audit-terminated")

AUDIT_SHEET = "TERMINATED_AUDIT"
HEADERS     = ["AGENT CODE", "AGENT NAME", "STATUS", "TERMINATED", "CHECKED_AT"]
# Status values (uppercase substring) that count as terminated/delisted:
TERMINATED_KEYWORDS = ("TERMINAT", "RESIGN", "CANCEL")


def load_db_agents(sid):
    """Return [(agent_code, agent_name), ...] from Database 2026."""
    rows = read_range(sid, "Database 2026!A:Z")
    if not rows:
        return []
    hdr = rows[0]
    ci = hdr.index("AGENT CODE") if "AGENT CODE" in hdr else 6  # col G fallback
    ni = hdr.index("AGENT NAME") if "AGENT NAME" in hdr else 2
    out = []
    for r in rows[1:]:
        code = str(r[ci]).strip() if len(r) > ci else ""
        name = str(r[ni]).strip() if len(r) > ni else ""
        if code:
            out.append((code, name))
    return out


def load_done_codes(sid):
    """Codes already in TERMINATED_AUDIT (for resume)."""
    rows = read_range(sid, f"{AUDIT_SHEET}!A:A")
    if not rows:
        return set()
    return {str(r[0]).strip() for r in rows[1:] if r and str(r[0]).strip()}


def pick_status(fields: dict):
    """Find the field whose label contains STATUS. Returns (label, value)."""
    for k, v in fields.items():
        if "STATUS" in k.upper():
            return k, v
    return None, ""


def record(sid, code, name, status, terminated):
    append_rows(sid, f"{AUDIT_SHEET}!A:E", [[
        code, name, status, "TRUE" if terminated else "FALSE",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ]])


def main():
    ap = argparse.ArgumentParser(description="PRISM termination audit")
    ap.add_argument("--recon", type=int, default=0, help="Dump fields for N agents, write nothing")
    ap.add_argument("--limit", type=int, default=0, help="Process up to N new agents this run")
    ap.add_argument("--headful", action="store_true", help="Show the browser window")
    args = ap.parse_args()

    sid = config.SPREADSHEET_ID
    if not (sid and config.PRISM_USERNAME and config.PRISM_PASSWORD):
        logger.error("Missing SPREADSHEET_ID / PRISM_USERNAME / PRISM_PASSWORD.")
        sys.exit(1)

    agents = load_db_agents(sid)
    logger.info(f"Database agents: {len(agents)}")

    if not args.recon:
        ensure_sheet(sid, AUDIT_SHEET, HEADERS)
        done = load_done_codes(sid)
        if done:
            agents = [(c, n) for (c, n) in agents if c not in done]
            logger.info(f"Skipping {len(done)} already-checked; {len(agents)} remaining.")
        if args.limit:
            agents = agents[: args.limit]
            logger.info(f"Limited to {len(agents)} this run.")

    if not agents:
        logger.info("Nothing to do.")
        return

    otp_service = get_otp_gmail_service()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headful)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        prism = PrismPage(ctx.new_page())

        if not _do_login(prism, config.PRISM_USERNAME, config.PRISM_PASSWORD, otp_service):
            logger.error("PRISM login failed — aborting.")
            return
        time.sleep(3)
        prism.dismiss_overlay()

        recon_left = args.recon
        terminated = checked = notfound = errors = 0

        for i, (code, name) in enumerate(agents, 1):
            try:
                if not prism.is_logged_in():
                    logger.warning("Session expired — re-logging in...")
                    if not _do_login(prism, config.PRISM_USERNAME, config.PRISM_PASSWORD, otp_service):
                        logger.error("Re-login failed — stopping (re-run to resume).")
                        break
                    time.sleep(2)
                    prism.dismiss_overlay()

                prism.go_to_agent_information()
                try:
                    prism.search_agent(code)
                except AgentNotFoundInPrismError:
                    if not args.recon:
                        record(sid, code, name, "NOT_FOUND", False)
                    notfound += 1
                    logger.info(f"[{i}/{len(agents)}] {code} {name} → NOT_FOUND")
                    continue

                if not prism.has_search_result(code):
                    if not args.recon:
                        record(sid, code, name, "NOT_FOUND", False)
                    notfound += 1
                    logger.info(f"[{i}/{len(agents)}] {code} {name} → NOT_FOUND")
                    continue

                if args.recon:
                    # Recon: show both the results-grid status AND the full detail fields
                    grid_status = prism.get_result_status(code)
                    prism.click_agent_result(code)
                    fields = prism.extract_all_fields()
                    logger.info(f"[RECON {args.recon - recon_left + 1}] {code} {name} — "
                                f"results-grid STATUS={grid_status!r}; detail fields:")
                    for k, v in fields.items():
                        logger.info(f"      {k!r}: {v!r}")
                    recon_left -= 1
                    if recon_left <= 0:
                        logger.info("Recon complete — confirm the STATUS label, then run full audit.")
                        break
                    continue

                # Fast path: read STATUS straight from the results grid (no detail click)
                status = prism.get_result_status(code)
                if not status:
                    # Fallback: open the details page and read STATUS there
                    prism.click_agent_result(code)
                    _, status = pick_status(prism.extract_all_fields())
                status = (status or "UNKNOWN").strip()
                is_term = any(kw in status.upper() for kw in TERMINATED_KEYWORDS)
                record(sid, code, name, status, is_term)
                checked += 1
                if is_term:
                    terminated += 1
                logger.info(
                    f"[{i}/{len(agents)}] {code} {name} → {status}"
                    + ("  *** TERMINATED ***" if is_term else "")
                )
                time.sleep(0.5)  # be gentle on PRISM

            except SessionExpiredError:
                logger.warning(f"Session expired at {code} — will re-login next loop.")
                continue
            except Exception as e:
                errors += 1
                logger.error(f"{code} ({name}): {e}")
                if not args.recon:
                    record(sid, code, name, f"ERROR: {str(e)[:120]}", False)
                continue

        if not args.recon:
            logger.info("=" * 50)
            logger.info(f"  Checked     : {checked}")
            logger.info(f"  TERMINATED  : {terminated}")
            logger.info(f"  Not found   : {notfound}")
            logger.info(f"  Errors      : {errors}")
            logger.info(f"  Results in sheet: {AUDIT_SHEET}")
            logger.info("=" * 50)


if __name__ == "__main__":
    main()
