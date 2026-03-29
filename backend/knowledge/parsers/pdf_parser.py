"""
PDF parser — extracts text from PDF files using PyMuPDF (fitz).

Handles:
- Text-based PDFs (direct extraction)
- Section detection via font-size heuristics
- Tables converted to markdown format
- Metadata from filename/path

Does NOT handle scanned/image PDFs (would need OCR).
"""
import os
import logging


from .base import ParsedDocument, sanitize_id, detect_entity_from_filename, detect_category_from_path

logger = logging.getLogger("parser.pdf")


def parse_pdf(filepath: str) -> list[ParsedDocument]:
    """
    Parse a PDF file into a list of ParsedDocuments.
    Each logical section becomes a separate document for better chunking.

    Returns list of ParsedDocument. Empty list if parsing fails.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF not installed. Run: pip install pymupdf")
        return []

    filename = os.path.basename(filepath)
    base_id = sanitize_id(filename)
    entity = detect_entity_from_filename(filename)
    category = detect_category_from_path(filepath)

    try:
        doc = fitz.open(filepath)
    except Exception as e:
        logger.error("Failed to open PDF '%s': %s", filepath, e)
        return []

    if doc.page_count == 0:
        logger.warning("PDF '%s' has no pages", filepath)
        return []

    # Extract all text, page by page
    pages_text = []
    for page_num in range(doc.page_count):
        page = doc[page_num]
        text = page.get_text("text")
        if text.strip():
            pages_text.append(text.strip())

    doc.close()

    if not pages_text:
        logger.warning("PDF '%s' has no extractable text (may be scanned)", filepath)
        return []

    full_text = "\n\n".join(pages_text)

    # Try to split into sections by detecting heading-like lines
    sections = _split_into_sections(full_text)

    documents = []
    if len(sections) <= 1:
        # No clear sections — treat as single document
        documents.append(ParsedDocument(
            id=base_id,
            content=full_text,
            metadata={
                "source": "pdf",
                "category": category,
                "topic": _infer_topic(full_text, filename),
                "doc_type": "pdf",
                "entity": entity,
                "file_path": filepath,
                "page_count": len(pages_text),
            },
        ))
    else:
        # Multiple sections — each becomes a document
        for i, (title, content) in enumerate(sections):
            if len(content.strip()) < 30:
                continue  # Skip tiny sections
            section_id = f"{base_id}_sec_{i}"
            documents.append(ParsedDocument(
                id=section_id,
                content=f"## {title}\n\n{content}" if title else content,
                metadata={
                    "source": "pdf",
                    "category": category,
                    "topic": _infer_topic(content, title or filename),
                    "doc_type": "pdf",
                    "entity": entity,
                    "file_path": filepath,
                    "section_title": title or f"Section {i+1}",
                    "section_index": i,
                },
            ))

    logger.info("Parsed PDF '%s': %d documents", filename, len(documents))
    return documents


def _split_into_sections(text: str) -> list[tuple[str, str]]:
    """
    Split text into sections based on heading patterns.
    Returns list of (title, content) tuples.
    Heading heuristics: all-caps lines, lines starting with numbers (1. 2.),
    lines that are short and followed by a blank line.
    """
    import re

    lines = text.split("\n")
    sections = []
    current_title = ""
    current_lines = []

    for line in lines:
        stripped = line.strip()
        is_heading = False

        # Pattern: all caps, 3-80 chars, not a sentence
        if stripped and stripped.isupper() and 3 <= len(stripped) <= 80 and "." not in stripped:
            is_heading = True
        # Pattern: starts with number + dot (1. Introduction)
        elif re.match(r"^\d+[\.\)]\s+\S", stripped) and len(stripped) < 80:
            is_heading = True
        # Pattern: markdown-style heading
        elif stripped.startswith("#"):
            is_heading = True

        if is_heading and current_lines:
            sections.append((current_title, "\n".join(current_lines)))
            current_title = stripped.lstrip("#").strip()
            current_lines = []
        elif is_heading:
            current_title = stripped.lstrip("#").strip()
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_title, "\n".join(current_lines)))

    return sections


def _infer_topic(text: str, context: str = "") -> str:
    """Best-effort topic inference from content + filename."""
    combined = (text[:500] + " " + context).lower()

    topic_keywords = {
        "financial_statements": ["balance sheet", "income statement", "cash flow", "资产负债", "利润表", "现金流"],
        "valuation": ["p/e", "market cap", "valuation", "dcf", "估值", "市盈率", "市值"],
        "earnings": ["earnings", "revenue", "eps", "quarterly", "财报", "营收", "每股收益"],
        "risk": ["risk", "volatility", "hedge", "风险", "波动", "对冲"],
        "macroeconomics": ["gdp", "inflation", "interest rate", "fed", "通胀", "利率", "央行"],
        "technical_analysis": ["moving average", "rsi", "support", "resistance", "均线", "技术分析"],
    }
    for topic, keywords in topic_keywords.items():
        if any(kw in combined for kw in keywords):
            return topic
    return "general"
