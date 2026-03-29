"""
Base parser interface.
All parsers output a list of ParsedDocument dicts with the same structure,
matching the existing seed_knowledge.json format.
"""
from dataclasses import dataclass, field, asdict



@dataclass
class ParsedDocument:
    """Unified document format — every parser produces a list of these."""
    id: str                         # Unique document ID
    content: str                    # Full text content
    metadata: dict = field(default_factory=dict)
    # metadata should include at minimum:
    #   source: str      — e.g. "sec_filing", "financial_glossary", "csv_data"
    #   category: str    — "concept" | "analysis" | "data" | "report"
    #   topic: str       — e.g. "financial_statements", "valuation"
    #   doc_type: str    — "pdf" | "csv" | "docx" | "json"
    # optional:
    #   entity: str      — ticker symbol if applicable (e.g. "AAPL")
    #   period: str      — e.g. "2025-Q3"
    #   subtopic: str    — finer classification
    #   file_path: str   — original file path

    def to_dict(self) -> dict:
        return {"id": self.id, "content": self.content, "metadata": self.metadata}


def sanitize_id(text: str) -> str:
    """Convert a filename or path into a safe document ID."""
    import re
    # Remove extension, replace special chars
    clean = re.sub(r"\.[^.]+$", "", text)       # strip extension
    clean = re.sub(r"[^\w\-]", "_", clean)      # replace non-alphanum
    clean = re.sub(r"_+", "_", clean).strip("_") # collapse underscores
    return clean.lower()[:100]


def detect_entity_from_filename(filename: str) -> str:
    """Try to extract a ticker symbol from filename.
    e.g. 'AAPL_Q3_2025_earnings.pdf' → 'AAPL'
    """
    import re
    match = re.match(r"^([A-Z]{2,6})[\s_\-]", filename)
    return match.group(1) if match else ""


def detect_category_from_path(filepath: str) -> str:
    """Infer category from directory structure.
    e.g. 'docs/earnings/AAPL.pdf' → 'analysis'
    """
    path_lower = filepath.lower()
    if any(k in path_lower for k in ["concept", "glossary", "definition", "term"]):
        return "concept"
    if any(k in path_lower for k in ["earning", "report", "analysis", "filing", "sec"]):
        return "analysis"
    if any(k in path_lower for k in ["data", "csv", "table", "price"]):
        return "data"
    return "report"
