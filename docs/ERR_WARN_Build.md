# Willy Error & Warning 协议

## 概述

LLM 输出统一 JSON 格式，`error` 和 `warnings` 使用固定英文 type，app 层转为中文展示。

## 输出结构

```json
{
  "error": null,                    // Error | null
  "warnings": [],                   // Warning[]
  "molecules": { ... },
  "residues": { ... },
  "md": { ... },
  "defaults": { ... }
}
```

## Error (阻塞)

| type | 说明 | LLM 行为 |
|------|------|------|
| `invalid_molecule` | 分子不在 registry | refresh_structs ×2 → 仍失败则返回，列出 available |
| `invalid_value` | 参数越界或 MD 数值配置包含 `NaN`、`+Inf`、`-Inf` | 返回 suggestion，ask user |
| `ambiguous` | 输入模糊 (如"锂盐100溶剂200") | 返回 suggestion，ask user |
| `invalid_quantum_input` | 所选后端的原始输入缺失或格式不完整 | 返回后端、文件类型与公开格式问题；不生成方案 |
| `charge_imbalance` | 审计后的体系净电荷不为零且未明确补偿/非中性策略 | 返回净电荷与补充策略提示；不生成方案 |

配置阶段的 `charge_imbalance` 检查原始量子输入的整数电荷与组分数量；未明确
`ion_compensation` 或 `non_neutral_confirmed=true` 时仍是阻塞错误。它不等同于 LigParGen
四位小数电荷在 GROMACS `grompp` 中产生的累计舍入。对后者，模拟执行器只允许单一 Ewald
净电荷 warning 且 `abs(total_charge) <= 0.15 e`，即 `[-0.15 e, +0.15 e]`，包含两个端点；
这里 `e` 是元电荷单位，不是能量单位 `eV`。满足条件时仅允许一次受控 `-maxwarn 1` 重试；
缺少可解析电荷、超过阈值或伴随其他 warning 时仍以输入契约失败记录。此容差不修改电荷，
不替代配置阶段的非中性确认，也不直接授予 MD 阶段许可。

```json
{"error": {"type": "invalid_molecule", "detail": "SDBT not found", "available": ["Li","FEC",...]}}
{"error": {"type": "ambiguous", "detail": "锂盐和溶剂未指定具体分子", "suggestion": "请指定: LiTFSI / EC / DME / ..."}}
```

**App 处理**：`invalid_molecule` → 重载 registry 重试 2 次 → 展示可用列表。其他 → 直接展示建议。

## Warning (非阻塞)

| type | 条件 | 展示文案 |
|------|------|------|
| `compute_heavy` | 分子数 > 10000 | "⚠️ 算力警告: 体系共 N 个分子..." |

```json
{"error": null, "warnings": [{"type": "compute_heavy", "detail": "Total 20000 molecules"}], "molecules": {...}, ...}
```

**App 处理**：在方案确认卡片中逐条渲染警告。

## 兼容

- 旧格式 `{"error": "Invalid molecule"}` (string) 仍可解析，转为 `{"type": "invalid_molecule"}`
- 新格式 `{"error": {"type": "invalid_molecule", "detail": "..."}}` 为推荐格式
