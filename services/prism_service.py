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
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

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


class SessionExpiredError(Exception):
    """Raised when PRISM shows a Session Timeout dialog mid-processing."""


# ─── Field-format helpers ─────────────────────────────────────────────────────

# Branch-name keyword → short code stored in the Google Sheet.
_BRANCH_KEYWORDS = (
    ("KEYSTONE", "LKL"),
    ("ESPERA",   "ESPERA"),
    ("REIGN",    "REIGN"),
    ("POLARIS",  "POLARIS"),
)


def _derive_branch_keyword(branch_full: str) -> str:
    """
    PRISM shows the full branch string (e.g. '00335 - LAZURITE KEYSTONE LIFE INS.
    AGENCY INC.'). The Google Sheet stores a short code derived from a keyword:
      contains 'KEYSTONE' → 'LKL'
      contains 'ESPERA'   → 'ESPERA'
      contains 'REIGN'    → 'REIGN'
      contains 'POLARIS'  → 'POLARIS'
    Falls back to the original string if no keyword matches.
    """
    if not branch_full:
        return ""
    upper = branch_full.upper()
    for needle, code in _BRANCH_KEYWORDS:
        if needle in upper:
            return code
    return branch_full.strip()


def _format_prism_date(raw: str) -> str:
    """
    Convert PRISM's 'DD-MMM-YYYY' (e.g. '30-APR-2026') to the Google Sheet's
    'M/D/YYYY' (e.g. '4/30/2026'). Returns the original string unchanged if
    the format isn't recognised, so we never silently drop data.
    """
    if not raw:
        return ""
    s = raw.strip()
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return f"{dt.month}/{dt.day}/{dt.year}"
        except ValueError:
            continue
    return s


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
        # Dismiss "MULTIPLE USERS" dialog if it appears (previous session still active).
        # Ext JS uses <a role="button"> not <button>, so we use JS click.
        try:
            self.page.wait_for_selector("text=MULTIPLE USERS", timeout=5_000)
            # JS-click the OK button inside the dialog
            self.page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll(
                        'a[role="button"], button, a.x-btn'
                    );
                    for (const btn of buttons) {
                        if (btn.textContent.trim().toUpperCase() === 'OK') {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }
            """)
            logger.info("Dismissed MULTIPLE USERS dialog.")
        except PlaywrightTimeoutError:
            pass  # dialog did not appear — normal flow

    def click_send_otp(self):
        """
        Request OTP email, handling two possible dialogs:
          A) Normal:   'PRISM requires an Email OTP' → click SUBMIT (sends new OTP)
          B) Throttled:'You still have an active login Email OTP request!'
                       → click OK (OTP already sent, skip requesting a new one)

        Returns True if a new OTP was requested, False if an existing one is reused.
        """
        # Wait for whichever dialog appears first (Playwright OR syntax)
        otp_request_dialog  = self.page.locator("text=PRISM requires an Email OTP")
        active_otp_dialog   = self.page.locator("text=active login Email OTP")
        otp_request_dialog.or_(active_otp_dialog).wait_for(
            state="visible", timeout=ACTION_TIMEOUT_MS
        )

        def _js_click_button(label_upper: str):
            return self.page.evaluate(f"""
                () => {{
                    const buttons = document.querySelectorAll(
                        'a[role="button"], button, a.x-btn'
                    );
                    for (const btn of buttons) {{
                        if (btn.textContent.trim().toUpperCase() === '{label_upper}') {{
                            btn.click();
                            return true;
                        }}
                    }}
                    return false;
                }}
            """)

        if active_otp_dialog.is_visible(timeout=2_000):
            # Already has an active OTP — dismiss and reuse it
            _js_click_button("OK")
            logger.info("Active OTP already exists — reusing existing email OTP.")
            return False
        else:
            # Normal flow — request a new OTP
            _js_click_button("SUBMIT")
            logger.info("Clicked SUBMIT — OTP email dispatched.")
            return True

    def fill_and_submit_otp(self, otp_code: str):
        """Enter the 6-digit OTP and submit."""
        # Wait directly for the OTP input field by its known ID
        # (more reliable than text matching in Ext JS apps)
        self.page.wait_for_selector("#OTPtextfield-inputEl", timeout=NAV_TIMEOUT_MS)


        # Target the OTP field by its known ID (confirmed from live page inspection)
        # Falls back to the name attribute, then placeholder text
        otp_input = None
        for selector in [
            "#OTPtextfield-inputEl",
            "input[name='OTPtextfield-inputEl']",
            "input[placeholder*='pin' i]",
            "input[placeholder*='otp' i]",
        ]:
            loc = self.page.locator(selector).first
            try:
                if loc.is_visible(timeout=1_000):
                    otp_input = loc
                    logger.info(f"Using OTP input selector: {selector}")
                    break
            except PlaywrightTimeoutError:
                continue

        if otp_input is None:
            raise RuntimeError("Could not find OTP input field on page.")

        otp_input.click()
        otp_input.fill("")         # clear any existing content
        otp_input.type(otp_code, delay=80)   # type character by character
        logger.info(f"Typed OTP: {otp_code}")

        time.sleep(0.5)
        # Use JS click — Ext JS uses <a role="button">, not <button>
        self.page.evaluate("""
            () => {
                const buttons = document.querySelectorAll(
                    'a[role="button"], button, a.x-btn'
                );
                for (const btn of buttons) {
                    if (btn.textContent.trim().toUpperCase() === 'SUBMIT') {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        logger.info(f"OTP {otp_code} submitted.")

    def wait_for_dashboard(self):
        """
        Wait until the dashboard is loaded after OTP submission.
        Uses URL change as the primary signal (most reliable for Ext JS apps),
        then waits for the Servicing nav link to confirm the UI is ready.
        """
        # Wait for URL to move away from the login/OTP pages
        self.page.wait_for_function(
            "() => !window.location.href.includes('login') && "
            "      !window.location.href.includes('otp') && "
            "      !window.location.href.includes('authenticate')",
            timeout=NAV_TIMEOUT_MS,
        )
        # Wait for Servicing nav link — Ext JS can take several seconds to render
        self.page.wait_for_selector(
            "a:has-text('Servicing'), a:has-text('SERVICING')",
            timeout=NAV_TIMEOUT_MS,
        )
        current_url = self.page.url
        logger.info(f"Dashboard loaded — login successful. URL: {current_url}")

    # ── Session check ─────────────────────────────────────────────────────────

    def is_logged_in(self) -> bool:
        """
        Return True if the dashboard is active.
        Checks URL (fast) then the Servicing nav link (confirms UI is ready).
        """
        try:
            url = self.page.url
            if "login" in url or "otp" in url or "authenticate" in url:
                return False
            return self.page.locator(
                "a:has-text('Servicing'), a:has-text('SERVICING')"
            ).first.is_visible(timeout=8_000)
        except PlaywrightTimeoutError:
            return False

    # ── Overlay / dialog cleanup ──────────────────────────────────────────────

    def dismiss_overlay(self):
        """
        Dismiss any visible Ext JS modal/window overlay.

        Checks ALL visible x-layer and x-window elements (not just customPanel)
        so it catches MULTIPLE USERS, Session Timeout, system messages, etc.

        Raises SessionExpiredError if a Session Timeout dialog is detected —
        the caller must re-login before continuing.
        """
        try:
            result = self.page.evaluate("""
                () => {
                    const layers = document.querySelectorAll(
                        '.x-layer, .x-window, .x-panel'
                    );
                    for (const layer of layers) {
                        // Skip invisible elements
                        if (!layer.offsetParent) continue;
                        const style = getComputedStyle(layer);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;

                        const text = layer.textContent || '';
                        const isTimeout = text.includes('Session Timeout') ||
                                          text.includes('session timeout');

                        // Find OK or CLOSE button inside this layer
                        const buttons = layer.querySelectorAll(
                            'a[role="button"], button, a.x-btn'
                        );
                        for (const btn of buttons) {
                            const t = btn.textContent.trim().toUpperCase();
                            if (t === 'OK' || t === 'CLOSE') {
                                btn.click();
                                return { clicked: true, isTimeout: isTimeout };
                            }
                        }
                    }
                    return { clicked: false, isTimeout: false };
                }
            """)

            if result["isTimeout"]:
                logger.warning("Session Timeout dialog detected — re-login required.")
                raise SessionExpiredError("Session Timeout")

            if result["clicked"]:
                logger.info("Dismissed overlay (JS OK/CLOSE click).")
                time.sleep(0.3)

        except SessionExpiredError:
            raise
        except Exception as e:
            logger.warning(f"dismiss_overlay error (non-fatal): {e}")

    # ── Navigation ────────────────────────────────────────────────────────────

    def go_to_agent_information(self):
        """
        Navigate to Servicing → Agent Information.

        Ext JS dropdown menu items are in the DOM but hidden until the menu
        opens — Playwright's visibility wait never resolves.  We use JS click
        to bypass visibility checks after opening the menu.
        """
        self.dismiss_overlay()
        # Open the Servicing dropdown
        self.page.locator("a:has-text('Servicing')").first.click()
        time.sleep(0.5)   # let Ext JS render the dropdown

        # JS-click the "Agent information" menu item (bypasses hidden state)
        clicked = self.page.evaluate("""
            () => {
                const links = document.querySelectorAll('a[role="menuitem"]');
                for (const link of links) {
                    if (link.textContent.trim().toLowerCase()
                            .includes('agent information')) {
                        link.click();
                        return true;
                    }
                }
                return false;
            }
        """)
        if not clicked:
            raise RuntimeError("Could not find 'Agent information' in Servicing dropdown.")

        self.page.wait_for_selector("text=AGENT INFORMATION", timeout=NAV_TIMEOUT_MS)
        logger.info("Navigated to Agent Information page.")

    # ── Agent search ──────────────────────────────────────────────────────────

    def search_agent(self, agent_code: str):
        """
        Select 'Agent Code' in the Ext JS ComboBox, fill in the agent code,
        and click SEARCH.

        Key insight (learned from live testing):
        - combo.select(rec) / setRawValue() updates Ext JS internals but does
          NOT fire PRISM's own change handlers, so the filter is silently
          ignored and PRISM shows the default SUMMARY view instead.
        - The only reliable approach: open the dropdown via its trigger button
          (force=True to bypass aria-hidden), wait for the bound list to be
          visible, then Playwright-click the "Agent code" item natively.
          This fires all mouse events that PRISM's handlers are listening to.
        """
        self.dismiss_overlay()

        # ── Step 1: Wait for the search form to fully render ─────────────────
        # After navigate to Agent Information, Ext JS may still be rendering.
        # Wait until at least one text input with a non-zero bounding box
        # exists on the page before trying to locate the combo.
        try:
            self.page.wait_for_function("""
                () => {
                    const inputs = document.querySelectorAll('input[type="text"], input:not([type])');
                    for (const inp of inputs) {
                        const r = inp.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) return true;
                    }
                    return false;
                }
            """, timeout=10_000)
        except PlaywrightTimeoutError:
            pass  # continue and let the selector search handle it

        # ── Step 2: Open the combo dropdown via Ext.expand() ────────────────
        # Strategy:
        #   - Sort all 'combobox[name=searchBy]' candidates so the visible,
        #     rendered one is first (the page also has a stale hidden one).
        #   - Call cmp.expand() — this opens the picker AND, because the cmp
        #     is the visible one with a real rect, Ext anchors the picker
        #     directly under the trigger (not at (0,0) like before).
        #   - The item click further down is a real Playwright mouse.click at
        #     the item's actual coordinates inside cmp.picker.el.dom — that's
        #     the real DOM mouse event PRISM's filter handler listens for.
        get_visible_cmp_js = """
            (() => {
                if (typeof Ext === 'undefined') return null;
                const cmps = Ext.ComponentQuery.query('combobox[name=searchBy]') || [];
                if (!cmps.length) return null;
                const sorted = cmps.slice().sort((a, b) => {
                    const av = (a.rendered ? 1 : 0) + (a.isVisible && a.isVisible() ? 2 : 0);
                    const bv = (b.rendered ? 1 : 0) + (b.isVisible && b.isVisible() ? 2 : 0);
                    return bv - av;
                });
                return sorted[0];
            })()
        """

        expanded = self.page.evaluate(f"""
            () => {{
                try {{
                    const cmp = {get_visible_cmp_js};
                    if (!cmp) return {{err: 'no-cmp'}};
                    cmp.expand();
                    const r = cmp.triggerWrap && cmp.triggerWrap.dom &&
                              cmp.triggerWrap.dom.getBoundingClientRect();
                    return {{
                        ok: true,
                        rendered: !!cmp.rendered,
                        visible: cmp.isVisible ? cmp.isVisible() : null,
                        cmpRect: r ? {{l: r.left, t: r.top, w: r.width, h: r.height}} : null,
                    }};
                }} catch(e) {{ return {{err: 'ex:' + e.message}}; }}
            }}
        """)
        logger.info(f"Combo expand: {expanded}")
        if not expanded or expanded.get('err'):
            raise RuntimeError(f"Cannot expand combo: {expanded}")

        # Wait for THIS combo's picker to be visible with a non-zero rect
        picker_visible_check = f"""
            () => {{
                try {{
                    const cmp = {get_visible_cmp_js};
                    if (!cmp || !cmp.picker || !cmp.picker.el) return false;
                    if (cmp.picker.isVisible && !cmp.picker.isVisible()) return false;
                    const r = cmp.picker.el.dom.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                }} catch(e) {{ return false; }}
            }}
        """
        try:
            self.page.wait_for_function(picker_visible_check, timeout=5_000)
        except PlaywrightTimeoutError:
            # Diagnostic: log what the picker actually looks like
            picker_diag = self.page.evaluate(f"""
                () => {{
                    try {{
                        const cmp = {get_visible_cmp_js};
                        if (!cmp) return {{err: 'no-cmp'}};
                        if (!cmp.picker) return {{err: 'no-picker', isExpanded: cmp.isExpanded}};
                        const el = cmp.picker.el && cmp.picker.el.dom;
                        const r = el ? el.getBoundingClientRect() : null;
                        return {{
                            isExpanded: cmp.isExpanded,
                            pickerVisible: cmp.picker.isVisible ? cmp.picker.isVisible() : null,
                            rect: r ? {{l: r.left, t: r.top, w: r.width, h: r.height}} : null,
                            offsetParent: el ? !!el.offsetParent : null,
                        }};
                    }} catch(e) {{ return {{err: 'ex:' + e.message}}; }}
                }}
            """)
            logger.error(f"Picker did not become visible. Diag: {picker_diag}")
            raise RuntimeError(f"Picker not visible after expand: {picker_diag}")

        time.sleep(0.3)

        # ── Step 3: Mouse-click "Agent code" inside cmp.picker.el.dom ───────
        # Retry up to 3 times — Ext's bound list sometimes hasn't fully bound
        # its click handlers in the brief moment after the picker is "visible".
        find_item_js = f"""
            () => {{
                try {{
                    const cmp = {get_visible_cmp_js};
                    if (!cmp || !cmp.picker || !cmp.picker.el) return null;
                    const items = cmp.picker.el.dom.querySelectorAll(
                        '.x-boundlist-item, .x-combo-list-item, li'
                    );
                    for (const item of items) {{
                        const t = (item.textContent || '').trim().toLowerCase();
                        if (t.includes('agent') && t.includes('code')) {{
                            const r = item.getBoundingClientRect();
                            return {{
                                x: r.left + r.width / 2,
                                y: r.top + r.height / 2,
                                text: item.textContent.trim(),
                                rect: {{l: r.left, t: r.top, w: r.width, h: r.height}},
                            }};
                        }}
                    }}
                    return null;
                }} catch(e) {{ return null; }}
            }}
        """
        read_state_js = f"""
            () => {{
                try {{
                    const cmp = {get_visible_cmp_js};
                    if (!cmp) return {{err: 'no-cmp'}};
                    return {{
                        rawValue:     cmp.getRawValue ? cmp.getRawValue() : null,
                        value:        cmp.getValue ? cmp.getValue() : null,
                        inputDisplay: cmp.inputEl ? cmp.inputEl.dom.value : null,
                    }};
                }} catch(e) {{ return {{err: 'ex:' + e.message}}; }}
            }}
        """

        combo_state = None
        for attempt in range(3):
            agent_code_item_pos = self.page.evaluate(find_item_js)
            if not agent_code_item_pos:
                # Picker may have closed — re-expand and try once more
                logger.warning(
                    f"Attempt {attempt + 1}: 'Agent code' item not found — re-expanding picker."
                )
                self.page.evaluate(
                    f"() => {{ const cmp = {get_visible_cmp_js}; if (cmp) cmp.expand(); }}"
                )
                self.page.wait_for_function(picker_visible_check, timeout=3_000)
                time.sleep(0.3)
                continue

            if attempt == 0:
                logger.info(f"Agent code item rect: {agent_code_item_pos.get('rect')}")

            self.page.mouse.click(agent_code_item_pos['x'], agent_code_item_pos['y'])
            logger.info(
                f"Attempt {attempt + 1}: mouse-clicked '{agent_code_item_pos['text']}' "
                f"at ({agent_code_item_pos['x']:.0f}, {agent_code_item_pos['y']:.0f})"
            )
            time.sleep(0.6)   # let PRISM's change handler run

            combo_state = self.page.evaluate(read_state_js)
            if combo_state and combo_state.get('rawValue'):
                logger.info(f"searchBy combo state: {combo_state}")
                break

            logger.warning(
                f"Attempt {attempt + 1}: combo did not register "
                f"({combo_state}) — re-expanding and retrying."
            )
            # Re-expand for the next attempt
            self.page.evaluate(
                f"() => {{ const cmp = {get_visible_cmp_js}; if (cmp) cmp.expand(); }}"
            )
            try:
                self.page.wait_for_function(picker_visible_check, timeout=3_000)
            except PlaywrightTimeoutError:
                pass
            time.sleep(0.3)

        if not combo_state or combo_state.get('err') or not combo_state.get('rawValue'):
            raise RuntimeError(f"searchBy combo did not register selection after 3 attempts: {combo_state}")

        # ── Step 4: Set keyword via Ext JS API ───────────────────────────────
        # The keyword field is readonly in the DOM (Ext JS manages it), so
        # we use Ext.getCmp().setValue() which bypasses the readonly attribute.
        kw_result = self.page.evaluate(f"""
            () => {{
                try {{
                    if (typeof Ext === 'undefined') return 'no-ext';
                    const searchEl = document.querySelector(
                        'input[placeholder="Enter search keyword"]'
                    );
                    if (!searchEl) return 'no-el';
                    const field = Ext.getCmp(searchEl.getAttribute('data-componentid'));
                    if (!field) return 'no-cmp';
                    field.setValue('{agent_code}');
                    return field.getValue();
                }} catch(e) {{ return 'error:' + e.message; }}
            }}
        """)
        logger.info(f"Keyword set: {kw_result}")
        time.sleep(0.2)

        # ── Step 4: Click SEARCH ──────────────────────────────────────────────
        # btn.el.dom.click() mirrors a real user click, firing DOM listeners.
        btn_result = self.page.evaluate("""
            () => {
                try {
                    if (typeof Ext !== 'undefined') {
                        for (const btn of Ext.ComponentQuery.query('button')) {
                            if ((btn.text || '').toUpperCase() === 'SEARCH') {
                                if (btn.el && btn.el.dom) {
                                    btn.el.dom.click();
                                    return 'ext-dom-click';
                                }
                                btn.fireEvent('click', btn);
                                return 'ext-fireEvent';
                            }
                        }
                    }
                } catch(e) {}
                for (const el of document.querySelectorAll(
                    'a[role="button"], button, a.x-btn'
                )) {
                    if (el.textContent.trim().toUpperCase() === 'SEARCH') {
                        el.click();
                        return 'dom-click';
                    }
                }
                return 'not-found';
            }
        """)
        logger.info(f"SEARCH button click: {btn_result}")
        time.sleep(0.5)   # let PRISM's AJAX request start

        # ── Step 5: Wait for SEARCH RESULTS ──────────────────────────────────
        # innerText only reflects visible text, so this correctly waits for
        # the results panel to appear (not just exist hidden in the DOM).
        self.page.wait_for_function(
            "() => document.body.innerText.includes('SEARCH RESULTS')",
            timeout=NAV_TIMEOUT_MS,
        )
        # Extra pause for Ext JS grid to finish rendering rows via AJAX.
        time.sleep(1.5)

        # Debug: log row count so we can confirm the filter was applied
        row_count = self.page.evaluate(
            "() => document.querySelectorAll('table tr').length"
        )
        logger.info(f"Search results loaded for agent {agent_code}. "
                    f"Table rows in DOM: {row_count}")

    def has_search_result(self, agent_code: str = "") -> bool:
        """
        Return True only if the specific agent_code appears as a link
        in the search results table.

        With an 'Agent code' filter, PRISM returns either the exact match
        or shows all agents when the code doesn't exist.  We must verify
        the specific code is present, not just that any table row exists.
        """
        if not agent_code:
            # Legacy fallback: any table link
            try:
                return self.page.locator("table a").first.is_visible(timeout=5_000)
            except PlaywrightTimeoutError:
                return False

        # Check whether the exact agent code appears as a link in the results.
        # Also log all visible link texts in the table (first 20) for debugging.
        result = self.page.evaluate(f"""
            () => {{
                const links = Array.from(document.querySelectorAll('table a, td a'));
                const texts = links.slice(0, 20).map(a => a.textContent.trim());
                let found = false;
                for (const a of links) {{
                    if (a.textContent.trim() === '{agent_code}') {{
                        found = true;
                        break;
                    }}
                }}
                return {{ found: found, sample: texts }};
            }}
        """)
        logger.info(f"has_search_result({agent_code}): found={result['found']} "
                    f"| sample links: {result['sample']}")
        return bool(result["found"])

    def click_agent_result(self, agent_code: str):
        """
        Click the agent code link in the search results table.

        With a proper 'Agent code' filter the results table shows one row whose
        first cell is an <a> link with the code as its text.  We JS-click it to
        bypass any Ext JS grid visibility constraints.
        """
        self.dismiss_overlay()

        clicked = self.page.evaluate(f"""
            () => {{
                // Look for any <a> tag whose trimmed text exactly matches the code
                const links = document.querySelectorAll('a');
                for (const a of links) {{
                    if (a.textContent.trim() === '{agent_code}') {{
                        a.click();
                        return true;
                    }}
                }}
                // Fallback: any element (td, span) whose text is the code
                const cells = document.querySelectorAll('td');
                for (const td of cells) {{
                    if (td.textContent.trim() === '{agent_code}') {{
                        td.click();
                        return true;
                    }}
                }}
                return false;
            }}
        """)

        if not clicked:
            # Final fallback — Playwright locator
            logger.warning(f"JS click for {agent_code} failed — trying Playwright locator.")
            self.page.locator(f"a:has-text('{agent_code}')").first.click(timeout=ACTION_TIMEOUT_MS)

        # Wait for Agent Details page
        self.page.wait_for_function(
            """() => {
                const t = document.body.innerText || '';
                return t.toUpperCase().includes('AGENT DETAILS') ||
                       t.toUpperCase().includes('AGENT DETAIL');
            }""",
            timeout=NAV_TIMEOUT_MS,
        )
        logger.info(f"Agent Details page loaded for {agent_code}.")

    # ── Data extraction ───────────────────────────────────────────────────────

    def extract_agent_details(self) -> dict:
        """
        Extract the 5 required fields from the Agent Details page.

        Pull values directly from Ext form components (fieldLabel + getRawValue),
        with a DOM fallback that pairs each .x-form-item-label with the adjacent
        value element. The previous regex approach failed because PRISM renders
        labels and values in separate columns — inner_text serialised all labels
        first, so '.+' after a label captured the *next label*, not the value.

        Returns a dict with keys:
          branch, um_name, recruiter_name, date_appointed, birthdate
        """
        # label-keyword (uppercase) → output key. Order matters: more-specific
        # patterns must come before generic ones (e.g. UNIT MANAGER before MANAGER).
        label_map = [
            ("BRANCH NAME",         "branch"),
            ("BRANCH",              "branch"),
            ("UNIT MANAGER",        "um_name"),
            ("UM NAME",             "um_name"),
            ("MANAGER",             "um_name"),
            ("RECRUITER NAME",      "recruiter_name"),
            ("RECRUITED BY",        "recruiter_name"),
            ("RECRUITER",           "recruiter_name"),
            ("APPOINTMENT DATE",    "date_appointed"),
            ("DATE OF APPOINTMENT", "date_appointed"),
            ("DATE APPOINTED",      "date_appointed"),
            ("DATE OF BIRTH",       "birthdate"),
            ("BIRTH DATE",          "birthdate"),
            ("BIRTHDATE",           "birthdate"),
            ("DOB",                 "birthdate"),
        ]

        # Wait for inputs to actually be populated (form loads via AJAX after the
        # AGENT DETAILS heading appears).
        try:
            self.page.wait_for_function(
                """() => {
                    const inps = document.querySelectorAll('input[type="text"]');
                    let withVal = 0;
                    inps.forEach(i => { if (i.value && i.value.trim()) withVal++; });
                    return withVal >= 5;
                }""",
                timeout=10_000,
            )
        except PlaywrightTimeoutError:
            pass

        result, diag = self.page.evaluate(
            """
            (labelMap) => {
                const norm = s => (s || '').toString()
                    .toUpperCase()
                    .replace(/[:\\u00A0]/g, '')
                    .replace(/\\s+/g, ' ')
                    .trim();
                const matchKey = lbl => {
                    const n = norm(lbl);
                    if (!n) return null;
                    for (const [needle, key] of labelMap) {
                        if (n === needle || n.startsWith(needle + ' ') || n.endsWith(' ' + needle)) return key;
                    }
                    for (const [needle, key] of labelMap) {
                        if (n.includes(needle)) return key;
                    }
                    return null;
                };

                const out = {};
                const diag = {extFields: [], pairs: []};

                // 1) Ext component path (kept for completeness; was empty last run)
                if (typeof Ext !== 'undefined') {
                    try {
                        const fields = Ext.ComponentQuery.query(
                            'displayfield, textfield, datefield, field, [fieldLabel]'
                        );
                        for (const f of fields) {
                            const lbl = f.fieldLabel || '';
                            let val = '';
                            try {
                                val = f.getRawValue ? f.getRawValue() : (f.getValue ? f.getValue() : '');
                            } catch(e) {}
                            val = (val == null ? '' : String(val)).trim();
                            if (lbl) diag.extFields.push({label: lbl, value: val, name: f.name || '', xtype: f.xtype || ''});
                            const key = matchKey(lbl);
                            if (key && val && !out[key]) out[key] = val;
                        }
                    } catch(e) { diag.extErr = e.message; }
                }

                // 2) DOM input/label pairing — for each <input> with a value,
                //    find the nearest preceding text-with-colon as its label.
                const findLabelFor = (inp) => {
                    // (a) <label for="id">
                    if (inp.id) {
                        const l = document.querySelector('label[for="' + CSS.escape(inp.id) + '"]');
                        if (l) {
                            const t = (l.textContent || '').trim();
                            if (t) return t;
                        }
                    }
                    // (b) Walk up to a row/cell wrapper, then look for a sibling/child
                    //     element whose text ends with ':' (or is short uppercase text)
                    let cur = inp;
                    for (let depth = 0; depth < 6; depth++) {
                        // Check previous siblings
                        let sib = cur.previousElementSibling;
                        while (sib) {
                            const t = (sib.textContent || '').trim();
                            if (t && t.length < 80 && /:\\s*$/.test(t)) return t;
                            if (t && t.length < 80 && /^[A-Z][A-Z\\s/]+:?$/.test(t)) return t;
                            sib = sib.previousElementSibling;
                        }
                        if (!cur.parentElement) break;
                        cur = cur.parentElement;
                    }
                    // (c) closest TR/table row — first cell text
                    const tr = inp.closest('tr');
                    if (tr) {
                        const firstCell = tr.querySelector('td, th');
                        if (firstCell && !firstCell.contains(inp)) {
                            const t = (firstCell.textContent || '').trim();
                            if (t) return t;
                        }
                    }
                    return '';
                };

                const inputs = document.querySelectorAll(
                    'input[type="text"], input:not([type]), textarea'
                );
                inputs.forEach(inp => {
                    const val = (inp.value || '').trim();
                    if (!val) return;
                    const lbl = findLabelFor(inp);
                    diag.pairs.push({label: lbl, value: val, name: inp.name || '', id: inp.id || ''});
                    const key = matchKey(lbl);
                    if (key && !out[key]) out[key] = val;
                });

                return [out, diag];
            }
            """,
            label_map,
        )

        # Log a one-line summary of what we found and what's missing.
        missing = [k for k in ("branch","um_name","recruiter_name","date_appointed","birthdate") if not result.get(k)]
        if missing:
            logger.warning(f"Missing fields {missing}.")
            logger.warning(f"Ext fields ({len(diag.get('extFields', []))}): {diag.get('extFields', [])[:30]}")
            logger.warning(f"DOM pairs ({len(diag.get('pairs', []))}): {diag.get('pairs', [])[:40]}")

        # Ensure all keys are present (empty string if nothing found)
        for k in ("branch","um_name","recruiter_name","date_appointed","birthdate"):
            result.setdefault(k, "")

        # Post-process: branch keyword + date format → match the Google Sheet schema
        result["branch"]         = _derive_branch_keyword(result["branch"])
        result["date_appointed"] = _format_prism_date(result["date_appointed"])
        result["birthdate"]      = _format_prism_date(result["birthdate"])

        logger.info(f"Extracted details: {result}")
        return result


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

        # After LOG IN + possible MULTIPLE USERS dismissal, check if PRISM
        # already landed us on the dashboard (no OTP needed in that case)
        if prism.is_logged_in():
            logger.info("Dashboard reached directly after login — OTP not required.")
            return True

        # Normal OTP flow
        otp_request_time_ms = int(time.time() * 1000)
        new_otp_requested = prism.click_send_otp()

        # Only wait for email delivery if we just triggered a new OTP
        if new_otp_requested:
            time.sleep(5)

        otp_code = read_prism_otp(
            otp_service,
            max_wait_seconds=90,
            poll_start_ms=otp_request_time_ms,
        )
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

            # Brief pause after login — Ext JS dashboard takes a moment to render
            time.sleep(3)
            prism.dismiss_overlay()  # clear any post-login modal before starting

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
                        time.sleep(2)
                        prism.dismiss_overlay()

                    # 5b: Navigate and search
                    prism.go_to_agent_information()
                    prism.search_agent(agent_code)

                    if not prism.has_search_result(agent_code):
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

                except SessionExpiredError:
                    # Session timed out mid-agent — re-login and retry this agent
                    logger.warning(f"Session expired mid-processing {agent_code} — re-logging in...")
                    logged_in = _do_login(prism, prism_username, prism_password, otp_service)
                    if not logged_in:
                        mark_pending_agent_done(spreadsheet_id, sheet_row, "ERROR")
                        result["errors"].append({
                            "agent_code": agent_code,
                            "error": "Re-login failed after session timeout",
                        })
                        continue
                    # Retry this agent once after re-login
                    try:
                        time.sleep(2)
                        prism.dismiss_overlay()
                        prism.go_to_agent_information()
                        prism.search_agent(agent_code)
                        if not prism.has_search_result(agent_code):
                            mark_pending_agent_done(spreadsheet_id, sheet_row, "NOT_FOUND")
                            result["not_found"].append({"agent_code": agent_code, "agent_name": agent_name})
                            continue
                        prism.click_agent_result(agent_code)
                        details = prism.extract_agent_details()
                        db_row = find_agent_row(spreadsheet_id, agent_code)
                        if db_row:
                            update_agent_prism_data(spreadsheet_id, db_row, details)
                            mark_pending_agent_done(spreadsheet_id, sheet_row, "DONE")
                            result["updated"].append({"agent_code": agent_code, "agent_name": agent_name, "details": details})
                            logger.info(f"✓ Agent {agent_code} updated after re-login retry.")
                        else:
                            mark_pending_agent_done(spreadsheet_id, sheet_row, "ERROR")
                            result["errors"].append({"agent_code": agent_code, "error": "Not in Database 2026"})
                    except Exception as retry_err:
                        mark_pending_agent_done(spreadsheet_id, sheet_row, "ERROR")
                        result["errors"].append({"agent_code": agent_code, "error": f"Retry failed: {retry_err}"})

                except PlaywrightTimeoutError as e:
                    logger.error(f"Timeout processing agent {agent_code}: {e}")
                    mark_pending_agent_done(spreadsheet_id, sheet_row, "ERROR")
                    result["errors"].append({
                        "agent_code": agent_code,
                        "error": f"Page timeout: {e}",
                    })
                except PlaywrightError as e:
                    # Covers "Target page, context or browser has been closed"
                    # and other Playwright runtime errors.  If the browser is gone
                    # there is no point processing any more agents — break out.
                    err_msg = str(e).splitlines()[0]  # first line is enough
                    logger.error(f"Browser/page error for {agent_code}: {err_msg}")
                    mark_pending_agent_done(spreadsheet_id, sheet_row, "ERROR")
                    result["errors"].append({
                        "agent_code": agent_code,
                        "error": err_msg,
                    })
                    if "closed" in err_msg.lower():
                        logger.error("Browser context is gone — aborting remaining agents.")
                        # Mark all remaining agents as errored
                        remaining = [
                            a for a in pending
                            if a["agent_code"] not in
                            {r["agent_code"] for r in result["updated"] + result["not_found"] + result["errors"]}
                            and a["agent_code"] != agent_code
                        ]
                        for rem in remaining:
                            mark_pending_agent_done(spreadsheet_id, rem["sheet_row"], "ERROR")
                            result["errors"].append({
                                "agent_code": rem["agent_code"],
                                "error": "Aborted — browser closed",
                            })
                        break
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
