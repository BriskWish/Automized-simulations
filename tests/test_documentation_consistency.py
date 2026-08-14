"""Acceptance checks for the locally maintained documentation baseline."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]+\]\((?P<target>[^)]+)\)")
CATALOG_COUNTS = re.compile(
    r"收集到 (?P<pytest>\d+) 条 pytest 用例；另有 (?P<llm>\d+) 条离线 LLM mock eval 场景；本台账共 (?P<total>\d+) 条记录"
)


def _markdown_sources() -> list[Path]:
    return [
        ROOT / "README.md",
        ROOT / "PROJECT_OVERVIEW.md",
        *(ROOT / "docs").glob("*.md"),
        ROOT / "tests" / "reports" / "README.md",
        ROOT / "tests" / "reports" / "test_case_catalog.md",
    ]


def test_local_markdown_links_resolve() -> None:
    missing: list[str] = []
    for source in _markdown_sources():
        for match in LINK.finditer(source.read_text(encoding="utf-8")):
            target = match.group("target").strip().strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith(("mailto:", "/")):
                continue
            # Chemical SMARTS/SMILES examples can use Markdown-like brackets;
            # only repository file references are in scope for this gate.
            if not Path(target).suffix:
                continue
            if not (source.parent / target).resolve().is_file():
                missing.append(f"{source.relative_to(ROOT)} -> {target}")
    assert not missing, "broken local Markdown links:\n" + "\n".join(missing)


def test_acceptance_documents_match_the_generated_catalog() -> None:
    catalog = (ROOT / "tests" / "reports" / "test_case_catalog.md").read_text(encoding="utf-8")
    match = CATALOG_COUNTS.search(catalog)
    assert match is not None
    counts = match.groupdict()
    inline = f"{counts['pytest']} pytest + {counts['llm']} LLM = {counts['total']}"
    registry = f"（{counts['pytest']} 条 pytest、{counts['llm']} 条 LLM 场景，共 {counts['total']} 条记录）"
    assert inline in (ROOT / "docs" / "project_gap_analysis.md").read_text(encoding="utf-8")
    assert inline in (ROOT / "docs" / "testing_strategy.md").read_text(encoding="utf-8")
    assert registry in (ROOT / "docs" / "document_registry.md").read_text(encoding="utf-8")


def test_registry_assigns_owners_to_current_acceptance_documents() -> None:
    registry = (ROOT / "docs" / "document_registry.md").read_text(encoding="utf-8")
    for document, owner in (
        ("project_gap_analysis.md", "0 号、5 号"),
        ("testing_strategy.md", "6 号"),
        ("test_case_catalog.md", "6 号"),
    ):
        rows = [line for line in registry.splitlines() if document in line and line.startswith("|")]
        assert len(rows) == 1
        assert owner in rows[0]
