"""Manifest-driven GROMACS master topology assembly."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import shutil

from willy._paths import get_project_root
from willy.errors import ErrorKind, StepError, StepResult
from willy.topology.backends import normalize_topology_config
from willy.topology.manifest import (
    TopologyManifestComponent,
    load_manifest,
    manifest_path,
    write_manifest,
)
from willy.topology.validation import run_output_path, validate_topology_files


ROOT = get_project_root()
CONFIG_PATH = ROOT / "config.json"

_FORCEFIELD_TEMPLATES = {
    "gaff_uff": {
        "defines": ("#define GAFF", "#define UFF"),
        "defaults": "1 3 yes 0.5 0.5",
    },
    "oplsaa": {
        "defines": ("#define OPLSAA",),
        "defaults": "1 3 yes 0.5 0.5",
    },
}
_ASSEMBLY_ITP_DIRNAME = ".assembly_itp"


class AssemblyValidationError(ValueError):
    def __init__(self, message: str, kind: ErrorKind = ErrorKind.CONFIG_INVALID):
        super().__init__(message)
        self.kind = kind


class TopConfig:
    """The assembly-relevant portion of a config snapshot."""

    def __init__(self, residues: dict[str, int] | None = None):
        self.residues = residues or {}

    @classmethod
    def from_json(cls, path: str = "config.json") -> "TopConfig":
        return cls(residues=json.loads(Path(path).read_text()).get("residues", {}))


def _section_name(line: str) -> str | None:
    stripped = line.strip()
    if not (stripped.startswith("[") and "]" in stripped):
        return None
    return stripped[1:stripped.index("]")].strip().lower()


def _extract_atomtypes(itp_path: Path) -> list[str]:
    """Extract data rows until the next section, preserving valid parameters."""
    in_section = False
    entries: list[str] = []
    for line in itp_path.read_text().splitlines():
        section = _section_name(line)
        if section is not None:
            if section == "atomtypes":
                in_section = True
                continue
            if in_section:
                break
        if not in_section:
            continue
        data = line.split(";", 1)[0].strip()
        if data and not data.startswith("#"):
            entries.append(data)
    return entries


def _collect_dedup_atomtypes(components: list[dict[str, Any]]) -> list[str]:
    """Deduplicate only identical atom types and reject parameter conflicts."""
    by_name: dict[str, tuple[str, ...]] = {}
    lines: list[str] = []
    referenced_types: set[str] = set()
    for component in components:
        itp_path = Path(component["itp"])
        for entry in _extract_atomtypes(itp_path):
            params = tuple(entry.split())
            if len(params) < 2:
                raise AssemblyValidationError(f"atomtype 定义不完整: {itp_path.name}: {entry}")
            name = params[0]
            previous = by_name.get(name)
            if previous is None:
                by_name[name] = params
                lines.append(entry)
            elif previous != params:
                raise AssemblyValidationError(
                    f"atomtype 参数冲突: {name} 在多个 ITP 中定义不同（{itp_path.name}）",
                    ErrorKind.ATOMTYPE_CONFLICT,
                )
        referenced_types.update(_extract_atoms_type_references(itp_path))
    if not lines:
        raise AssemblyValidationError("所有启用 ITP 均未定义 [ atomtypes ]")
    missing = sorted(referenced_types - by_name.keys())
    if missing:
        raise AssemblyValidationError(
            "[ atoms ] 引用了未定义 atomtype: " + ", ".join(missing)
        )
    return lines


def _extract_atoms_type_references(itp_path: Path) -> set[str]:
    """Return atom type names referenced by legal ``[ atoms ]`` data rows."""
    in_atoms = False
    referenced: set[str] = set()
    for line in itp_path.read_text().splitlines():
        section = _section_name(line)
        if section is not None:
            if section == "atoms":
                in_atoms = True
                continue
            if in_atoms:
                break
        if not in_atoms:
            continue
        fields = line.split(";", 1)[0].split()
        if (
            len(fields) >= 8
            and fields[0].lstrip("+-").isdigit()
            and fields[2].lstrip("+-").isdigit()
        ):
            referenced.add(fields[1])
    return referenced


def _assembly_itp_dir(topo_dir: Path) -> Path:
    """Create and validate the private directory for post-processed ITPs."""
    root = topo_dir.resolve()
    directory = root / _ASSEMBLY_ITP_DIRNAME
    directory.mkdir(exist_ok=True)
    if not directory.is_dir() or directory.resolve().parent != root:
        raise AssemblyValidationError(".assembly_itp 必须是当前 run_dir 内的普通目录")
    return directory.resolve()


def _assembly_itp_path(component: dict[str, Any], topo_dir: Path) -> Path:
    """Derive the independent, post-processed ITP path for one component."""
    try:
        return run_output_path(_assembly_itp_dir(topo_dir), str(component["residue_name"]), ".itp")
    except (KeyError, ValueError) as exc:
        raise AssemblyValidationError(f"非法组装 ITP 输出名: {exc}") from exc


def _write_manifest_data(topo_dir: Path, manifest: dict[str, Any]) -> None:
    components = [TopologyManifestComponent(**component) for component in manifest["components"]]
    write_manifest(
        topo_dir,
        backend=manifest["backend"],
        forcefield_family=manifest["forcefield_family"],
        components=components,
        retry_ledger=manifest.get("retry_ledger", {}),
    )


def _prepare_assembly_itps(
    topo_dir: Path,
    manifest: dict[str, Any],
    components: list[dict[str, Any]],
) -> int:
    """Copy source ITPs and revise only the copies used by ``topol.top``."""
    from willy.topology.itp_revise import revise_itp

    revised = 0
    for component in components:
        source = Path(component["itp"])
        assembly_itp = _assembly_itp_path(component, topo_dir)
        shutil.copy2(source, assembly_itp)
        revised += revise_itp(str(assembly_itp), residue_name=component["residue_name"])
        component["assembly_itp"] = str(assembly_itp)
    _write_manifest_data(topo_dir, manifest)
    return revised


def _validated_manifest_components(
    manifest: dict[str, Any],
    config: TopConfig,
    topo_dir: Path,
    topology: dict[str, Any],
) -> list[dict[str, Any]]:
    backend = manifest.get("backend")
    family = manifest.get("forcefield_family")
    if backend not in {"sobtop", "oplsaa"} or family not in _FORCEFIELD_TEMPLATES:
        raise AssemblyValidationError("topology_manifest.json 的后端或力场族无效")
    if topology["backend"] != backend or topology["force_field"] != family:
        raise AssemblyValidationError("配置快照与 topology_manifest.json 的后端/力场族不一致")

    manifest_components = manifest.get("components")
    if not isinstance(manifest_components, list):
        raise AssemblyValidationError("topology_manifest.json 缺少 components 列表")
    by_residue = {component.get("residue_name"): component for component in manifest_components}
    enabled: list[dict[str, Any]] = []
    for residue_name, quantity in config.residues.items():
        if not isinstance(quantity, int) or quantity <= 0:
            raise AssemblyValidationError(f"residues.{residue_name} 必须是正整数")
        component = by_residue.get(residue_name)
        if component is None:
            raise AssemblyValidationError(f"manifest 缺少启用残基 {residue_name}")
        if not component.get("success") or not component.get("validated"):
            raise AssemblyValidationError(f"manifest 中 {residue_name} 尚未成功并通过产物校验")
        if component.get("backend") != backend or component.get("forcefield_family") != family:
            raise AssemblyValidationError("检测到跨后端或跨力场族混用，不能组装同一 topol.top")
        for key in ("itp", "gro"):
            value = component.get(key)
            if not value:
                raise AssemblyValidationError(f"manifest 中 {residue_name} 缺少 {key}")
            path = Path(value)
            if not path.is_file():
                raise AssemblyValidationError(f"manifest 中 {residue_name} 的 {key} 不存在: {path}")
            if path.resolve().parent != topo_dir.resolve():
                raise AssemblyValidationError(f"{residue_name} 的 {key} 不在当前 run_dir/topology 目录")
        artifact_validation = validate_topology_files(
            component["itp"], component["gro"],
            step_name="top_assembly", step_index=5, error_kind=ErrorKind.CONFIG_INVALID,
            expected_moleculetype=residue_name,
        )
        if not artifact_validation.success:
            raise AssemblyValidationError(
                artifact_validation.error.message if artifact_validation.error else "manifest 产物校验失败",
                ErrorKind.CONFIG_INVALID,
            )
        enabled.append(component)

    if len({component.get("forcefield_family") for component in enabled}) != 1:
        raise AssemblyValidationError("检测到多个 forcefield_family，禁止混合组装")
    return enabled


def generate_top(
    config: TopConfig | None = None,
    config_path: str | None = None,
    topo_dir: str | None = None,
    output_path: str | None = None,
) -> Path:
    """Generate ``topol.top`` from validated components in the run manifest."""
    config_file = Path(config_path) if config_path is not None else CONFIG_PATH
    if topo_dir is None:
        raise AssemblyValidationError("top_assembly 必须提供当前 run 的 topo_dir")
    directory = Path(topo_dir)
    if config is None:
        config = TopConfig.from_json(str(config_file))
    if not config.residues:
        raise AssemblyValidationError("config.json 中 residues 为空")

    raw_config = json.loads(config_file.read_text())
    topology, issues, _ = normalize_topology_config(raw_config.get("topology", {}))
    if issues:
        raise AssemblyValidationError("; ".join(issues))
    if not manifest_path(directory).is_file():
        raise AssemblyValidationError(f"缺少 {manifest_path(directory).name}，Step 4 未产生可用拓扑产物")
    manifest = load_manifest(directory)
    components = _validated_manifest_components(manifest, config, directory, topology)
    family = manifest["forcefield_family"]
    template = _FORCEFIELD_TEMPLATES[family]
    atomtype_lines = _collect_dedup_atomtypes(components)
    assembly_itps: list[Path] = []
    for component in components:
        expected = _assembly_itp_path(component, directory)
        if component.get("assembly_itp") != str(expected) or not expected.is_file():
            raise AssemblyValidationError(
                f"{component['residue_name']} 缺少当前组装副本；请通过 top_assembly.build() 生成"
            )
        assembly_itps.append(expected)

    lines = [*template["defines"], "", "[ defaults ]", template["defaults"], ""]
    lines.extend(["[ atomtypes ]", "; name   atomtype parameters"])
    lines.extend(atomtype_lines)
    lines.append("")
    lines.extend(
        f'#include "{itp.relative_to(directory.resolve()).as_posix()}"'
        for itp in assembly_itps
    )
    lines.extend(["", "[ system ]", "-".join(config.residues), "", "[ molecules ]"])
    lines.extend(f"{name}   {count}" for name, count in config.residues.items())
    lines.append("")

    output = Path(output_path) if output_path is not None else directory / "topol.top"
    output.write_text("\n".join(lines))
    return output


def build(
    config_path: str | None = None,
    topo_dir: str | None = None,
    output_path: str | None = None,
) -> StepResult:
    """Assemble a master topology from sources and independent revised ITPs."""
    import time

    start = time.monotonic()
    config_file = Path(config_path) if config_path is not None else CONFIG_PATH
    if topo_dir is None:
        return StepResult(
            step_name="top_assembly", step_index=5, success=False,
            error=StepError(
                ErrorKind.INPUT_CONTRACT,
                "top_assembly 必须提供当前 run 的 topo_dir",
            ),
        )
    directory = Path(topo_dir)
    try:
        config = TopConfig.from_json(str(config_file))
        raw_config = json.loads(config_file.read_text())
        topology, issues, _ = normalize_topology_config(raw_config.get("topology", {}))
        if issues:
            raise AssemblyValidationError("; ".join(issues))
        manifest = load_manifest(directory)
        components = _validated_manifest_components(manifest, config, directory, topology)
        _collect_dedup_atomtypes(components)
        revised = _prepare_assembly_itps(directory, manifest, components)
        top_path = generate_top(config_path=str(config_file), topo_dir=str(directory), output_path=output_path)
    except AssemblyValidationError as exc:
        return StepResult(
            step_name="top_assembly", step_index=5, success=False,
            error=StepError(exc.kind, str(exc), hint="修复 manifest 记录的拓扑产物后重新执行 Step 4。"),
            duration_s=time.monotonic() - start,
        )
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return StepResult(
            step_name="top_assembly", step_index=5, success=False,
            error=StepError(ErrorKind.CONFIG_INVALID, f"主拓扑组装失败: {exc}"),
            duration_s=time.monotonic() - start,
        )

    return StepResult(
        step_name="top_assembly", step_index=5, success=True,
        outputs={"topol": str(top_path)},
        artifacts=[str(top_path), *(component["assembly_itp"] for component in components)],
        duration_s=time.monotonic() - start, extra={"itp_revised": revised},
    )
