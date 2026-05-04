# PRISM Agent Update Workflow
## Instructions for Claude in Chrome

Use this prompt whenever new agents need to be updated from PRISM after the pipeline has run.
Copy everything below the divider and paste it into a new Claude conversation.

---

## HOW TO TRIGGER

After you run the GitHub pipeline and the Apps Script finishes:
1. Check if the **PENDING_PRISM_UPDATE** tab exists in the Google Sheet and has rows with STATUS = "PENDING"
2. If yes, open a new Claude conversation and paste the prompt below

---

## PROMPT TO PASTE INTO CLAUDE

---

I need you to update new agent details in my Google Sheet by fetching their information from the PRISM portal. Here is the exact workflow to follow:

### What's open in my browser
- **Google Sheets** tab: LKL_DATABASE_2026 (spreadsheet with "Database 2026" and "PENDING_PRISM_UPDATE" sheets)
- **Gmail** tab: the **plukfloroespiritu** Gmail account — this is the inbox that receives PRISM OTP emails (different from my personal Gmail). Look for the tab with this account open, not jlouisencinas@gmail.com.
- You will need to open: **https://prism.prulifeuk.com.ph**

---

### STEP 1 — Read the pending agent list

1. Switch to the Google Sheets tab
2. Click on the **PENDING_PRISM_UPDATE** sheet tab
3. Read all rows where column C (STATUS) = "PENDING"
4. Note each agent's code (column A) and name (column B)
5. If there are no PENDING rows, stop and tell me "No pending agents to update."

---

### STEP 2 — Log into PRISM

1. Open a new tab and navigate to **https://prism.prulifeuk.com.ph**
2. Enter the username and password (ask me if you don't have these stored)
3. Click **LOG IN**
4. A modal will appear: "PRISM requires an Email OTP for login authentication"
   - The radio option "Send OTP via email to my registered email address" should already be selected
   - Click **SUBMIT**
5. Immediately switch to the **plukfloroespiritu Gmail** tab
6. Look for the most recent email with subject **"PRISM: Login OTP"** — it arrives within 30 seconds
7. Open that email and copy the **6-digit OTP code**
   - ⚠️ The OTP expires in **5 minutes** — move quickly
   - ⚠️ If the plukfloroespiritu Gmail is not open in Chrome, ask me to open it before continuing
8. Switch back to the PRISM tab
9. Enter the 6-digit OTP in the field
10. Click **SUBMIT**
11. Confirm you are on the PRISM dashboard (you should see "FLORO GALAMAY ESPIRITU III" in the top-left and the navigation bar with Home, Dashboard, Servicing, etc.)

---

### STEP 3 — Fetch each agent's details (repeat for every PENDING agent)

For **each agent code** in your PENDING list, do the following:

#### 3a — Check session is still active
- Look at the top-left of PRISM. If you see a username (e.g., "FLORO GALAMAY ESPIRITU III"), the session is active.
- If the page shows the login screen or a timeout message, **go back to Step 2** to re-login and get a new OTP, then continue from the agent you were on.

#### 3b — Navigate to Agent Information
1. Click **Servicing** in the top navigation bar
2. In the dropdown, click **Agent Information**

#### 3c — Search by agent code
1. Make sure the search dropdown on the left shows **"Agent code"** (click the dropdown and select it if not)
2. Type the agent code into the search field (e.g., `70191371`)
3. Click **SEARCH**
4. Wait for the results table to appear

#### 3d — Open the agent detail page
1. In the SEARCH RESULTS table, click on the **agent code link** (it appears in blue/hyperlink style in the AGENT CODE column)
2. You will be taken to the **AGENT INFORMATION > AGENT DETAILS** page

#### 3e — Extract these 5 fields

| PRISM Field Label | Maps to Google Sheets Column |
|---|---|
| APPOINTMENT DATE | Column J — DATE APPOINTED |
| MANAGER | Column D — UM NAME |
| RECRUITER | Column F — RECRUITER NAME |
| DATE OF BIRTH | Column K — BIRTHDATE |
| BRANCH NAME | Column B — BRANCH (replaces "FOR_DB_UPDATE") |

Note the exact values shown for each field.

---

### STEP 4 — Update Google Sheets

1. Switch to the Google Sheets tab
2. Go to the **Database 2026** sheet
3. Find the row where column G (AGENT CODE) matches the agent code you just looked up
4. Update these cells in that row:
   - **Column B (BRANCH)**: replace `FOR_DB_UPDATE` with the BRANCH NAME from PRISM (e.g., `00481 - ESPERA BRANCH`)
   - **Column D (UM NAME)**: enter the MANAGER value from PRISM
   - **Column F (RECRUITER NAME)**: enter the RECRUITER value from PRISM
   - **Column J (DATE APPOINTED)**: enter the APPOINTMENT DATE from PRISM (e.g., `28-APR-2026`)
   - **Column K (BIRTHDATE)**: enter the DATE OF BIRTH from PRISM (e.g., `03-JAN-1999`)
5. Switch to the **PENDING_PRISM_UPDATE** sheet
6. Find the row for this agent and change column C (STATUS) from `PENDING` to `DONE`

Then go back to **Step 3a** and process the next agent.

---

### STEP 5 — Completion report

Once all agents have been processed, tell me:

> "Done. Updated [X] agents:
> - [Agent Code] [Agent Name] ✓
> - [Agent Code] [Agent Name] ✓
>
> Any issues: [list agents that could not be found or had errors, if any]"

---

### Error handling rules

| Situation | What to do |
|---|---|
| PRISM session times out mid-loop | Re-login (Step 2), get new OTP, continue from the next PENDING agent |
| Agent code search returns no results | Mark the agent STATUS as `NOT_FOUND` in PENDING_PRISM_UPDATE, continue to next agent, include in the issues report |
| OTP email doesn't arrive after 60 seconds | Ask me to check the Gmail inbox manually and provide the OTP |
| A field is blank or "N/A" on the PRISM detail page | Leave that cell empty in the sheet (do not write "N/A" or "null") |
| Gmail tab is not visible | Ask me to open the Gmail account that receives PRISM OTPs before proceeding |

---

**Important:** Do not close or refresh the PRISM tab between agents — navigate within the same session to avoid triggering a new OTP request. Only re-login if the session has actually expired.

---

*End of workflow prompt*
