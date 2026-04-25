"""
services/zip_service.py
------------------------
DATA LAYER — ZIP extraction service.

Extracts password-protected ZIP archives
into a temporary staging directory.
"""

import os
import zipfile

from core.logger import get_logger

logger = get_logger(__name__)


def extract_zip(zip_path: str, output_dir: str, password: bytes = None) -> str:
    """
    Extract a (optionally password-protected) ZIP file.

    Args:
        zip_path:   Path to the .zip file.
        output_dir: Directory to extract contents into.
        password:   ZIP password as bytes, or None for unprotected ZIPs.

    Returns:
        The output directory path.
    """
    os.makedirs(output_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        if password:
            zf.setpassword(password)
        zf.extractall(output_dir)

    logger.info(f"Extracted {os.path.basename(zip_path)} → {output_dir}")
    return output_dir


def list_pdfs(directory: str, suffix: str = "branchproductionreport.pdf") -> list:
    """
    Return all PDF files in a directory whose names end with the given suffix.
    Excludes files with underscores in the name (temp/partial files).
    """
    return [
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if f.lower().endswith(suffix) and "_" not in f
    ]
