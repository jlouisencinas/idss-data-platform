"""
storage/local_storage.py
--------------------------
DATA LAYER — Local filesystem persistence.

Handles saving DataFrames and raw bytes to disk,
and cleaning up staging directories after processing.
"""

import os
import shutil

import pandas as pd

from core.logger import get_logger

logger = get_logger(__name__)


def save_bytes(path: str, content: bytes) -> None:
    """Write raw bytes to a file, creating parent directories as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    logger.debug(f"Saved bytes → {path}")


def save_csv(df: pd.DataFrame, path: str) -> str:
    """
    Save a DataFrame to CSV.

    Returns the saved file path.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    logger.info(f"Saved CSV → {path} ({len(df)} rows)")
    return path


def cleanup_directory(folder_path: str, keep_filename: str = None) -> None:
    """
    Remove all files and subdirectories in a folder.

    If keep_filename is given, that file is preserved.
    """
    for item in os.listdir(folder_path):
        if keep_filename and item == keep_filename:
            continue

        item_path = os.path.join(folder_path, item)
        try:
            if os.path.isfile(item_path):
                os.remove(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
            logger.debug(f"Deleted: {item}")
        except Exception as e:
            logger.warning(f"Could not delete {item}: {e}")

    logger.info(f"Cleaned up staging directory: {folder_path}")
