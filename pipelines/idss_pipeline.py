"""
pipelines/idss_pipeline.py
---------------------------
ORCHESTRATION LAYER — IDSS Data Platform main pipeline.

Coordinates all layers in sequence:
  1. CLOUD  — Ingest ZIPs from Gmail
  2. DATA   — Extract & transform PDF data
  3. AI     — Validate and summarize (optional)
  4. CLOUD  — Upload CSV to Google Drive
  5. CLOUD  — Trigger Apps Script to refresh Sheets

Architecture:
  Gmail → ZIP → PDF → DataFrame → CSV → Drive → Apps Script
"""

import os
from datetime import datetime

import pandas as pd

from connectors.drive_connector import upload_file
from core import config
from core.logger import get_logger
from integrations.apps_script import trigger_apps_script
from services.ai_service import summarize_report_with_ai, validate_and_fix_with_ai
from services.ingestion_service import download_latest_reports
from services.pdf_service import extract_raw_rows, extract_report_date
from services.prism_service import run_prism_update
from services.transform_service import build_dataframe, finalize_consolidated
from services.zip_service import extract_zip, list_pdfs
from storage.local_storage import cleanup_directory, save_csv

logger = get_logger(__name__)


def run_pipeline(use_ai_validation: bool = False) -> None:
    """
    Execute the full IDSS data pipeline end-to-end.

    Args:
        use_ai_validation: When True, run AI-based record validation
                           before saving (requires ANTHROPIC_API_KEY).
    """
    start = datetime.now()
    logger.info("=" * 60)
    logger.info("IDSS Data Platform — Pipeline START")
    logger.info("=" * 60)

    # ── Step 1: CLOUD — Download from Gmail ──────────────────────────────────
    logger.info("[1/5] Downloading IDSS reports from Gmail...")
    downloaded_files = download_latest_reports()

    if not downloaded_files:
        logger.info("No new files downloaded. Pipeline complete (nothing to process).")
        return

    logger.info(f"Downloaded {len(downloaded_files)} ZIP file(s): {downloaded_files}")

    # ── Step 2: DATA — Extract & Transform ───────────────────────────────────
    logger.info("[2/5] Extracting and transforming PDF data...")

    all_dfs = []

    for directory in (config.BRANCH_DIR, config.UNIT_DIR):
        zip_files = sorted(
            [
                f for f in os.listdir(directory)
                if f.lower().endswith(".zip") and not f.lower().endswith(".part")
            ],
            key=lambda f: os.path.getmtime(os.path.join(directory, f)),
        )

        for zip_file in zip_files:
            zip_path = os.path.join(directory, zip_file)
            extract_dir = os.path.join(directory, f"extracted_{os.path.splitext(zip_file)[0]}")
            os.makedirs(extract_dir, exist_ok=True)

            logger.info(f"  Extracting: {zip_file}")
            extract_zip(zip_path, extract_dir, password=config.ZIP_PASSWORD)

            pdf_files = list_pdfs(extract_dir)
            if not pdf_files:
                logger.warning(f"  No matching PDFs found in {extract_dir}")
                continue

            for pdf_path in pdf_files:
                report_date = extract_report_date(pdf_path)
                logger.info(f"  Processing: {os.path.basename(pdf_path)} | Date: {report_date}")

                table_rows, orphan_rows = extract_raw_rows(pdf_path)
                df = build_dataframe(table_rows, orphan_rows, report_date)
                all_dfs.append(df)

            # Clean up the ZIP after processing
            os.remove(zip_path)
            logger.info(f"  Deleted ZIP: {zip_file}")

    if not all_dfs:
        logger.warning("No data frames extracted. Aborting pipeline.")
        return

    # ── Step 3: AI — Validate (optional) ────────────────────────────────────
    if use_ai_validation:
        logger.info("[3/5] Running AI validation on extracted records...")
        validated_dfs = []
        for df in all_dfs:
            records = df.to_dict(orient="records")
            validated = validate_and_fix_with_ai(records)
            validated_dfs.append(pd.DataFrame(validated))
        all_dfs = validated_dfs
    else:
        logger.info("[3/5] AI validation skipped (set use_ai_validation=True to enable).")

    # ── Step 4: DATA — Consolidate and Save CSV ───────────────────────────────
    logger.info("[4/5] Consolidating and saving CSV...")
    final_df = pd.concat(all_dfs, ignore_index=True)
    final_df = finalize_consolidated(final_df)

    # Derive output filename from the date column header
    # (all known fixed columns are excluded; what remains is the date header)
    known_cols = {
        "UNIT", "AGENT CODE", "AGENT NAME",
        "CC", "APE", "MTD CC", "MTD APE", "YTD APE",
        "LAPSES", "NAP", "YTD CC", "NET CC", "", "  ",
    }
    date_cols = [c for c in final_df.columns if c not in known_cols]
    report_date_str = date_cols[0] if date_cols else "unknown"

    try:
        clean_date = datetime.strptime(report_date_str, "%B %d, %Y").strftime("%Y%m%d")
    except ValueError:
        clean_date = report_date_str.replace(" ", "_")

    csv_filename = f"Consolidated_Report_{clean_date}.csv"
    csv_path = os.path.join(config.OUTPUT_DIR, csv_filename)
    save_csv(final_df, csv_path)

    # Optional AI summary
    if config.ANTHROPIC_API_KEY:
        summary_meta = {
            "report_date": report_date_str,
            "total_agents": len(final_df),
            "total_APE": float(final_df["APE"].sum()),
            "total_CC": float(final_df["CC"].sum()),
        }
        summary = summarize_report_with_ai(summary_meta)
        if summary:
            logger.info(f"AI Report Summary:\n{summary}")

    # ── Step 5: CLOUD — Upload to Drive & Trigger Apps Script ─────────────────
    logger.info("[5/5] Uploading to Google Drive and triggering Apps Script...")

    upload_file(
        local_path=csv_path,
        folder_id=config.DRIVE_FOLDER_ID,
        client_secret_path=config.CLIENT_SECRET_PATH,
        cred_path=config.DRIVE_CRED_PATH,
    )

    apps_script_ok = trigger_apps_script(config.WEBAPP_URL)

    # ── Step 6: PRISM — Enrich new agents ────────────────────────────────────
    # The Apps Script (step 5) creates PENDING_PRISM_UPDATE with any new agents
    # flagged BRANCH = FOR_DB_UPDATE. We now fetch their details from Prism and
    # write them back to Database 2026.
    if apps_script_ok and config.SPREADSHEET_ID and config.PRISM_USERNAME and config.PRISM_PASSWORD:
        logger.info("[6/6] Running PRISM agent enrichment...")
        prism_result = run_prism_update(
            spreadsheet_id=config.SPREADSHEET_ID,
            prism_username=config.PRISM_USERNAME,
            prism_password=config.PRISM_PASSWORD,
            headless=True,
        )
        updated   = len(prism_result.get("updated", []))
        not_found = len(prism_result.get("not_found", []))
        errors    = len(prism_result.get("errors", []))
        logger.info(
            f"PRISM enrichment done — "
            f"updated: {updated}, not_found: {not_found}, errors: {errors}"
        )
        if prism_result.get("not_found"):
            logger.warning(
                "Agents not found in Prism: "
                + ", ".join(a["agent_code"] for a in prism_result["not_found"])
            )
        if prism_result.get("errors"):
            logger.error(
                "Agents with errors: "
                + ", ".join(
                    f"{a['agent_code']} ({a['error']})"
                    for a in prism_result["errors"]
                )
            )
    else:
        if not apps_script_ok:
            logger.warning("[6/6] Skipping PRISM enrichment — Apps Script trigger failed.")
        else:
            logger.info(
                "[6/6] Skipping PRISM enrichment — "
                "SPREADSHEET_ID / PRISM_USERNAME / PRISM_PASSWORD not configured."
            )

    # Clean up staging directories
    for directory in (config.BRANCH_DIR, config.UNIT_DIR):
        cleanup_directory(directory, keep_filename=None)

    elapsed = (datetime.now() - start).total_seconds()
    logger.info("=" * 60)
    logger.info(f"IDSS Data Platform — Pipeline COMPLETE ({elapsed:.1f}s)")
    logger.info(f"Output: {csv_path}")
    logger.info("=" * 60)
