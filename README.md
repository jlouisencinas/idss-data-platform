# IDSS Data Platform

> Automated insurance production report pipeline — from Gmail inbox to Google Sheets — built with a clean **AI · Cloud · Data** architecture.

This project eliminates the manual process of downloading, extracting, and consolidating IDSS Branch Production Reports. It runs fully automated on a schedule, locally or on Google Cloud Run.

---

## What It Does

Every run, the pipeline:

1. **Fetches** the latest Branch and Unit Production Report ZIPs from Gmail
2. **Extracts** password-protected ZIPs and parses agent data from PDF tables
3. **Transforms** and deduplicates the data into a clean consolidated CSV
4. **Validates** records using Claude AI *(optional)*
5. **Uploads** the CSV to Google Drive
6. **Triggers** a Google Apps Script to refresh the live Sheets dashboard

---

## Architecture

```
main.py  (entry point)
    └── pipelines/idss_pipeline.py       ← orchestrates all steps
            ├── services/ingestion_service.py
            │       └── connectors/gmail_connector.py   ← Gmail API
            ├── services/zip_service.py                 ← ZIP extraction
            ├── services/pdf_service.py                 ← PDF parsing
            ├── services/transform_service.py           ← data cleaning
            ├── services/ai_service.py                  ← Claude AI (optional)
            ├── storage/local_storage.py                ← save CSV
            ├── connectors/drive_connector.py           ← Google Drive API
            └── integrations/apps_script.py             ← Apps Script trigger
```

| Layer | Responsibility | Tech |
|---|---|---|
| **Cloud** | Gmail download, Drive upload, Apps Script trigger | Google APIs v3, OAuth2 |
| **Data** | ZIP extract, PDF parse, DataFrame transform | pdfplumber, pandas |
| **AI** | Intelligent record extraction and validation | Anthropic Claude API |
| **Core** | Config, logging, environment detection | Python stdlib |

---

## Tech Stack

- **Language** — Python 3.11
- **Data** — pandas, pdfplumber
- **Cloud** — Google Gmail API, Drive API v3, Apps Script
- **AI** — Anthropic Claude (`claude-opus-4-6`)
- **Infra** — Docker, Google Cloud Run, Cloud Build, Cloud Scheduler, Secret Manager
- **CI/CD** — Git + `cloudbuild.yaml` (auto-deploy on push)

---

## Running Locally

```bash
# 1. Clone and set up
git clone https://github.com/jlouisencinas/idss-data-platform.git
cd idss-data-platform
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt

# 2. Run (opens browser for OAuth on first run)
python main.py

# 3. Optional — enable Claude AI validation
set ANTHROPIC_API_KEY=sk-ant-...
python main.py --ai

# 4. Download only (skip processing)
python main.py --ingest
```

Automate with **Windows Task Scheduler** using the included `run.bat`.

---

## Deploying to Google Cloud Run

```bash
# 1. Store secrets in Secret Manager
gcloud secrets create gmail-token-json --data-file=token.json
gcloud secrets create service-account-json --data-file=service_account.json

# 2. Build and deploy
gcloud builds submit --config cloudbuild.yaml

# 3. Schedule daily at 8 AM Manila time
gcloud scheduler jobs create http idss-daily \
  --schedule="0 8 * * *" \
  --time-zone="Asia/Manila" \
  --uri="..."
```

Environment is detected automatically — the same codebase runs locally and on Cloud Run with no code changes. Cloud Run sets `K_SERVICE`; the app switches to headless auth via Secret Manager.

---

## Environment Variables (Cloud Run)

| Variable | Description |
|---|---|
| `GMAIL_TOKEN_JSON` | Contents of `token.json` (Gmail OAuth2 token) |
| `SERVICE_ACCOUNT_JSON` | Contents of `service_account.json` (Drive upload) |
| `DRIVE_FOLDER_ID` | Target Google Drive folder ID |
| `WEBAPP_URL` | Google Apps Script Web App URL |
| `ANTHROPIC_API_KEY` | Claude API key *(optional)* |

Copy `.env.example` → `.env` for local overrides.

---

## Project Structure

```
idss-data-platform/
├── core/               # config, logger
├── connectors/         # Gmail, Drive API clients
├── services/           # ingestion, zip, pdf, transform, AI
├── integrations/       # Apps Script trigger
├── storage/            # local CSV persistence
├── pipelines/          # pipeline orchestrator
├── data/               # runtime data (gitignored)
├── logs/               # run logs (gitignored)
├── Dockerfile
├── cloudbuild.yaml
├── run.bat             # Windows Task Scheduler launcher
└── main.py
```

---

## Background

This started as a fully manual process — downloading report emails, unzipping files, parsing PDFs, and uploading CSVs by hand every reporting cycle. This platform replaces that entirely, and was built as a learning project integrating **AI**, **Cloud infrastructure**, and **Data engineering** into a single production-ready pipeline.

---

*Built by [John Louis Encinas](https://github.com/jlouisencinas)*
