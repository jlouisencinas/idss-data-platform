"""
services/ai_service.py
-----------------------
AI LAYER — Claude-powered intelligent extraction service.

Uses the Anthropic API to parse IDSS Production Report text
when traditional table extraction produces incomplete results.
This acts as a smart fallback and validation layer.
"""

import json

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

from core import config
from core.logger import get_logger

logger = get_logger(__name__)

# ─── Prompt Templates ─────────────────────────────────────────────────────────

_EXTRACTION_PROMPT = """\
You are a data extraction assistant for insurance production reports.

Below is raw text extracted from an IDSS Branch Production Report PDF.
Extract every agent record and return them as a JSON array.

Each object in the array must have these exact keys:
  "AGENT CODE"  — 7+ digit string
  "AGENT NAME"  — string
  "CC"          — number
  "APE"         — number
  "MTD CC"      — number
  "MTD APE"     — number
  "YTD APE"     — number
  "LAPSES"      — number
  "NAP"         — number
  "YTD CC"      — number
  "NET CC"      — number

Rules:
- Skip header rows, summary rows, manager lines (BM:, DM:, UM:, etc.)
- Skip rows ending with "*"
- Use 0 for any missing numeric field
- Return ONLY valid JSON (no markdown, no commentary)

Raw text:
{text}
"""

_VALIDATION_PROMPT = """\
You are a quality control assistant for insurance data.

Below is a JSON array of agent records extracted from a PDF report.
Identify and fix any obvious issues:
- Duplicate agent codes (keep the one with the longest name)
- Names that appear truncated or merged with the next field
- Numeric fields that seem clearly wrong (e.g., negative CC)

Return the corrected JSON array only. No explanations.

Records:
{records}
"""


# ─── Public API ───────────────────────────────────────────────────────────────

def extract_with_ai(page_text: str) -> list[dict]:
    """
    Use Claude to extract agent records from raw PDF page text.

    Args:
        page_text: The full text of one or more PDF pages.

    Returns:
        List of agent record dicts, or [] on failure.
    """
    if not _ANTHROPIC_AVAILABLE:
        logger.warning("anthropic package not installed — AI extraction skipped.")
        return []
    if not config.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — AI extraction skipped.")
        return []

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=config.AI_MODEL,
            max_tokens=config.AI_MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": _EXTRACTION_PROMPT.format(text=page_text),
                }
            ],
        )

        raw = message.content[0].text.strip()
        records = json.loads(raw)
        logger.info(f"AI extracted {len(records)} agent records.")
        return records

    except json.JSONDecodeError as e:
        logger.error(f"AI returned invalid JSON: {e}")
        return []
    except Exception as e:
        logger.error(f"AI extraction failed: {e}")
        return []


def validate_and_fix_with_ai(records: list[dict]) -> list[dict]:
    """
    Use Claude to validate and clean a list of already-extracted records.

    Args:
        records: List of agent record dicts from the data pipeline.

    Returns:
        Cleaned list of records, or the original list on failure.
    """
    if not _ANTHROPIC_AVAILABLE:
        logger.warning("anthropic package not installed — AI validation skipped.")
        return records
    if not config.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set — AI validation skipped.")
        return records

    if not records:
        return records

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=config.AI_MODEL,
            max_tokens=config.AI_MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": _VALIDATION_PROMPT.format(
                        records=json.dumps(records, indent=2)
                    ),
                }
            ],
        )

        raw = message.content[0].text.strip()
        validated = json.loads(raw)
        logger.info(f"AI validated {len(validated)} records (was {len(records)}).")
        return validated

    except json.JSONDecodeError as e:
        logger.error(f"AI validation returned invalid JSON: {e}")
        return records
    except Exception as e:
        logger.error(f"AI validation failed: {e}")
        return records


def summarize_report_with_ai(df_summary: dict) -> str:
    """
    Generate a natural-language summary of the consolidated report.

    Args:
        df_summary: A dict with key metrics (total agents, total APE, date, etc.)

    Returns:
        A brief summary string, or empty string on failure.
    """
    if not _ANTHROPIC_AVAILABLE or not config.ANTHROPIC_API_KEY:
        return ""

    prompt = (
        "Generate a 2-3 sentence executive summary of an IDSS production report. "
        f"Here are the key metrics: {json.dumps(df_summary)}. "
        "Be concise and professional. Focus on total agents processed, APE, and report date."
    )

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=config.AI_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        summary = message.content[0].text.strip()
        logger.info("AI generated report summary.")
        return summary

    except Exception as e:
        logger.error(f"AI summary failed: {e}")
        return ""
