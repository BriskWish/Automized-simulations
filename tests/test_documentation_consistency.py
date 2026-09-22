"""Acceptance checks for the locally maintained documentation baseline."""

from __future__ import annotations

from pathlib import Path
import json
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
    assert inline in (ROOT / "docs" / "testing.md").read_text(encoding="utf-8")
    assert registry in (ROOT / "docs" / "document_registry.md").read_text(encoding="utf-8")


def test_registry_assigns_owners_to_current_acceptance_documents() -> None:
    registry = (ROOT / "docs" / "document_registry.md").read_text(encoding="utf-8")
    for document, owner in (
        ("project_gap_analysis.md", "0 号、5 号"),
        ("testing.md", "6 号"),
        ("test_case_catalog.md", "6 号"),
    ):
        rows = [line for line in registry.splitlines() if document in line and line.startswith("|")]
        assert len(rows) == 1
        assert owner in rows[0]


def test_eq_coverage_contract_replaces_plan_and_has_sanitized_evidence() -> None:
    from willy.simulation.eq_acceptance import EQ_ACCEPTANCE_POLICY

    report = json.loads((ROOT / "tests/reports/audits/g09_eq_coverage_20260906.json").read_text())
    assert report["policy"] == EQ_ACCEPTANCE_POLICY and report["passed"] is True
    for case in report["cases"].values():
        assert case["originals_unchanged"] is True
        assert case["accepted"] == case["production_permission"] == case["expected_accepted"]
        assert case["managed_auxiliary_execution"] is True
    text = json.dumps(report, ensure_ascii=False)
    for forbidden in ("/home/", "/tmp/", "md_run/", "raw_output", "stderr", "api_key", "api-key", "Bearer "):
        assert forbidden not in text
    revision = (ROOT / "docs/revision.md").read_text()
    assert "EQ 最后 1 ns 五段覆盖验收" not in revision
    gaps = (ROOT / "docs/project_gap_analysis.md").read_text()
    open_items, closed_items = gaps.split("## 已关闭项", 1)
    assert "| G-09 |" not in open_items and "| G-09 |" in closed_items
    assert EQ_ACCEPTANCE_POLICY in (ROOT / "docs/simulation.md").read_text()


def test_charge_policy_documents_match_the_runtime_tolerance() -> None:
    from willy.simulation._gmx_utils import GROMPP_NET_CHARGE_TOLERANCE_E

    tolerance = f"{GROMPP_NET_CHARGE_TOLERANCE_E:g} e"
    for document in (
        "README.md", "PROJECT_OVERVIEW.md", "docs/ERR_WARN_Build.md",
        "docs/quantum.md", "docs/simulation.md",
    ):
        content = (ROOT / document).read_text(encoding="utf-8")
        assert f"{tolerance}`" in content, document
        assert "grompp" in content and "Ewald" in content, document
        if document != "README.md":
            assert "charge_imbalance" in content, document

    boundary = f"[-{tolerance}, +{tolerance}]"
    for document in ("docs/ERR_WARN_Build.md", "docs/simulation.md"):
        content = (ROOT / document).read_text(encoding="utf-8")
        assert boundary in content, document
