"""
CSV parser — converts CSV/TSV files into embeddable documents.

Key design: CSV rows without context are meaningless. Each chunk
gets the column headers prepended so the embedding model (and LLM)
can understand what the numbers mean.

Chunking strategy: groups of ROWS_PER_CHUNK rows, each prefixed with headers.
Encoding: auto-detected via charset-normalizer (handles GBK/GB2312/GB18030).
"""
import os
import csv
import logging

from .base import ParsedDocument, sanitize_id, detect_entity_from_filename, detect_category_from_path

logger = logging.getLogger("parser.csv")

ROWS_PER_CHUNK = 15
MAX_COLUMNS = 20


def _detect_encoding(filepath: str) -> str:
    """Detect file encoding. Falls back to utf-8 if detection fails."""
    try:
        from charset_normalizer import from_path
        result = from_path(filepath)
        best = result.best()
        if best and best.encoding:
            logger.debug("Detected encoding for %s: %s", filepath, best.encoding)
            return best.encoding
    except Exception:
        pass
    return "utf-8"


def parse_csv(filepath: str) -> list[ParsedDocument]:
    """
    Parse a CSV/TSV file into a list of ParsedDocuments.
    Each chunk contains ROWS_PER_CHUNK rows with column headers.
    """
    filename = os.path.basename(filepath)
    base_id = sanitize_id(filename)
    entity = detect_entity_from_filename(filename)
    category = detect_category_from_path(filepath)

    encoding = _detect_encoding(filepath)

    try:
        # Detect delimiter
        with open(filepath, "r", encoding=encoding, errors="replace") as f:
            sample = f.read(4096)

        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")

        with open(filepath, "r", encoding=encoding, errors="replace") as f:
            reader = csv.reader(f, dialect)
            rows = list(reader)

    except Exception as e:
        logger.error("Failed to parse CSV '%s': %s", filepath, e)
        return []

    if len(rows) < 2:
        logger.warning("CSV '%s' has fewer than 2 rows", filepath)
        return []

    headers = rows[0]
    data_rows = rows[1:]

    if len(headers) > MAX_COLUMNS:
        logger.warning("CSV '%s' has %d columns (max %d), skipping", filepath, len(headers), MAX_COLUMNS)
        return []

    # Format header line
    header_line = " | ".join(h.strip() for h in headers)
    topic = _infer_csv_topic(headers, filename)

    # Chunk into groups of ROWS_PER_CHUNK
    documents = []
    for chunk_idx in range(0, len(data_rows), ROWS_PER_CHUNK):
        chunk_rows = data_rows[chunk_idx: chunk_idx + ROWS_PER_CHUNK]

        # Build markdown-style table
        lines = [f"Columns: {header_line}", ""]
        for row in chunk_rows:
            row_text = " | ".join(cell.strip() for cell in row)
            lines.append(row_text)

        content = "\n".join(lines)
        if len(content.strip()) < 20:
            continue

        row_start = chunk_idx + 1
        row_end = chunk_idx + len(chunk_rows)

        documents.append(ParsedDocument(
            id=f"{base_id}_rows_{row_start}_{row_end}",
            content=content,
            metadata={
                "source": "csv",
                "category": category,
                "topic": topic,
                "doc_type": "csv",
                "entity": entity,
                "file_path": filepath,
                "columns": headers,
                "row_range": f"{row_start}-{row_end}",
                "total_rows": len(data_rows),
            },
        ))

    logger.info("Parsed CSV '%s': %d rows → %d chunks", filename, len(data_rows), len(documents))
    return documents


def _infer_csv_topic(headers: list[str], filename: str) -> str:
    """Infer topic from column names and filename."""
    combined = " ".join(headers).lower() + " " + filename.lower()

    if any(k in combined for k in [
        "price", "close", "open", "volume", "ohlc", "high", "low",
        "股价", "收盘", "开盘", "最高", "最低", "成交量", "涨跌", "涨幅", "换手率",
    ]):
        return "market_data"
    if any(k in combined for k in [
        "revenue", "income", "eps", "earnings", "profit", "margin",
        "营收", "利润", "净利", "毛利", "每股收益", "营业收入", "净资产",
    ]):
        return "financial_statements"
    if any(k in combined for k in [
        "gdp", "cpi", "unemployment", "rate", "inflation",
        "通胀", "失业", "利率", "汇率", "货币供应",
    ]):
        return "macroeconomics"
    if any(k in combined for k in ["dividend", "yield", "股息", "分红", "派息"]):
        return "income"
    return "general"
