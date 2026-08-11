"""Bounded lookup for the curated GROMACS mdrun knowledge base.

The parser deliberately accepts only the small, line-oriented schema in
``docs/knowledge_mdrun.md``.  It never exposes arbitrary Markdown, paths, or
runtime logs to an LLM.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping

from willy._paths import get_project_root

KNOWLEDGE_PATH = get_project_root() / "docs" / "knowledge_mdrun.md"
MAX_ENTRIES_PER_LOOKUP = 3

_ENTRY_RE = re.compile(r"^### Entry (\d+): (.+?)\s*$")
_FIELD_RE = re.compile(r"^- ([a-z_]+):\s*(.*?)\s*$")
_REQUIRED_FIELDS = {
    "category",
    "source_url",
    "source_section",
    "applicable_when",
    "facts",
    "evidence_needed",
    "allowed_adjustment_fields",
    "compatibility_notice",
}


class MdrunKnowledgeError(ValueError):
    """Raised when the local, curated knowledge document is malformed."""


def _normalize_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.strip().split()).casefold()


def _safe_text(value: object, limit: int = 600) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    return text[:limit]


def _parse_document(path: Path | None = None) -> list[dict[str, Any]]:
    source = Path(path or KNOWLEDGE_PATH)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MdrunKnowledgeError("GROMACS MD 知识库不可用") from exc

    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        match = _ENTRY_RE.match(line)
        if match:
            if current is not None:
                entries.append(current)
            current = {"number": int(match.group(1)), "name": match.group(2).strip()}
            continue
        if current is None:
            continue
        field = _FIELD_RE.match(line)
        if field:
            current[field.group(1)] = field.group(2).strip()
    if current is not None:
        entries.append(current)

    if not entries:
        raise MdrunKnowledgeError("GROMACS MD 知识库没有有效条目")
    seen_numbers: set[int] = set()
    seen_names: set[str] = set()
    for entry in entries:
        missing = _REQUIRED_FIELDS.difference(entry)
        if missing or entry["number"] in seen_numbers or _normalize_name(entry["name"]) in seen_names:
            raise MdrunKnowledgeError("GROMACS MD 知识库条目格式无效")
        seen_numbers.add(entry["number"])
        seen_names.add(_normalize_name(entry["name"]))
        entry["entry_id"] = f"mdrun-{entry['number']:03d}"
        fields = entry.get("allowed_adjustment_fields", "")
        entry["allowed_adjustment_fields"] = [
            item.strip() for item in fields.split(",") if item.strip()
        ]
    return sorted(entries, key=lambda item: item["number"])


def public_entry_index(path: Path | None = None) -> list[dict[str, Any]]:
    """Return only numeric IDs and names for prompt injection."""
    return [
        {"number": int(entry["number"]), "name": str(entry["name"])}
        for entry in _parse_document(path)
    ]


def _public_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "number": int(entry["number"]),
        "entry_id": str(entry["entry_id"]),
        "name": str(entry["name"]),
        "category": _safe_text(entry["category"], 80),
        "applicable_when": _safe_text(entry["applicable_when"]),
        "facts": _safe_text(entry["facts"]),
        "evidence_needed": _safe_text(entry["evidence_needed"]),
        "allowed_adjustment_fields": list(entry["allowed_adjustment_fields"]),
        "compatibility_notice": _safe_text(entry["compatibility_notice"]),
        "source_url": _safe_text(entry["source_url"], 300),
        "source_section": _safe_text(entry["source_section"], 160),
    }


def lookup_mdrun_knowledge(
    requests: object,
    *,
    path: Path | None = None,
    max_entries: int = MAX_ENTRIES_PER_LOOKUP,
) -> dict[str, Any]:
    """Resolve a bounded list of ``{number, name}`` references.

    Both fields are required and must match the same canonical entry.  A
    mismatch is reported without revealing internal document content.
    """
    if not isinstance(requests, list) or not requests:
        return {"ok": False, "lookup_status": "not_matched", "entries": [], "errors": ["必须提供条目数字和名称"]}
    if len(requests) > min(max_entries, MAX_ENTRIES_PER_LOOKUP):
        return {"ok": False, "lookup_status": "not_matched", "entries": [], "errors": ["单次最多读取 3 条知识条目"]}
    try:
        entries = _parse_document(path)
    except MdrunKnowledgeError as exc:
        return {"ok": False, "lookup_status": "unavailable", "entries": [], "errors": [str(exc)]}

    by_number = {entry["number"]: entry for entry in entries}
    matched: list[dict[str, Any]] = []
    errors: list[str] = []
    for request in requests:
        if not isinstance(request, Mapping) or isinstance(request.get("number"), bool):
            errors.append("条目必须同时包含 number 和 name")
            continue
        try:
            number = int(request.get("number"))
        except (TypeError, ValueError):
            errors.append("条目 number 无效")
            continue
        entry = by_number.get(number)
        if entry is None or _normalize_name(request.get("name")) != _normalize_name(entry["name"]):
            errors.append(f"条目 {number} 的数字与名称不匹配")
            continue
        matched.append(_public_entry(entry))
    status = "retrieved" if matched else "not_matched"
    return {
        "ok": bool(matched),
        "lookup_status": status,
        "entries": matched,
        "errors": errors,
    }
