"""
scripts/test_prism.py
----------------------
Quick local test for the PRISM agent enrichment step.

Runs run_prism_update() in VISIBLE browser mode (headless=False) so you
can watch what Playwright is doing and catch any UI issues early.

Prerequisites (all in .env):
  SPREADSHEET_ID        — your Google Sheets ID
  PRISM_USERNAME        — PRISM portal username (e.g. 70008940)
  PRISM_PASSWORD        — PRISM portal password
  PRISM_OTP_TOKEN_JSON  — OAuth2 token for plukfloroespiritu Gmail
  SERVICE_ACCOUNT_JSON  — service account JSON (for Sheets read/write)

Usage:
    python scripts/test_prism.py

The script will:
  1. Load your .env
  2. Check all required vars are present
  3. Read PENDING_PRISM_UPDATE from Google Sheets
  4. Open a visible Chrome window and run the full PRISM enrichment
  5. Print a summary of updated / not found / errors
"""

import os
import sys
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

# ── Check required env vars ────────────────────────────────────────────────────
REQUIRED = {
    "SPREADSHEET_ID":       "Google Sheets spreadsheet ID",
    "PRISM_USERNAME":       "PRISM portal username",
    "PRISM_PASSWORD":       "PRISM portal password",
    "PRISM_OTP_TOKEN_JSON": "OAuth2 token JSON for plukfloroespiritu Gmail",
    "SERVICE_ACCOUNT_JSON": "Service account JSON for Sheets API",
}

missing = [k for k, _ in REQUIRED.items() if not os.environ.get(k)]
if missing:
    print("\n❌  Missing required environment variables in .env:\n")
    for k in missing:
        print(f"   {k}  —  {REQUIRED[k]}")
    print("\nFill these in your .env file and retry.\n")
    sys.exit(1)

print("✓  All required env vars found.\n")

# ── Check PENDING_PRISM_UPDATE has agents ──────────────────────────────────────
from connectors.sheets_connector import get_pending_agents

spreadsheet_id = os.environ["SPREADSHEET_ID"]
print(f"Reading PENDING_PRISM_UPDATE from spreadsheet: {spreadsheet_id}\n")

pending = get_pending_agents(spreadsheet_id)

if not pending:
    print("⚠️  No PENDING agents found in PENDING_PRISM_UPDATE.")
    print()
    print("This means either:")
    print("  a) The Apps Script hasn't run yet with new agents, OR")
    print("  b) All agents have already been processed (STATUS = DONE)")
    print()
    print("To test with real data, trigger the pipeline first so the Apps Script")
    print("creates PENDING rows, then re-run this script.")
    sys.exit(0)

print(f"Found {len(pending)} pending agent(s):\n")
for a in pending:
    print(f"  • {a['agent_code']}  {a['agent_name']}  (row {a['sheet_row']})")
print()

# ── Confirm before opening browser ────────────────────────────────────────────
answer = input("Proceed with PRISM enrichment? A browser window will open. [y/N] ").strip().lower()
if answer != "y":
    print("Aborted.")
    sys.exit(0)

# ── Run PRISM update — visible browser ────────────────────────────────────────
from services.prism_service import run_prism_update

print("\nLaunching visible browser (headless=False)...\n")

result = run_prism_update(
    spreadsheet_id=spreadsheet_id,
    prism_username=os.environ["PRISM_USERNAME"],
    prism_password=os.environ["PRISM_PASSWORD"],
    headless=False,   # ← watch the browser
)

# ── Summary ────────────────────────────────────────────────────────────────────
print("\n" + "=" * 55)
print("PRISM ENRICHMENT RESULT")
print("=" * 55)

if result["updated"]:
    print(f"\n✅  Updated ({len(result['updated'])}):")
    for a in result["updated"]:
        print(f"   {a['agent_code']}  {a['agent_name']}")
        d = a.get("details", {})
        print(f"     Branch:      {d.get('branch')}")
        print(f"     UM Name:     {d.get('um_name')}")
        print(f"     Recruiter:   {d.get('recruiter_name')}")
        print(f"     Appointed:   {d.get('date_appointed')}")
        print(f"     Birthdate:   {d.get('birthdate')}")

if result["not_found"]:
    print(f"\n⚠️   Not found in Prism ({len(result['not_found'])}):")
    for a in result["not_found"]:
        print(f"   {a['agent_code']}  {a['agent_name']}")

if result["errors"]:
    print(f"\n❌  Errors ({len(result['errors'])}):")
    for a in result["errors"]:
        print(f"   {a['agent_code']}  —  {a['error']}")

print()
