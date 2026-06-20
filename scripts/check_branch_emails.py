"""
scripts/check_branch_emails.py

Pre-flight check for the scheduled pipeline run.

Searches plukfloroespiritu Gmail for branch report emails from the last 3 days.
If any single report date has all 4 expected branch emails, prints "true".
Otherwise prints "false".

Used by the check-emails job in idss-pipeline.yml. The pipeline job only
runs when this script prints "true", avoiding wasted Playwright installs
and pipeline runs when not all reports have arrived yet.

Prints exactly one line to stdout: "true" or "false".
All diagnostic output goes to stderr so it doesn't pollute the capture.
"""

import json
import os
import re
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BRANCH_REGEX = re.compile(
    r"Branch Production Reports as of (\d{8})(?:\s+\((.+?)\))?",
    re.IGNORECASE,
)
EXPECTED_BRANCHES = 4


def main() -> None:
    # Manual workflow_dispatch → always run the pipeline
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        print("true", flush=True)
        return

    token_json = os.environ.get("PRISM_OTP_TOKEN_JSON", "")
    if not token_json:
        print("PRISM_OTP_TOKEN_JSON not set — skipping run.", file=sys.stderr)
        print("false", flush=True)
        return

    creds = Credentials.from_authorized_user_info(
        json.loads(token_json),
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    gmail = build("gmail", "v1", credentials=creds)

    results = gmail.users().messages().list(
        userId="me",
        q='subject:"Branch Production Reports as of" newer_than:3d',
        maxResults=50,
    ).execute()

    messages = results.get("messages", [])
    if not messages:
        print("No branch report emails found in the last 3 days.", file=sys.stderr)
        print("false", flush=True)
        return

    # Collect distinct branch names per report date
    date_branches: dict[str, set] = {}
    for msg_stub in messages:
        msg = gmail.users().messages().get(
            userId="me",
            id=msg_stub["id"],
            format="metadata",
            metadataHeaders=["Subject"],
        ).execute()
        subject = next(
            (h["value"] for h in msg["payload"]["headers"] if h["name"] == "Subject"),
            "",
        )
        m = BRANCH_REGEX.search(subject)
        if not m:
            continue
        date   = m.group(1)
        branch = (m.group(2) or "unknown").strip()
        date_branches.setdefault(date, set()).add(branch)

    print("Branch report counts per date:", file=sys.stderr)
    for date, branches in sorted(date_branches.items()):
        print(f"  {date}: {len(branches)}/{EXPECTED_BRANCHES}  {sorted(branches)}", file=sys.stderr)

    complete = [d for d, b in date_branches.items() if len(b) >= EXPECTED_BRANCHES]
    if complete:
        print(f"Complete date(s) found: {complete} — pipeline will run.", file=sys.stderr)
        print("true", flush=True)
    else:
        print("No date has all 4 branches yet — skipping this run.", file=sys.stderr)
        print("false", flush=True)


if __name__ == "__main__":
    main()
