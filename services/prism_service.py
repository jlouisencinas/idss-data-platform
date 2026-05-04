"""
services/prism_service.py
--------------------------
AUTOMATION LAYER — PRISM Agent Portal browser automation.

Uses Playwright (headless Chromium) to:
  1. Log in to https://prism.prulifeuk.com.ph
  2. Retrieve the OTP from the plukfloroespiritu Gmail inbox
  3. For each agent in PENDING_PRISM_UPDATE:
       a. Search by agent code
       b. Extract 5 fields from the Agent Details page
       c. Write them back to Database 2026 via the Sheets API
       d. Mark the agent as DONE in PENDING_PRISM_UPDATE

Called by the pipeline after the Apps Script step when new agents exist.
"""

import re
import time

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from connectors.gmail_connector import get_otp_gmail_service, read_prism_otp
from connectors.sheets_connector import (
    find_agent_row,
    get_pending_agents,
    mark_pending_agent_done,
    update_agent_prism_data,
)
from core import config
from core.logger import get_logger

logger = get_logger(__name__)

PRISM_URL         = "https://prism.prulifeuk.com.ph"
NAV_TIMEOUT_MS    = 30_000   # 30s for page navigation
ACTION_TIMEOUT_MS = 15_000   # 15s for element interactions


# ─── Page interaction helpers ─────────────────────────────────────────────────

class PrismPage:
    """Thin wrapper around a Playwright page for Prism-specific actions."""

    def __init__(self, page):
        self.page = page

    # ── Login flow ────────────────────────────────────────────────────────────

    def go_to_login(self):
        self.page.goto(PRISM_URL, timeout=NAV_TIMEOUT_MS)
        self.page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
        logger.info("Navigated to Prism login page.")

    def fill_credentials(self, username: str, password: str):
        self.page.locator("input[type='text']").first.fill(username)
        self.page.locator("input[type='password']").fill(password)
        logger.info("Credentials filled.")

    def click_login(self):
        self.page.get_by_role("button", name=re.compile(r"LOG\s*IN", re.IGNORECASE)).click()
        logger.info("Clicked LOG IN.")

    def click_send_otp(self):
        """Click SUBMIT on the OTP-send modal (triggers OTP email)."""
        self.page.wait_for_selector(
            "text=PRISM requires an Email OTP",
            timeout=ACTION_TIMEOUT_MS,
        )
        self.page.get_by_role("button", name=re.compile(r"SUBMIT", re.IGNORECASE)).click()
        logger.info("Clicked SUBMIT — OTP email dispatched.")

    def fill_and_submit_otp(self, otp_code: str):
        """Enter the 6-digit OTP and submit."""
        self.page.wait_for_selector(
            "text=Please enter the 6-digit One Time Pin",
            timeout=ACTION_TIMEOUT_MS,
        )
        otp_input = self.page.locator("input[type='text']").first
        otp_input.clear()
        otp_input.fill(otp_code)
        self.page.get_by_role("button", name=re.compile(r"SUBMIT", re.IGNORECASE)).click()
        logger.info(f"OTP {otp_code} submitted.")

    def wait_for_dashboard(self):
        """Wait until the dashboard nav bar is visible (confirms successful login)."""
        self.page.wait_for_selector("text=FLORO GALAMAY ESPIRITU", timeout=NAV_TIMEOUT_MS)
        logger.info("Dashboard loaded — login successful.")

    # ── Session check ─────────────────────────────────────────────────────────

    def is_logged_in(self) -> bool:
        """Return True if the dashboard nav bar is visible."""
        try:
            return self.page.locator("text=FLORO GALAMAY ESPIRITU").is_visible(timeout=3_000)
        except PlaywrightTimeoutError:
            return False

    # ── Navigation ────────────────────────────────────────────────────────────

    def go_to_agent_information(self):
        """Navigate to Servicing → Agent Information."""
        self.page.get_by_role("link", name=re.compile(r"Servicing", re.IGNORECASE)).click()
        self.page.get_by_role("link", name=re.compile(r"Agent Information", re.IGNORECASE)).click()
        self.page.wait_for_selector("text=AGENT INFORMATION", timeout=NAV_TIMEOUT_MS)
        logger.info("Navigated to Agent Information page.")

    # ── Agent search ──────────────────────────────────────────────────────────

    def search_agent(self, agent_code: str):
        """Type agent code in the search box and click SEARCH."""
        # Ensure "Agent code" is selected in the dropdown
        search_type_select = self.page.locator("select").first
        search_type_select.select_option(label=re.compile(r"Agent code", re.IGNORECASE))

        search_input = self.page.locator("input[type='text']").first
        search_input.clear()
        search_input.fill(agent_code)
        self.page.get_by_role("button", name=re.compile(r"SEARCH", re.IGNORECASE)).click()

        # Wait for results table OR a "no results" indicator
        self.page.wait_for_selector("text=SEARCH RESULTS", timeout=NAV_TIMEOUT_MS)
        logger.info(f"Search results loaded for agent {agent_code}.")

    def has_search_result(self) -> bool:
        """Return True if at least one result row is present."""
        try:
            # The result link is the agent code in the table
            return self.page.locator("table a").first.is_visible(timeout=5_000)
        except PlaywrightTimeoutError:
            return False

    def click_agent_result(self, agent_code: str):
        """Click the agent code link in the search results table."""
        self.page.get_by_role("link", name=agent_code).first.click()
        self.page.wait_for_selector(
            "text=AGENT INFORMATION > AGENT DETAILS",
            timeout=NAV_TIMEOUT_MS,
        )
        logger.info(f"Agent Details page loaded for {agent_code}.")

    # ── Data extraction ───────────────────────────────────────────────────────

    def extract_agent_details(self) -> dict:
        """
        Extract the 5 required fields from the Agent Details page.

        Returns a dict with keys:
          branch, um_name, recruiter_name, date_appointed, birthdate
        """
        # Get full page text — most reliable for a legacy table layout
        body_text = self.page.inner_text("body")

        def _extract(pattern: str) -> str:
            m = re.search(pattern, body_text, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        data = {
            "branch":         _extract(r"BRANCH NAME\s*[:\|]\s*(.+)"),
            "um_name":        _extract(r"MANAGER\s*[:\|]\s*(.+)"),
            "recruiter_name": _extract(r"RECRUITER\s*[:\|]\s*(.+)"),
            "date_appointed": _extract(r"APPOINTMENT DATE\s*[:\|]\s*(.+)"),
            "birthdate":      _extract(r"DATE OF BIRTH\s*[:\|]\s*(.+)"),
        }

        # Clean up any trailing whitespace or label bleed
        for k, v in data.items():
            data[k] = v.split("\n")[0].strip()

        logger.info(f"Extracted details: {data}")
        return data


# ─── Login orchestration ──────────────────────────────────────────────────────

def _do_login(prism: PrismPage, username: str, password: str, otp_service) -> bool:
    """
    Full login sequence: credentials → OTP request → read OTP → submit.

    Returns True on success, False on failure.
    """
    try:
        prism.go_to_login()
        prism.fill_credentials(username, password)
        prism.click_login()
        prism.click_send_otp()

        # Give Prism a moment to send the email before polling
        time.sleep(5)

        otp_code = read_prism_otp(otp_service, max_wait_seconds=90)
        if not otp_code:
            logger.error("Could not obtain OTP — aborting login.")
            return False

        prism.fill_and_submit_otp(otp_code)
        prism.wait_for_dashboard()
        return True

    except PlaywrightTimeoutError as e:
        logger.error(f"Login timed out: {e}")
        return False
    except Exception as e:
        logger.error(f"Login failed unexpectedly: {e}")
        return False


# ─── Main entry point ─────────────────────────────────────────────────────────

def run_prism_update(
    spreadsheet_id: str,
    prism_username: str,
    prism_password: str,
    headless: bool = True,
) -> dict:
    """
    Full PRISM enrichment run for all PENDING agents.

    Steps:
      1. Read pending agents from PENDING_PRISM_UPDATE sheet
      2. Launch Playwright (headless Chromium)
      3. Log into Prism (with Gmail OTP)
      4. For each pending agent:
           a. Check session is still active (re-login if needed)
           b. Navigate to Agent Information, search by code
           c. Extract 5 fields
           d. Update Database 2026 via Sheets API
           e. Mark agent DONE in PENDING_PRISM_UPDATE
      5. Return summary report

    Args:
        spreadsheet_id:  Google Sheets spreadsheet ID.
        prism_username:  PRISM portal username.
        prism_password:  PRISM portal password.
        headless:        Run browser headlessly (True in CI, False for local debugging).

    Returns:
        {
          "updated":   [{"agent_code": ..., "agent_name": ...}, ...],
          "not_found": [{"agent_code": ..., "agent_name": ...}, ...],
          "errors":    [{"agent_code": ..., "error": ...}, ...],
        }
    """
    result = {"updated": [], "not_found": [], "errors": []}

    # ── Step 1: Check for pending agents ──────────────────────────────────────
    pending = get_pending_agents(spreadsheet_id)
    if not pending:
        logger.info("No pending agents — PRISM update skipped.")
        return result

    logger.info(f"Starting PRISM update for {len(pending)} agent(s).")

    # ── Step 2: Build OTP Gmail service ───────────────────────────────────────
    try:
        otp_service = get_otp_gmail_service()
    except Exception as e:
        logger.error(f"Cannot build OTP Gmail service: {e}")
        for agent in pending:
            result["errors"].append({
                "agent_code": agent["agent_code"],
                "error": "OTP Gmail service unavailable",
            })
        return result

    # ── Step 3: Launch Playwright ──────────────────────────────────────────────
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page    = context.new_page()
        prism   = PrismPage(page)
        logged_in = False

        try:
            # ── Step 4: Initial login ──────────────────────────────────────────
            logged_in = _do_login(prism, prism_username, prism_password, otp_service)
            if not logged_in:
                for agent in pending:
                    result["errors"].append({
                        "agent_code": agent["agent_code"],
                        "error": "Prism login failed",
                    })
                return result

            # ── Step 5: Process each agent ────────────────────────────────────
            for agent in pending:
                agent_code = agent["agent_code"]
                agent_name = agent["agent_name"]
                sheet_row  = agent["sheet_row"]

                logger.info(f"Processing agent {agent_code} ({agent_name})...")

                try:
                    # 5a: Re-login if session expired
                    if not prism.is_logged_in():
                        logger.warning("Session expired — re-logging in...")
                        logged_in = _do_login(
                            prism, prism_username, prism_password, otp_service
                        )
                        if not logged_in:
                            result["errors"].append({
                                "agent_code": agent_code,
                                "error": "Re-login failed after session timeout",
                            })
                            continue

                    # 5b: Navigate and search
                    prism.go_to_agent_information()
                    prism.search_agent(agent_code)

                    if not prism.has_search_result():
                        logger.warning(f"Agent {agent_code} not found in Prism.")
                        mark_pending_agent_done(spreadsheet_id, sheet_row, "NOT_FOUND")
                        result["not_found"].append({
                            "agent_code": agent_code,
                            "agent_name": agent_name,
                        })
                        continue

                    # 5c: Open details and extract
                    prism.click_agent_result(agent_code)
                    details = prism.extract_agent_details()

                    # 5d: Find row in Database 2026 and write
                    db_row = find_agent_row(spreadsheet_id, agent_code)
                    if db_row is None:
                        logger.error(
                            f"Agent {agent_code} missing from Database 2026 — "
                            "Apps Script may not have run yet."
                        )
                        mark_pending_agent_done(spreadsheet_id, sheet_row, "ERROR")
                        result["errors"].append({
                            "agent_code": agent_code,
                            "error": "Not in Database 2026",
                        })
                        continue

                    update_agent_prism_data(spreadsheet_id, db_row, details)

                    # 5e: Mark done
                    mark_pending_agent_done(spreadsheet_id, sheet_row, "DONE")
                    result["updated"].append({
                        "agent_code": agent_code,
                        "agent_name": agent_name,
                        "details":    details,
                    })
                    logger.info(f"✓ Agent {agent_code} ({agent_name}) updated successfully.")

                except PlaywrightTimeoutError as e:
                    logger.error(f"Timeout processing agent {agent_code}: {e}")
                    mark_pending_agent_done(spreadsheet_id, sheet_row, "ERROR")
                    result["errors"].append({
                        "agent_code": agent_code,
                        "error": f"Page timeout: {e}",
                    })
                except Exception as e:
                    logger.error(f"Unexpected error for agent {agent_code}: {e}")
                    mark_pending_agent_done(spreadsheet_id, sheet_row, "ERROR")
                    result["errors"].append({
                        "agent_code": agent_code,
                        "error": str(e),
                    })

        finally:
            context.close()
            browser.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info(
        f"PRISM update complete — "
        f"updated: {len(result['updated'])}, "
        f"not found: {len(result['not_found'])}, "
        f"errors: {len(result['errors'])}"
    )
    return result
