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
| `invalid_value` | 参数越界 | 返回 suggestion，ask user |
| `ambiguous` | 输入模糊 (如"锂盐100溶剂200") | 返回 suggestion，ask user |

```json
{"error": {"type": "invalid_molecule", "detail": "SDBT not found", "available": ["Li","FEC",...]}}
{"error": {"type": "ambiguous", "detail": "锂盐和溶剂未指定具体分子", "suggestion": "请指定: LiTFSI / EC / DME / ..."}}
```

**App 处理**：`invalid_molecule` → 重载 registry 重试 2 次 → 展示可用列表。其他 → 直接展示建议。

## Warning (非阻塞)

| type | 条件 | 展示文案 |
|------|------|------|
| `charge_imbalance` | 净电荷 ≠ 0 | "⚠️ 电荷警告: 净电荷 +50" |
| `compute_heavy` | 分子数 > 10000 | "⚠️ 算力警告: 体系共 N 个分子..." |

```json
{"error": null, "warnings": [{"type": "charge_imbalance", "detail": "Net charge +50"}], "molecules": {...}, ...}
```

**App 处理**：在方案确认卡片中逐条渲染警告。

## 兼容

- 旧格式 `{"error": "Invalid molecule"}` (string) 仍可解析，转为 `{"type": "invalid_molecule"}`
- 新格式 `{"error": {"type": "invalid_molecule", "detail": "..."}}` 为推荐格式
