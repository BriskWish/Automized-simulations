"""Gaussian SMD solvent records and deterministic input rendering."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Mapping

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from willy._paths import get_project_root
from willy.config_store import _exclusive_lock, write_json


SOLVENT_DIR_NAME = "smd_solvents"
BUILTIN_FILENAME = "gaussian_builtin.json"
MANUAL_FILENAME = "gaussian_manual.json"
GAS_SOLVENT = "gas"


class SMDSolventError(ValueError):
    """Raised when an SMD solvent record cannot be accepted or rendered."""


@dataclass(frozen=True)
class SMDSolvent:
    name: str
    epsilon: str | None
    epsinf: str | None
    source: str

    @property
    def is_manual(self) -> bool:
        return self.source == "manual"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "epsilon": self.epsilon,
            "epsinf": self.epsinf,
            "source": self.source,
            "manual": self.is_manual,
        }


def solvent_directory(project_root: str | Path | None = None) -> Path:
    root = Path(project_root) if project_root is not None else get_project_root()
    if (root / SOLVENT_DIR_NAME).is_dir():
        return root / SOLVENT_DIR_NAME
    return root / "struct" / SOLVENT_DIR_NAME


def _load_file(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SMDSolventError(f"无法读取溶剂库 {path.name}: {exc}") from exc
    records = payload.get("solvents") if isinstance(payload, Mapping) else None
    if not isinstance(records, Mapping):
        raise SMDSolventError(f"溶剂库 {path.name} 必须包含 solvents 对象")
    return {str(name): dict(value) for name, value in records.items() if isinstance(value, Mapping)}


def load_solvents(project_root: str | Path | None = None) -> tuple[dict[str, SMDSolvent], dict[str, SMDSolvent]]:
    directory = solvent_directory(project_root)
    builtin_path = directory / BUILTIN_FILENAME
    if not builtin_path.exists():
        builtin_path = Path(__file__).resolve().parents[3] / "struct" / SOLVENT_DIR_NAME / BUILTIN_FILENAME
    builtin = {
        name: SMDSolvent(name, str(record.get("epsilon")), None, "builtin")
        for name, record in _load_file(builtin_path).items()
        if record.get("epsilon") is not None
    }
    manual = {
        name: SMDSolvent(name, str(record.get("epsilon")), str(record.get("epsinf")), "manual")
        for name, record in _load_file(directory / MANUAL_FILENAME).items()
        if record.get("epsilon") is not None and record.get("epsinf") is not None
    }
    return builtin, manual


def _name_key(name: object) -> str:
    return str(name).strip().casefold()


def resolve_exact(name: object, project_root: str | Path | None = None) -> SMDSolvent | None:
    if not isinstance(name, str) or not name.strip():
        return None
    builtin, manual = load_solvents(project_root)
    key = _name_key(name)
    for record in (*builtin.values(), *manual.values()):
        if _name_key(record.name) == key:
            return record
    return None


def _finite_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise SMDSolventError(f"{field} 必须是有限正数")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError, ValueError) as exc:
        raise SMDSolventError(f"{field} 必须是有限正数") from exc
    if not number.is_finite() or number < 1:
        raise SMDSolventError(f"{field} 必须满足 >= 1")
    if number.adjusted() > 100 or number.as_tuple().exponent < -100:
        raise SMDSolventError(f"{field} 数值表示过长")
    return number


def validate_custom_values(epsilon: object, epsinf: object) -> tuple[str, str]:
    dielectric = _finite_decimal(epsilon, "epsilon")
    optical = _finite_decimal(epsinf, "epsinf")
    if dielectric < optical:
        raise SMDSolventError("必须满足 epsilon >= epsinf >= 1")
    return format(dielectric, "f"), format(optical, "f")


def _validate_custom_name(name: object) -> str:
    if not isinstance(name, str):
        raise SMDSolventError("人工溶剂名必须是文本")
    clean = name.strip()
    if not clean or len(clean) > 96 or any(not char.isprintable() for char in clean):
        raise SMDSolventError("人工溶剂名不能为空、不能包含控制字符且长度不得超过 96")
    if _name_key(clean) == GAS_SOLVENT:
        raise SMDSolventError("gas 是保留值，不能登记为人工溶剂")
    return clean


def next_default_name(project_root: str | Path | None = None) -> str:
    builtin, manual = load_solvents(project_root)
    occupied = {_name_key(name) for name in (*builtin, *manual)}
    index = 1
    while f"default_{index}".casefold() in occupied:
        index += 1
    return f"default_{index}"


def register_manual_solvent(
    name: str | None,
    epsilon: object,
    epsinf: object,
    *,
    project_root: str | Path | None = None,
) -> SMDSolvent:
    """Persist one manual solvent without ever overwriting either library."""
    dielectric, optical = validate_custom_values(epsilon, epsinf)
    directory = solvent_directory(project_root)
    with _exclusive_lock(directory / "catalog"):
        clean_name = next_default_name(project_root) if name is None or name == "" else _validate_custom_name(name)
        builtin, manual = load_solvents(project_root)
        key = _name_key(clean_name)
        if any(_name_key(record.name) == key for record in (*builtin.values(), *manual.values())):
            raise SMDSolventError(f"溶剂名已存在（大小写不敏感精确匹配）: {clean_name}")
        records = _load_file(directory / MANUAL_FILENAME)
        records[clean_name] = {"epsilon": dielectric, "epsinf": optical, "manual": True}
        write_json(directory / MANUAL_FILENAME, {"solvents": records})
    return SMDSolvent(clean_name, dielectric, optical, "manual")


def list_solvents(project_root: str | Path | None = None) -> list[SMDSolvent]:
    builtin, manual = load_solvents(project_root)
    return sorted((*builtin.values(), *manual.values()), key=lambda item: item.name.casefold())


def lookup_solvent(query: str, *, limit: int = 5, project_root: str | Path | None = None) -> dict[str, Any]:
    """Resolve exact names first, then return vector-ranked candidates."""
    exact = resolve_exact(query, project_root)
    if exact is not None:
        return {"match": exact.as_dict(), "candidates": [exact.as_dict()]}
    records = list_solvents(project_root)
    if not records or not isinstance(query, str) or not query.strip():
        return {"match": None, "candidates": []}
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
    matrix = vectorizer.fit_transform([record.name for record in records])
    scores = cosine_similarity(vectorizer.transform([query.strip()]), matrix)[0]
    ranked = sorted(zip(scores, records), key=lambda item: (-float(item[0]), item[1].name.casefold()))
    candidates = [record.as_dict() | {"score": round(float(score), 6)} for score, record in ranked[:max(1, limit)]]
    return {"match": None, "candidates": candidates}


def _strip_scrf(route: str) -> str:
    """Remove every Gaussian SCRF option, including balanced parentheses."""
    result: list[str] = []
    cursor = 0
    pattern = re.compile(r"\bSCRF\b", re.IGNORECASE)
    while True:
        match = pattern.search(route, cursor)
        if match is None:
            result.append(route[cursor:])
            break
        result.append(route[cursor:match.start()])
        index = match.end()
        while index < len(route) and route[index].isspace():
            index += 1
        has_value = index < len(route) and route[index] in "=("
        if not has_value:
            cursor = match.end()
            continue
        if index < len(route) and route[index] == "=":
            index += 1
            while index < len(route) and route[index].isspace():
                index += 1
        if index < len(route) and route[index] == "(":
            depth = 0
            while index < len(route):
                depth += route[index] == "("
                depth -= route[index] == ")"
                index += 1
                if depth == 0:
                    break
        else:
            while index < len(route) and not route[index].isspace():
                index += 1
        cursor = index
    return re.sub(r"\s{2,}", " ", "".join(result)).strip()


def render_scrf(solvent_name: object, project_root: str | Path | None = None) -> tuple[str, str, dict[str, Any]]:
    """Return route text, trailing Gaussian input, and an audit snapshot."""
    if isinstance(solvent_name, str) and solvent_name.strip().casefold() == GAS_SOLVENT:
        return "", "", {"name": GAS_SOLVENT, "source": "none", "manual": False}
    record = resolve_exact(solvent_name, project_root)
    if record is None:
        raise SMDSolventError(f"未登记的 Gaussian 溶剂: {solvent_name}")
    if record.is_manual:
        route = "SCRF=(SMD,Solvent=Generic,Read)"
        trailer = f"Eps={record.epsilon}\nEpsInf={record.epsinf}\n"
    else:
        route = f"SCRF=(SMD,Solvent={record.name})"
        trailer = ""
    return route, trailer, record.as_dict()


def render_scrf_snapshot(
    solvent_name: object,
    snapshot: Mapping[str, Any] | None,
    project_root: str | Path | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Render a bound plan snapshot, falling back to the named catalog entry."""
    if snapshot is None:
        return render_scrf(solvent_name, project_root)
    if not isinstance(snapshot, Mapping) or not snapshot.get("name"):
        raise SMDSolventError("solvent_ref 必须是完整溶剂快照")
    name = str(snapshot["name"])
    if _name_key(name) != _name_key(solvent_name):
        raise SMDSolventError("solvent 与 solvent_ref 名称不一致")
    source = str(snapshot.get("source", ""))
    if name.casefold() == GAS_SOLVENT and source == "none":
        return "", "", {"name": GAS_SOLVENT, "source": "none", "manual": False}
    if source == "manual":
        epsilon, epsinf = validate_custom_values(snapshot.get("epsilon"), snapshot.get("epsinf"))
        record = SMDSolvent(name, epsilon, epsinf, "manual")
        return "SCRF=(SMD,Solvent=Generic,Read)", f"Eps={epsilon}\nEpsInf={epsinf}\n", record.as_dict()
    if source != "builtin":
        raise SMDSolventError("solvent_ref.source 必须是 builtin、manual 或 gas 对应的 none")
    record = resolve_exact(name, project_root)
    if record is None or record.source != "builtin":
        raise SMDSolventError(f"内置 Gaussian 溶剂快照无法验证: {name}")
    return f"SCRF=(SMD,Solvent={record.name})", "", record.as_dict()


def apply_scrf(route: str, solvent_name: object, project_root: str | Path | None = None) -> tuple[str, str, dict[str, Any]]:
    rendered_route, trailer, snapshot = render_scrf(solvent_name, project_root)
    clean_route = _strip_scrf(route)
    if rendered_route:
        clean_route = f"{clean_route} {rendered_route}".strip()
    return clean_route, trailer, snapshot


def apply_scrf_snapshot(
    route: str,
    solvent_name: object,
    snapshot: Mapping[str, Any] | None,
    project_root: str | Path | None = None,
) -> tuple[str, str, dict[str, Any]]:
    rendered_route, trailer, record = render_scrf_snapshot(solvent_name, snapshot, project_root)
    clean_route = _strip_scrf(route)
    if rendered_route:
        clean_route = f"{clean_route} {rendered_route}".strip()
    return clean_route, trailer, record


def has_scrf(route: str) -> bool:
    return bool(re.search(r"\bSCRF\b", route or "", re.IGNORECASE))


def input_has_scrf(path: str | Path) -> bool:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return has_scrf(gaussian_route(text))


def gaussian_route(text: str) -> str:
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if line.lstrip().startswith("#")), None)
    if start is None:
        return ""
    route = []
    for line in lines[start:]:
        if not line.strip():
            break
        route.append(line.strip())
    return " ".join(route)
