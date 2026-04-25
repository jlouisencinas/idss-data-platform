# ─────────────────────────────────────────────────────────────────────────────
# Dockerfile — IDSS Data Platform
# Builds a container for Google Cloud Run Jobs
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# System deps required by pdfplumber / pypdfium2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache — only rebuilds on requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code (credentials are NOT copied — supplied at runtime via env vars)
COPY core/        ./core/
COPY connectors/  ./connectors/
COPY services/    ./services/
COPY integrations/ ./integrations/
COPY storage/     ./storage/
COPY pipelines/   ./pipelines/
COPY main.py      .

# Create runtime directories (data and logs)
RUN mkdir -p data/downloads/branch data/downloads/unit data/output data/temp logs

# Cloud Run Jobs run once and exit — no PORT needed
CMD ["python", "main.py"]
