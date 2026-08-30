# Willy 外部 Smoke Fixture Bundle

真实外部工具 fixture 不进入普通 `tests/` 回归路径。验收机通过 `WILLY_EXTERNAL_SMOKE_FIXTURES` 指向受控、可读的 bundle 根目录。

每个 bundle 必须含 `fixture-manifest.json`：

```json
{
  "schema_version": 1,
  "bundle_id": "reviewed-minimal-inputs",
  "cases": {
    "sobtop_ec": {
      "files": {
        "sobtop_ec/EC.mol2": {
          "sha256": "<64-char lowercase SHA-256>",
          "size_bytes": 123
        },
        "sobtop_ec/EC.chg": {
          "sha256": "<64-char lowercase SHA-256>",
          "size_bytes": 45
        }
      }
    }
  }
}
```

`cases.<case_id>.files` 必须与 `src/willy/external_smoke.py` 中的 `fixture_files` 完全一致。预检拒绝缺失、额外、大小不符或 SHA-256 不符的文件。

支持的 case 路径：

- `gromacs_minimal/topol.top`、`g01_minimal.itp`、`model.pdb`、`em.mdp`、`eq.mdp`、`prod.mdp`
- `sobtop_ec/EC.mol2`、`sobtop_ec/EC.chg`
- `g16_minimal/input.gjf`
- `orca_minimal/input.gjf`
- `multiwfn_minimal/input.fchk`
- `ligpargen_minimal/input.mol2`

预检不启动科学计算：

```bash
python3 -m willy.external_smoke \
  --fixture-root /secure/willy-smoke-fixtures \
  --cases sobtop_ec \
  --required
```

真实执行必须通过 `pytest -m external --run-external` 显式启用，并由 `WILLY_EXTERNAL_SMOKE_EVIDENCE_DIR` 输出脱敏 evidence。fixture 内容、许可证、真实日志和密钥不得上传为 CI artifact。
