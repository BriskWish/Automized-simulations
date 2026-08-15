# Willy External Smoke Fixture Bundle

真实外部工具 fixture 不随应用仓库发布，也不得放入普通 `tests/` 回归路径。验收机在受控、可读的目录中提供 bundle，并通过 `WILLY_EXTERNAL_SMOKE_FIXTURES` 指向其根目录。

每个 bundle 必须含 `fixture-manifest.json`，其格式如下：

```json
{
  "schema_version": 1,
  "bundle_id": "2026-08-05-reviewed-minimal-inputs",
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

`cases.<case_id>.files` 必须与 `src/willy/external_smoke.py` 中该 case 的 `fixture_files` 完全一致。预检会拒绝缺失文件、额外/遗漏的 manifest 文件项、大小不符或 SHA-256 不符的 bundle。

当前 case 路径：

- `gromacs_minimal/topol.top`、`g01_minimal.itp`、`model.pdb`、`em.mdp`、`eq.mdp`、`prod.mdp`
- `sobtop_ec/EC.mol2`、`sobtop_ec/EC.chg`
- `g16_minimal/input.gjf`
- `orca_minimal/input.gjf`
- `multiwfn_minimal/input.fchk`
- `ligpargen_minimal/input.mol2`

预检命令不会启动任何外部计算：

```bash
python3 -m willy.external_smoke \
  --fixture-root /secure/willy-smoke-fixtures \
  --cases sobtop_ec \
  --required
```

真实执行仍须通过 `pytest -m external --run-external` 显式启用，并由 `WILLY_EXTERNAL_SMOKE_EVIDENCE_DIR` 输出脱敏、带输入/产物散列的证据 JSON。fixture 内容、许可证文件、真实运行日志和任何密钥都不得上传为 CI artifact。
