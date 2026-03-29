# knowledge/parsers/json_parser.py

import os
import json
import logging
from .base import ParsedDocument, sanitize_id, detect_entity_from_filename, detect_category_from_path

logger = logging.getLogger("parser.json")


def parse_json(filepath: str) -> list[ParsedDocument]:
    filename = os.path.basename(filepath)
    
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error("Failed to parse JSON '%s': %s", filename, e)
        return []

    # 支持两种格式：数组 [{id, content, metadata}] 或单个对象 {id, content, metadata}
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        logger.warning("JSON '%s' is not an array or object", filename)
        return []

    documents = []
    base_id = sanitize_id(filename)

    for i, item in enumerate(data):
        doc_id = item.get("id", f"{base_id}_{i}")
        content = item.get("content", "")
        if not content.strip():
            continue

        metadata = item.get("metadata", {})
        metadata.setdefault("source", "json")
        metadata.setdefault("doc_type", "json")
        metadata.setdefault("category", detect_category_from_path(filepath))
        metadata.setdefault("file_path", filepath)

        documents.append(ParsedDocument(id=doc_id, content=content, metadata=metadata))

    logger.info("Parsed JSON '%s': %d documents", filename, len(documents))
    return documents