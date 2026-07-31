"""
toolist_global.py
=================
Layer 0 — Config Agent 的 10 个工具定义与处理函数 + TF-IDF 向量检索。

工具:
  tools_lookup_molecule, tools_resolve_compound, tools_lookup_md_defaults,
  tools_get_box_density, tools_lookup_basis_set, tools_refresh_structs,
  tools_diagnose_error_config, tools_validate_config,
  tools_set_backend_quantum, tools_skip_molecule_global
"""

from willy.llm_config import _available_residues



TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tools_lookup_molecule",
            "description": "查询分子的电荷、自旋、推荐基组、力场和中文别名。输入分子名或中文名均可。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "分子名或中文别名，如 Li, TFSI, 硝酸根, 锂离子, LiTFSI"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tools_resolve_compound",
            "description": "将化合物名（如 LiTFSI, LiPF6, 硝酸锂）拆分为组成离子及其比例。",
            "parameters": {
                "type": "object",
                "properties": {
                    "compound": {
                        "type": "string",
                        "description": "化合物名，如 LiTFSI, LiPF6, LiNO3, 硝酸锂, 六氟磷酸锂"
                    }
                },
                "required": ["compound"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tools_lookup_md_defaults",
            "description": "获取 MD 模拟的默认参数：温度、时间、压强、时间步长、热浴、压浴等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "system_type": {
                        "type": "string",
                        "description": "体系类型: ionic_liquid(离子液体), solvent_mix(溶剂混合), aqueous(水溶液), organic(纯有机)",
                        "enum": ["ionic_liquid", "solvent_mix", "aqueous", "organic"]
                    }
                },
                "required": ["system_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tools_get_box_density",
            "description": "根据体系类型推荐 Packmol 盒子填充密度 (分子/nm³)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "system_type": {
                        "type": "string",
                        "description": "体系类型",
                        "enum": ["ionic_liquid", "solvent_mix", "aqueous", "organic"]
                    },
                    "has_ions": {"type": "boolean", "description": "是否含离子"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tools_lookup_basis_set",
            "description": "根据分子大小推荐 Gaussian 计算基组。",
            "parameters": {
                "type": "object",
                "properties": {
                    "atom_count": {"type": "integer", "description": "原子数"},
                    "has_metal": {"type": "boolean", "description": "是否含过渡金属"}
                },
                "required": ["atom_count"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tools_refresh_structs",
            "description": "重新扫描 struct/ 目录和 knowledge.md，刷新可用分子列表。用户上传新结构后调用此工具更新。返回当前所有可用分子。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tools_diagnose_error_config",
            "description": "根据错误信息查询修复方案。用于配置阶段的常见问题诊断。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symptom": {
                        "type": "string",
                        "description": "错误现象，如 temperature_explosion, packmol_fail, grompp_atomtype, sched_affinity, sobtop_rc24"
                    }
                },
                "required": ["symptom"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tools_validate_config",
            "description": "验证草稿 config.json 是否合法。检查 residues 中的每个分子在 molecules 中是否有定义、参数值是否在合理范围内、必填字段是否齐全。返回问题列表（空列表 = 通过验证）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "config_json": {
                        "type": "string",
                        "description": "完整的 config.json 内容（JSON 字符串），含 molecules, residues, md, defaults 四个段。"
                    }
                },
                "required": ["config_json"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tools_set_backend_quantum",
            "description": "设置量子化学计算后端。用户说'用 ORCA'、'换高斯'、'用 G16' 时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "backend": {
                        "type": "string",
                        "enum": ["g16", "orca"],
                        "description": "量子化学后端: g16=Gaussian 16, orca=ORCA",
                    }
                },
                "required": ["backend"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tools_skip_molecule_global",
            "description": "将指定分子加入跳过列表，流水线将排除此分子。用户说'跳过 XXX''移除 XXX'时调用。可在配置阶段或流水线运行前使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "molecule_name": {
                        "type": "string",
                        "description": "要跳过的分子名",
                    },
                    "reason": {
                        "type": "string",
                        "description": "跳过原因",
                    },
                },
                "required": ["molecule_name"],
            },
        },
    },
]

# ============================================================
# Tool 分类元数据
# ============================================================

TOOL_META = {
    "tools_lookup_molecule":       {"category": "query",      "mutating": False, "risk": "low"},
    "tools_resolve_compound":      {"category": "query",      "mutating": False, "risk": "low"},
    "tools_lookup_md_defaults":    {"category": "query",      "mutating": False, "risk": "low"},
    "tools_get_box_density":       {"category": "query",      "mutating": False, "risk": "low"},
    "tools_lookup_basis_set":      {"category": "query",      "mutating": False, "risk": "low"},
    "tools_refresh_structs":       {"category": "refresh",    "mutating": False, "risk": "low"},
    "tools_diagnose_error_config": {"category": "diagnostic", "mutating": False, "risk": "low"},
    "tools_validate_config":       {"category": "validation", "mutating": False, "risk": "low"},
    "tools_set_backend_quantum":      {"category": "config",     "mutating": True,  "risk": "medium"},
    "tools_skip_molecule_global":     {"category": "config",     "mutating": True,  "risk": "medium"},
}

# ============================================================
# 向量检索器 —— 从 knowledge.md 自动提取分子库
# ============================================================

import re as _re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# 硬编码兜底（knowledge.md 不可用时的最小集合）
_FALLBACK_MOLECULES = {
    "Li":   {"charge": 1,  "spin": 1, "atom_count": 1,  "basis": "b3lyp/6-311+g(d,p)", "forcefield": "UFF"},
    "TFSI": {"charge": -1, "spin": 1, "atom_count": 15, "basis": "b3lyp/6-311+g(d,p)", "forcefield": "GAFF"},
    "NO3":  {"charge": -1, "spin": 1, "atom_count": 4,  "basis": "b3lyp/6-311+g(d,p)", "forcefield": "GAFF"},
}

class _MoleculeRegistry:
    """从 knowledge.md 自动解析分子库，TF-IDF 语义检索。"""

    def __init__(self):
        self._names = []
        self._data = {}
        self._vectorizer = TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 4))
        self._matrix = None
        self._load()

    def _load(self):
        from willy._paths import get_project_root
        path = get_project_root() / "docs" / "knowledge.md"
        if not path.exists():
            self._data = dict(_FALLBACK_MOLECULES)
            self._names = list(self._data.keys())
            return

        text = path.read_text()
        # 匹配 knowledge.md 中的分子表格行: | Li | +1 | 1 | 1 | ... | 锂离子、锂盐 |
        rows = _re.findall(
            r'\|\s*(\w+)\s*\|\s*([+-]?\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([\w/+\(\)\-,]+)\s*\|\s*(\w+)\s*\|\s*([^|]*)\|',
            text
        )
        for name, charge, spin, atom_count, basis, forcefield, aliases_raw in rows:
            aliases = [a.strip() for a in aliases_raw.replace('、', ',').split(',') if a.strip()]
            aliases.append(name)  # 自身名也加入检索
            self._data[name] = {
                "charge": int(charge), "spin": int(spin),
                "atom_count": int(atom_count), "basis": basis.strip(),
                "forcefield": forcefield.strip(), "aliases": aliases,
            }
            self._names.append(name)

        # 补充 struct/ 下的 .gjf 文件（未在 knowledge.md 中注册的，默认中性分子）
        struct_dir = get_project_root() / "struct"
        if struct_dir.exists():
            for gjf in struct_dir.glob("*.gjf"):
                name = gjf.stem
                if name.endswith("_run") or name in self._data:
                    continue
                self._data[name] = {
                    "charge": 0, "spin": 1, "atom_count": 0,
                    "basis": "b3lyp/6-311+g(d,p)", "forcefield": "GAFF",
                    "aliases": [name],
                }
                self._names.append(name)

        # 补充硬编码兜底条目
        for name, info in _FALLBACK_MOLECULES.items():
            if name not in self._data:
                self._data[name] = dict(info)
                self._data[name]["aliases"] = [name]
                self._names.append(name)

        # 构建 TF-IDF 索引（每个分子用别名列表拼接）
        if self._names:
            docs = [", ".join(self._data[n].get("aliases", [n])) for n in self._names]
            self._matrix = self._vectorizer.fit_transform(docs)

    def lookup(self, query: str, threshold: float = 0.55) -> dict | None:
        """检索分子信息：先精确匹配，失败再语义检索。返回 None 表示未找到。"""
        q = query.strip()
        # ① 精确匹配：名称本身
        if q in self._data:
            info = self._data[q]
            return {"name": q, "charge": info["charge"], "spin": info["spin"],
                    "atom_count": info["atom_count"], "basis": info["basis"],
                    "forcefield": info["forcefield"], "aliases": info["aliases"]}
        # ② 精确匹配：中文别名
        for name, info in self._data.items():
            if q in info.get("aliases", []):
                return {"name": name, "charge": info["charge"], "spin": info["spin"],
                        "atom_count": info["atom_count"], "basis": info["basis"],
                        "forcefield": info["forcefield"], "aliases": info["aliases"]}
        # ③ 向量语义检索（兜底，提高阈值防误匹配）
        if self._matrix is None or self._matrix.shape[0] == 0:
            return None
        vec = self._vectorizer.transform([q])
        sims = cosine_similarity(vec, self._matrix)[0]
        best_idx = sims.argmax()
        if sims[best_idx] < threshold:
            return None
        name = self._names[best_idx]
        info = self._data[name]
        return {"name": name, "charge": info["charge"], "spin": info["spin"],
                "atom_count": info["atom_count"], "basis": info["basis"],
                "forcefield": info["forcefield"], "aliases": info["aliases"]}

    def get_all_names(self) -> list[str]:
        return list(self._names)

_registry = _MoleculeRegistry()
_MOLECULES = _registry  # 兼容旧名字

_COMPOUNDS = {
    "LiTFSI":       [("Li", 1), ("TFSI", 1)],
    "LiPF6":        [("Li", 1), ("PF6", 1)],
    "LiNO3":        [("Li", 1), ("NO3", 1)],
    "硝酸锂":       [("Li", 1), ("NO3", 1)],
    "六氟磷酸锂":   [("Li", 1), ("PF6", 1)],
}

_ERRORS = {
    "temperature_explosion": "NPT 盒子崩塌：增大密度 (≥6/nm³) + 先跑 100ps NVT + 退火从 298K 起",
    "packmol_fail": "盒子太小 → 降低密度或手动设更大 box_size",
    "grompp_atomtype": "itp 未过滤 [atomtypes] → 运行 top_assembly (自动调 itp_revise)",
    "sched_affinity": "WSL2: 注入 GAUSS_CDEF=0 OMP_NUM_THREADS=1",
    "sobtop_rc24": "非致命 Fortran 清理错误，输出文件正常即可忽略",
    "scf_not_converged": "改 6-31g(d) 或加 scf=xqc",
    "em_not_converged": "初始原子重叠 → 增大 packmol tolerance 或盒子",
    "resp_failed": "检查 Multiwfn/Gaussian 是否在 PATH，检查 .gjf 格式",
}


def _resolve_name(name: str) -> str | None:
    """将中文别名或变体映射到标准名（TF-IDF 语义检索）。"""
    result = _registry.lookup(name.strip())
    return result["name"] if result else None


# ============================================================
# 工具调度
# ============================================================

def handle_tool_call(tool_name: str, args: dict) -> str:
    """
    根据 LLM 发来的 tool_call 返回 JSON 结果。

    供 chat_fn 在 tool_calls 循环中使用。
    """
    import json as _json

    if tool_name == "tools_lookup_molecule":
        result = _registry.lookup(args["name"])
        if result:
            # 判断分子类型 (cation/anion/solvent)
            chg = result["charge"]
            mol_type = "cation" if chg > 0 else ("anion" if chg < 0 else "solvent")
            return _json.dumps({
                "name": result["name"], "charge": chg, "spin": result["spin"],
                "atom_count": result["atom_count"], "basis": result["basis"],
                "forcefield": result["forcefield"], "type": mol_type,
                "aliases": result["aliases"][:5],  # 只展示前5个别名
            }, ensure_ascii=False)
        # 尝试作为化合物解析
        if args["name"] in _COMPOUNDS:
            return _json.dumps({
                "name": args["name"], "is_compound": True,
                "hint": f"请调用 tools_resolve_compound('{args['name']}') 拆分为离子",
            }, ensure_ascii=False)
        return _json.dumps({"error": f"未找到: {args['name']}",
                            "available": ", ".join(_registry.get_all_names()),
                            "known_compounds": ", ".join(_COMPOUNDS.keys())},
                           ensure_ascii=False)

    elif tool_name == "tools_resolve_compound":
        compound = args["compound"].strip()
        result = _COMPOUNDS.get(compound)
        if result:
            ions = [{"name": name, "count": n} for name, n in result]
            total_charge = sum(
                (_registry.lookup(name) or {}).get("charge", 0) * n
                for name, n in result)
            return _json.dumps({"compound": compound, "ions": ions,
                                "total_charge": total_charge,
                                "note": f"拆分为 {', '.join(f'{name}×{n}' for name,n in result)}"},
                               ensure_ascii=False)
        return _json.dumps({"error": f"未知化合物: {compound}",
                            "hint": f"已知: {', '.join(_COMPOUNDS.keys())}"}, ensure_ascii=False)

    elif tool_name == "tools_lookup_md_defaults":
        st = args.get("system_type", "solvent_mix")
        defaults = {
            "ionic_liquid": {"ref_t": 298, "prod_ns": 10, "eq_ns": 5, "dt": 0.001,
                             "tcoupl": "V-rescale", "tau_t": 0.5, "pcoupl": "C-rescale",
                             "note": "含 Li 体系 dt=1fs"},
            "solvent_mix":  {"ref_t": 298, "prod_ns": 10, "eq_ns": 5, "dt": 0.001,
                             "tcoupl": "V-rescale", "tau_t": 0.5, "pcoupl": "C-rescale"},
            "aqueous":      {"ref_t": 298, "prod_ns": 10, "eq_ns": 5, "dt": 0.002,
                             "tcoupl": "V-rescale", "tau_t": 0.5, "pcoupl": "C-rescale"},
            "organic":      {"ref_t": 298, "prod_ns": 10, "eq_ns": 5, "dt": 0.002,
                             "tcoupl": "V-rescale", "tau_t": 0.5, "pcoupl": "C-rescale"},
        }
        return _json.dumps(defaults.get(st, defaults["solvent_mix"]))

    elif tool_name == "tools_get_box_density":
        has_ions = args.get("has_ions", True)
        st = args.get("system_type", "")
        if st == "ionic_liquid": d = 6.0
        elif st == "solvent_mix" and has_ions: d = 5.0
        elif st == "aqueous": d = 3.0
        elif st == "organic": d = 4.0
        else: d = 5.0
        formula = f"box = ceil(∛(N / {d}) × 10) Å"
        return _json.dumps({"density": d, "unit": "molecules/nm³", "formula": formula})

    elif tool_name == "tools_lookup_basis_set":
        n = args.get("atom_count", 10)
        has_metal = args.get("has_metal", False)
        if has_metal: rec = "b3lyp/def2TZVP"
        elif n < 20: rec = "b3lyp/6-311+g(d,p)"
        else: rec = "b3lyp/6-31g(d)"
        return _json.dumps({"recommended": rec, "default": "b3lyp/6-311+g(d,p)"})

    elif tool_name == "tools_refresh_structs":
        _registry._load()
        names = _registry.get_all_names()
        return _json.dumps({"ok": True, "count": len(names), "molecules": names})

    elif tool_name == "tools_diagnose_error_config":
        sym = args.get("symptom", "")
        fix = _ERRORS.get(sym, "查看 knowledge.md §九 获取详细诊断")
        from willy.errors import DiagnosisResult
        dr = DiagnosisResult(
            source="config",
            severity="error" if sym in _ERRORS else "warning",
            issues=[f"症状: {sym}"] if sym else [],
            evidence=[],
            hint=fix,
            extra={"symptom": sym},
        )
        return _json.dumps(dr.to_dict(), ensure_ascii=False)

    elif tool_name == "tools_validate_config":
        try:
            cfg = _json.loads(args["config_json"]) if isinstance(args["config_json"], str) else args["config_json"]
        except (_json.JSONDecodeError, TypeError):
            return _json.dumps({"valid": False, "issues": ["JSON 解析失败——检查格式是否正确"]})
        from willy.llm_config import validate_config
        issues = validate_config(cfg)
        if issues:
            return _json.dumps({"valid": False, "issues": issues})
        # 额外检查: 净电荷
        molecules = cfg.get("molecules", {})
        residues = cfg.get("residues", {})
        net = 0
        for name, count in residues.items():
            mol = molecules.get(name, {})
            net += count * mol.get("charge", 0)
        extra = {}
        if abs(net) > 0.5:
            extra["charge_imbalance"] = f"净电荷 {net:+d}，体系不呈电中性"
        return _json.dumps({"valid": True, "issues": [], **extra})

    elif tool_name == "tools_set_backend_quantum":
        from willy._paths import get_project_root as _get_root
        backend = args["backend"]
        cfg_path = _get_root() / "config.json"
        try:
            if cfg_path.exists():
                cfg = _json.loads(cfg_path.read_text())
            else:
                cfg = {}
        except (_json.JSONDecodeError):
            cfg = {}
        cfg["backend"] = backend
        cfg_path.write_text(_json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
        return _json.dumps({
            "ok": True,
            "backend": backend,
            "note": f"量子化学后端已设为 {backend}",
        }, ensure_ascii=False)

    elif tool_name == "tools_skip_molecule_global":
        from willy._paths import get_project_root as _get_root
        name = args["molecule_name"]
        reason = args.get("reason", "用户指定跳过")
        cfg_path = _get_root() / "config.json"
        try:
            if cfg_path.exists():
                cfg = _json.loads(cfg_path.read_text())
            else:
                cfg = {}
        except (_json.JSONDecodeError):
            cfg = {}
        skipped = cfg.setdefault("skipped_molecules", [])
        if name not in skipped:
            skipped.append(name)
        cfg.setdefault("skip_reasons", {})[name] = reason
        cfg_path.write_text(_json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
        return _json.dumps({
            "ok": True,
            "molecule": name,
            "reason": reason,
            "warning": f"⚠ {name} 已加入跳过列表，流水线将排除此分子",
        }, ensure_ascii=False)

    return _json.dumps({"error": f"未知工具: {tool_name}"})
