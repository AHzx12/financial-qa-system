"""
File parsers — unified dispatch by extension.

Usage:
    from knowledge.parsers import parse_file
    docs = parse_file("/path/to/report.pdf")
"""
import os
import logging
from .base import ParsedDocument
from .pdf_parser import parse_pdf
from .csv_parser import parse_csv
from .docx_parser import parse_docx
from .json_parser import parse_json

logger = logging.getLogger("parsers")

# Extension → parser mapping
_PARSERS = {
    ".pdf": parse_pdf,
    ".csv": parse_csv,
    ".tsv": parse_csv,
    ".docx": parse_docx,
    ".json": parse_json,
}

SUPPORTED_EXTENSIONS = set(_PARSERS.keys())


def parse_file(filepath: str) -> list[ParsedDocument]:
    """
    Parse any supported file into a list of ParsedDocuments.
    Returns empty list for unsupported or failed files.
    """
    ext = os.path.splitext(filepath)[1].lower()
    parser = _PARSERS.get(ext)

    if parser is None:
        logger.warning("Unsupported file type '%s': %s", ext, filepath)
        return []

    try:
        return parser(filepath)
    except Exception as e:
        logger.error("Parser failed for '%s': %s", filepath, e)
        return []


def scan_directory(directory: str) -> list[str]:
    """
    Recursively find all supported files in a directory.
    Returns sorted list of absolute paths.
    """
    files = []
    for root, _, filenames in os.walk(directory):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                files.append(os.path.join(root, fname))
    return sorted(files)
