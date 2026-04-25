"""
main.py
--------
IDSS Data Platform — Entry point.

Run this file to execute the full pipeline:
    python main.py

Optional flags:
    --ai        Enable AI-powered record validation (requires ANTHROPIC_API_KEY)
    --ingest    Run ingestion step only (download ZIPs, skip processing)
"""

import argparse
import sys

from core.logger import get_logger
from pipelines.idss_pipeline import run_pipeline
from services.ingestion_service import download_latest_reports

logger = get_logger("idss-data-platform")


def parse_args():
    parser = argparse.ArgumentParser(
        description="IDSS Data Platform — automated report extraction pipeline"
    )
    parser.add_argument(
        "--ai",
        action="store_true",
        help="Enable AI-powered record validation via Claude API",
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Run ingestion only (download ZIPs from Gmail, skip processing)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.ingest:
        logger.info("Running ingestion-only mode...")
        files = download_latest_reports()
        logger.info(f"Downloaded: {files or 'no new files'}")
        return

    run_pipeline(use_ai_validation=args.ai)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user.")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Pipeline failed with unexpected error: {e}")
        sys.exit(1)
