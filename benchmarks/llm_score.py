"""llm_score.py — LLM config 准确性评分（20 条精选用例，支持并行）"""

import json, sys, time, re
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from openai import OpenAI
from willy.toolist_global import TOOLS, handle_tool_call, _MOLECULES as _REGISTRY

# _j() 对齐 app.py: 支持 ```json / ```yaml / ``` / 裸 JSON / yaml
def _j(raw):
    for tag in ("```json","```yaml","```"):
        if tag in raw:
            txt = raw.split(tag)[1].split("```")[0].strip()
            try: return json.loads(txt)
            except json.JSONDecodeError:
                try:
                    import yaml
                    return yaml.safe_load(txt)
                except Exception:
                    pass
    try: return json.loads(raw)
    except json.JSONDecodeError:
        import yaml; return yaml.safe_load(raw)

CLIENT = OpenAI(
    api_key=open(Path(__file__).parent.parent / ".env").read()
        .split("DEEPSEEK_API_KEY=")[1].split("\n")[0].strip().strip('"'),
    base_url="https://api.deepseek.com",
)

# ============================================================
# 20 条用例：中文 / 英文 / 中英混杂 / 化合物 / 模糊 / 配对 / 多体系
# ============================================================

TEST_CASES = [
    # ── 基础英文 ──
    {
        "id": 1,
        "input": "Li 100, TFSI 100, DME 500, 330K, 15ns eq, 30ns prod",
        "expected": {"residues": {"Li": 100, "TFSI": 100, "DME": 500},
                      "md": {"ref_t": 330, "prod_ns": 30, "eq_ns": 15}},
    },
    {
        "id": 2,
        "input": "100 Li+, 200 FEC, 50 NO3-, 400K, 50ns production",
        "expected": {"residues": {"Li": 100, "FEC": 200, "NO3": 50},
                      "md": {"ref_t": 400, "prod_ns": 50}},
    },
    # ── 基础中文 ──
    {
        "id": 3,
        "input": "50个锂离子, 50个硝酸根, 200个氟代碳酸乙烯酯, 350K, 20纳秒",
        "expected": {"residues": {"Li": 50, "NO3": 50, "FEC": 200},
                      "md": {"ref_t": 350, "prod_ns": 20}},
    },
    {
        "id": 4,
        "input": "六氟磷酸根40个 锂离子40个 碳酸乙烯酯300个 350K 15ns",
        "expected": {"residues": {"PF6": 40, "Li": 40, "EC": 300},
                      "md": {"ref_t": 350, "prod_ns": 15}},
    },
    # ── 化合物拆分 ──
    {
        "id": 5,
        "input": "100个硝酸锂, 300个FEC溶剂, 350K, 20ns",
        "expected": {"residues": {"Li": 100, "NO3": 100, "FEC": 300},
                      "md": {"ref_t": 350, "prod_ns": 20}},
    },
    {
        "id": 6,
        "input": "LiTFSI 80个, LiPF6 20个, EC 400, EMC 200, 320K, 15ns",
        "expected": {"residues": {"Li": 100, "TFSI": 80, "PF6": 20, "EC": 400, "EMC": 200},
                      "md": {"ref_t": 320, "prod_ns": 15}},
    },
    {
        "id": 7,
        "input": "50 LiNO3, 30 LiTFSI, FEC 300, DME 200, 350K, 25ns",
        "expected": {"residues": {"Li": 80, "NO3": 50, "TFSI": 30, "FEC": 300, "DME": 200},
                      "md": {"ref_t": 350, "prod_ns": 25}},
    },
    # ── 中英混杂 + 模糊表达 ──
    {
        "id": 8,
        "input": "Li 50 FEC 250 EC 200, 还有TFSI大概50个吧, PF6也来30, 室温, 10ns够了",
        "expected": {"residues": {"Li": 50, "TFSI": 50, "PF6": 30, "FEC": 250, "EC": 200},
                      "md": {"ref_t": 298, "prod_ns": 10}},
    },
    # ── 模糊表达（需询问） ──
    {
        "id": 9,
        "input": "锂盐100 溶剂200, 350K, 20ns",
        "expected": None,  # 模糊：未指定具体盐/溶剂种类，应反询问
    },
    {
        "id": 10,
        "input": "Li FEC NO3-各100个, 300K, 10ns",
        "expected": {"residues": {"Li": 100, "FEC": 100, "NO3": 100},
                      "md": {"ref_t": 300, "prod_ns": 10}},  # 所有可用分子各100
    },
    # ── 配对表达 ──
    {
        "id": 11,
        "input": "Li和FEC各200个, NO3 50个, 350K, 20ns",
        "expected": {"residues": {"Li": 200, "FEC": 200, "NO3": 50},
                      "md": {"ref_t": 350, "prod_ns": 20}},
    },
    {
        "id": 12,
        "input": "EC 和 DME 分别 300 和 150 个, Li 60, TFSI 60, 320K, 18ns",
        "expected": {"residues": {"EC": 300, "DME": 150, "Li": 60, "TFSI": 60},
                      "md": {"ref_t": 320, "prod_ns": 18}},
    },
    {
        "id": 13,
        "input": "Li和TFSI各50, PF6和NO3各25, FEC 200, EMC 100, 310K, 12ns",
        "expected": {"residues": {"Li": 50, "TFSI": 50, "PF6": 25, "NO3": 25, "FEC": 200, "EMC": 100},
                      "md": {"ref_t": 310, "prod_ns": 12}},
    },
    # ── 特殊格式 ──
    {
        "id": 14,
        "input": "Li=40, FEC=120, NO3=40, T=298K, t=5ns, p=1bar, dt=1fs",
        "expected": {"residues": {"Li": 40, "FEC": 120, "NO3": 40},
                      "md": {"ref_t": 298, "prod_ns": 5, "ref_p": 1.0, "dt": 0.001}},
    },
    {
        "id": 15,
        "input": "Li:80 TFSI:80 FEC:300 DMM:100, temp:320, time:30ns",
        "expected": {"residues": {"Li": 80, "TFSI": 80, "FEC": 300, "DMM": 100},
                      "md": {"ref_t": 320, "prod_ns": 30}},
    },
    # ── 英文嘈杂 ──
    {
        "id": 16,
        "input": "I'd like to simulate 40 lithium, 40 TFSI, plus 20 extra PF6, with 200 EC and 100 EMC, at 315K for 14ns",
        "expected": {"residues": {"Li": 40, "TFSI": 40, "PF6": 20, "EC": 200, "EMC": 100},
                      "md": {"ref_t": 315, "prod_ns": 14}},
    },
    {
        "id": 17,
        "input": "Using EC:EMC:DME = 2:1:1 ratio, about 600 total solvent, with 60 LiTFSI, at 310K for 12ns",
        "expected": {"residues": {"Li": 60, "TFSI": 60, "EC": 300, "EMC": 150, "DME": 150},
                      "md": {"ref_t": 310, "prod_ns": 12}},
    },
    # ── 多体系（只返回首个） ──
    {
        "id": 18,
        "input": "先跑A体系: 100 Li, 100 NO3, 100 FEC, 300K, 10ns; 结束后再跑B体系: 100 Li, 100 NO3, 250 FEC, 350K, 20ns",
        "expected": {"residues": {"Li": 100, "NO3": 100, "FEC": 100},
                      "md": {"ref_t": 300, "prod_ns": 10}},  # 只返回第一个
    },
    # ── 缺省值测试 ──
    {
        "id": 19,
        "input": "Li 50 FEC 100",
        "expected": {"residues": {"Li": 50, "FEC": 100},
                      "md": {"ref_t": 298, "prod_ns": 10}},
    },
    # ── 后端选择 ──
    {
        "id": 24,
        "input": "用 ORCA 算 Li 50, TFSI 50, EC 200, 300K, 10ns",
        "expected": {"residues": {"Li": 50, "TFSI": 50, "EC": 200},
                      "md": {"ref_t": 300, "prod_ns": 10}},
    },
    # ── 纯溶剂 + 混合阴离子 ──
    {
        "id": 20,
        "input": "纯溶剂测试: EC 500, DME 300, EMC 200, 不加盐, 298K, 5ns",
        "expected": {"residues": {"EC": 500, "DME": 300, "EMC": 200},
                      "md": {"ref_t": 298, "prod_ns": 5}},
    },
    # ── 预期电荷不平衡（LLM 应准确提取数值 + 标注警告） ──
    {
        "id": 21,
        "input": "Li 100 TFSI 50, FEC 300, 350K, 20ns",
        "expected": {"residues": {"Li": 100, "TFSI": 50, "FEC": 300},
                      "md": {"ref_t": 350, "prod_ns": 20}},
    },
    {
        "id": 22,
        "input": "Li 30 PF6 50, EC 200, 300K, 10ns",
        "expected": {"residues": {"Li": 30, "PF6": 50, "EC": 200},
                      "md": {"ref_t": 300, "prod_ns": 10}},
    },
    {
        "id": 23,
        "input": "Li 80 NO3 50 PF6 20, FEC 400, 320K, 15ns",
        "expected": {"residues": {"Li": 80, "NO3": 50, "PF6": 20, "FEC": 400},
                      "md": {"ref_t": 320, "prod_ns": 15}},
    },
]


# ============================================================
# 评分逻辑
# ============================================================

def score_structure(parsed: dict) -> tuple[int, list[str]]:
    score = 5; issues = []
    for k in ["backend", "molecules", "residues", "md", "defaults"]:
        if k not in parsed:
            score -= 2; issues.append(f"缺失: {k}"); break
    for name in parsed.get("residues", {}):
        mol = parsed.get("molecules", {}).get(name, {})
        if not mol: score -= 2; issues.append(f"molecules 缺 {name}"); break
        for f in ["charge", "spin", "basis"]:
            if f not in mol: score -= 2; issues.append(f"{name} 缺 {f}"); break
    for f in ["ref_t", "prod_ns"]:
        if f not in parsed.get("md", {}): score -= 2; issues.append(f"md 缺 {f}")
    return max(1, score), issues


def score_values(parsed: dict, expected: dict, user_input: str = "") -> tuple[int, dict]:
    score = 5; fields = {}
    exp_r = expected.get("residues", {}); got_r = parsed.get("residues", {})
    exp_m = expected.get("md", {}); got_m = parsed.get("md", {})

    # ── residues 逐项比对 ──
    for name in set(list(exp_r) + list(got_r)):
        e, g = exp_r.get(name), got_r.get(name)
        if e == g: fields[f"res.{name}"] = "✅"
        elif g is None: fields[f"res.{name}"] = f"❌ miss(exp={e})"; score -= 1
        elif e is None: fields[f"res.{name}"] = f"❌ extra(got={g})"; score -= 1
        else: fields[f"res.{name}"] = f"❌ {g}≠{e}"; score -= 1

    # ── md 逐项比对 ──
    for k in ["ref_t", "prod_ns", "eq_ns", "dt", "ref_p"]:
        e = exp_m.get(k)
        if e is None: continue
        g = got_m.get(k)
        if g is None: fields[f"md.{k}"] = f"❌ miss(exp={e})"; score -= 1
        elif abs(g - e) < 1: fields[f"md.{k}"] = "✅"
        else: fields[f"md.{k}"] = f"❌ {g}≠{e}"; score -= 1

    # ── 净电荷评估（LLM 应正确查询 tools_lookup_molecule 获取电荷）──
    mols = parsed.get("molecules", {})
    net = 0
    for name, count in got_r.items():
        mol = mols.get(name, {})
        llm_charge = mol.get("charge", 0)
        # 对照知识库中的真实电荷
        known = _REGISTRY._data.get(name, {})
        if known:
            true_charge = known["charge"]
            if llm_charge != true_charge:
                fields[f"chg.{name}"] = f"❌ LLM={llm_charge}≠真实{true_charge}"
                score -= 1
            else:
                fields[f"chg.{name}"] = "✅"
            net += count * llm_charge
        else:
            net += count * llm_charge

    if abs(net) > 0.5:
        fields["net_charge"] = f"⚠ {net:+d}"  # 记录但不扣分（用户可能故意不平衡）
    else:
        fields["net_charge"] = "✅ 中性"

    return max(1, score), fields


# ============================================================
# 主流程
# ============================================================

def _eval_one(tc: dict) -> dict:
    """评估单条用例（供并行调用）。走完整 tool-based API 路径。"""
    exp = tc.get("expected")
    result = {"id": tc["id"], "input": tc["input"], "source": "api", "ok": True}

    if exp is None:
        result["ok"] = None  # 跳过模糊用例
        return result

    try:
        from willy.agent_config import get_system_prompt
        SYS = get_system_prompt()
    except Exception:
        SYS = "你是 MD 配置生成助手。必须用工具查询信息。输出 JSON。"

    try:
        msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": tc["input"]}]
        config_dict = None
        for _ in range(5):
            r = CLIENT.chat.completions.create(
                model="deepseek-v4-pro", messages=msgs, tools=TOOLS, temperature=0.1)
            msg = r.choices[0].message
            if msg.tool_calls:
                tcalls = [{"id": t.id, "type": "function",
                           "function": {"name": t.function.name, "arguments": t.function.arguments}}
                          for t in msg.tool_calls]
                msgs.append({"role": "assistant", "content": msg.content or "", "tool_calls": tcalls})
                for t in msg.tool_calls:
                    res = handle_tool_call(t.function.name, json.loads(t.function.arguments))
                    msgs.append({"role": "tool", "tool_call_id": t.id, "content": res})
            else:
                config_dict = _j(msg.content)
                if config_dict and config_dict.get("residues"):
                    break  # 成功
                # residues 空 → 继续
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)
        return result

    if not config_dict or not config_dict.get("residues"):
        result["ok"] = False
        result["error"] = "residues 为空"
        return result

    # ── 错误响应检测 ──
    err_type = config_dict.get("error") if config_dict else None
    if err_type:
        result["ok"] = False
        result["error_type"] = err_type
        result["errs"] = [f"LLM Error[{err_type}]: {config_dict.get('detail','')[:80]}"]
        return result

    # Score
    result["s_struct"], result["issues"] = score_structure(config_dict)
    result["s_value"], result["fields"] = score_values(config_dict, exp, tc.get("input", ""))
    errs = [f"{k}:{v}" for k, v in result["fields"].items() if v != "✅"] + result["issues"]
    result["ok"] = len(errs) == 0
    result["errs"] = errs
    return result


def run(workers: int = 5, imbalanced_only: bool = False):
    cases = TEST_CASES
    if imbalanced_only:
        # 只测电荷不平衡的用例
        from willy.toolist_global import _MOLECULES as _REG
        imbalanced_ids = set()
        for tc in TEST_CASES:
            exp = tc.get("expected")
            if exp is None: continue
            net = sum(count * (_REG._data.get(n, {}).get("charge", 0) or 0)
                     for n, count in exp.get("residues", {}).items())
            if abs(net) >= 0.5:
                imbalanced_ids.add(tc["id"])
        cases = [tc for tc in TEST_CASES if tc["id"] in imbalanced_ids]
        label = f"{len(cases)} 条电荷不平衡用例"
    else:
        label = f"{len(cases)} 用例"

    print("=" * 70)
    print(f"  LLM Config 准确性评分 ({label}, {workers} 并发)")
    print("=" * 70)

    total_struct = 0; total_value = 0
    all_fields = defaultdict(lambda: {"ok": 0, "total": 0})
    scored = 0; error_count = 0

    # 并行评估所有用例
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_eval_one, tc): tc for tc in cases}
        for future in as_completed(futures):
            r = future.result()
            if r["ok"] is None:  # 跳过的模糊用例
                continue
            scored += 1

            if not r["ok"] and "error" in r:
                err_type = r.get("error_type", "")
                icon = "🟡" if err_type else "❌"
                print(f"  #{r['id']:2d} {icon} {r.get('error','API err')}")
                if err_type: error_count += 1
                continue

            total_struct += r["s_struct"]; total_value += r["s_value"]
            icon = "✅" if r["ok"] else "❌"
            print(f"  #{r['id']:2d} {icon} [{r['source']}] {r['input'][:52]}")
            if not r["ok"]:
                for e in r["errs"][:2]: print(f"      {e}")
            for k, v in r["fields"].items():
                all_fields[k]["total"] += 1
                if v == "✅": all_fields[k]["ok"] += 1

    print("-" * 70)
    extra = f"  Error: {error_count}" if error_count else ""
    print(f"{'平均':<3}  结构:{total_struct/scored:.1f}/5  数值:{total_value/scored:.1f}/5  综合:{(total_struct+total_value)/(scored*10):.0%}{extra}")
    print()
    print("逐字段:")
    for f in sorted(all_fields.keys()):
        s = all_fields[f]; pct = s["ok"]/s["total"]; bar = "█"*int(pct*30)+"░"*(30-int(pct*30))
        print(f"  {f:<20s} [{bar}] {s['ok']}/{s['total']} {pct:.0%}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-w", "--workers", type=int, default=5, help="并发数 (默认 5)")
    parser.add_argument("--imbalanced", action="store_true", help="仅测试电荷不平衡的用例")
    args = parser.parse_args()
    run(workers=args.workers, imbalanced_only=args.imbalanced)
