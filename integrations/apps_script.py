"""
integrations/apps_script.py
-----------------------------
CLOUD LAYER — Google Apps Script integration.

Sends an HTTP POST to the deployed Apps Script Web App
to trigger downstream Google Sheets processing
after a new CSV has been uploaded to Drive.
"""

import requests

from core.logger import get_logger

logger = get_logger(__name__)


def trigger_apps_script(webapp_url: str, timeout: int = 30) -> bool:
    """
    Trigger the Google Apps Script Web App via HTTP POST.

    Args:
        webapp_url: The deployed Web App URL.
        timeout:    Request timeout in seconds.

    Returns:
        True if the trigger succeeded (HTTP 2xx), False otherwise.
    """
    try:
        response = requests.post(webapp_url, timeout=timeout)
        response.raise_for_status()
        logger.info(f"Apps Script triggered successfully. Response: {response.text[:200]}")
        return True
    except requests.exceptions.HTTPError as e:
        logger.error(f"Apps Script HTTP error: {e}")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Apps Script request failed: {e}")
        return False
