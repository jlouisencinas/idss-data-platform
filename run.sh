#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# run.sh — IDSS Data Platform Linux/Docker launcher
# Used inside the Docker container (Cloud Run) or on a Linux server
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting IDSS pipeline..."
cd /app

python main.py

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pipeline complete."
