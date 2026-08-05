# 拓扑层设计文档

> 维护范围：`src/willy/topology/`。拓扑层将当前 run 的量子产物转为经过校验的 GROMACS 拓扑产物，并以 manifest 作为唯一的 Step 4/5 交接契约。

## 1. 配置与后端

`config.json` 的 `topology` 段是唯一后端选择入口：

```json
{
  "topology": {
    "backend": "sobtop",
    "force_field": "gaff_uff",
    "default_lbcc": false,
    "default_opt_steps": 0
  }
}
```

| backend | force_field | forcefield_family | 适用范围 |
|---|---|---|---|
| `sobtop` | `gaff_uff` | `gaff_uff` | Sobtop 的 GAFF 优先、UFF 补齐组合；可在同一体系内使用 |
| `oplsaa` | `oplsaa` | `oplsaa` | LigParGen/BOSS 的整套 OPLS-AA 体系 |

不支持 AMBER。Sobtop 与 OPLS-AA 不能在同一个 run 内混用。历史配置的 `backend=ligpargen, force_field=gaff` 是旧编排器始终调用 Sobtop 造成的矛盾，运行时会迁移为 `sobtop/gaff_uff`；带 OPLS 字段的历史 LigParGen 配置迁移为 `oplsaa/oplsaa`。

OPLS-AA 依赖 `LigParGen` 和 BOSS。推荐用 `WILLY_LIGPARGEN_BIN`、`WILLY_BOSS_HOME` 配置；兼容读取 `BOSSdir`，未设置时会采用已存在的 `~/boss/boss`。BOSS 目录只在 LigParGen 子进程环境中以 `BOSSdir` 注入，预检不会修改 Willy 进程的全局环境。

Step 4 只调用 `dispatch_topology(config_path, workspace)`。它通过 `TopologyBackend` 接口选择注册后端：

```python
class TopologyBackend:
    forcefield_family: str

    def prepare(self, plan) -> StepResult: ...
    def parameterize(self, component, workspace) -> StepResult: ...
    def validate(self, component, outputs) -> StepResult: ...
```

新增 CGenFF、OpenFF 等后端必须实现并注册此接口，不得向 `PipelineOrchestrator` 添加业务分支。

## 2. Manifest 契约

Step 4 只从当前 run 的配置快照中列出的 `residues`/`molecules` 建立计划，绝不扫描目录内所有 `.mol2` 文件。它在 `md_run/<run_id>/topology_manifest.json` 原子写入每个组件的：

- `molecule_id`、`residue_name`、`quantity`
- `mol2`、`chg`、`itp`（未修订的后端源）、`assembly_itp`（主拓扑消费的副本）、`gro`
- `charge`、`spin`、`smiles`
- `lbcc`、`opt_steps`（OPLS-AA 参数化实参）
- `backend`、`forcefield_family`
- `success`、`validated`、`error`
- `retry_ledger`（按 `backend:molecule:action` 持久化的重试账本）

Sobtop 组件必须有 `.mol2` 和 `.chg`。OPLS-AA 使用每个组件配置的真实 `charge` 和 `spin`（LigParGen 使用前者，后者记录在 manifest 供追溯）。Step 5 和 Topology Agent 只消费 manifest 中 `success=true` 且 `validated=true` 的显式路径。

`.mol2` 或 `.chg` 缺失时，Step 4 必须以对应分子返回 `file_not_found`，不得猜测或扫描其他 run 的产物。该错误说明上游量子产物不完整；编排器的 Step 2/3 完整性门禁应在进入拓扑层前阻止该情况，Sobtop 的检查仅作为最后一道输入防线。

LigParGen 将临时文件写到 `/tmp`，因此每次 OPLS-AA 调用会使用运行级唯一前缀，且只清理、验证和复制该前缀的 `.itp/.gro`。复制至 run_dir 前会将 ITP 的 `[ moleculetype ]` 恢复为配置中的残基名，避免临时前缀进入最终主拓扑。残基名在创建计划及写入前均会拒绝 `/`、`\\` 和路径跳转，输出路径还会验证其解析后的父目录仍是当前 run_dir。

## 3. Sobtop 执行

Sobtop 在 `vendor/sobtop/` 内写中间输出，因此 `topo_gaff.py` 使用跨进程 `fcntl` 锁保护工作目录。每次执行都会：

1. 删除该分子的 vendor 与 run_dir 旧 `.itp`、`.gro`、`.top`。
2. 使用 `SobtopInputBuilder` 构造并测试正式交互输入。
3. 只接受本次进程启动后写出的完整 `.itp/.gro/.top`。
4. 先验证 ITP/GRO，再复制 `.itp/.gro` 到 run_dir。
5. 在成功、失败和超时路径清理 vendor 临时输出。

Sobtop、LigParGen/BOSS 以及 mol2 转 SMILES 的 obabel 均通过
`run_managed_command()` 在独立进程组内执行。当前 run 请求停止或命令超时时，
执行器对该进程组依次发送 `SIGINT -> SIGTERM -> SIGKILL`，并仅在触发停止时在
run 私有 `process_lifecycle.jsonl` 记录脱敏命令、停止原因、已发信号和退出码。
这不改变后端的 `StepResult`：超时仍为 `timeout`，普通非零退出仍按 Sobtop 或
LigParGen 的原有错误分类处理。

已用内置 Sobtop `2026.1.16` 记录菜单含义。冻结的动作序列是：

```text
mol2 -> 7 -> 10 -> chg -> 0 -> 1 -> 2 -> 4 -> top/itp paths -> 2 -> gro path -> 0
```

其中 `2` 为“GAFF 后以 UFF 补齐原子类型”，`4` 为“预置键参数优先、缺失参数猜测”。返回码 `0` 正常成功；返回码 `24` 仅在本次 ITP/GRO 完整且格式验证通过时成功；其他所有非零返回码失败。

## 4. 产物与组装

Step 4 对每个后端产物执行统一验证：

- ITP 必须含 `[ moleculetype ]` 和 `[ atoms ]`，且有合法原子数据行。
- `[ moleculetype ]` 的首个合法名称必须等于 manifest/config 中的 `residue_name`。
- GRO 第二行原子数必须可解析。
- ITP 的 `[ atoms ]` 原子数必须等于 GRO 原子数。

`top_assembly.build()` 要求 manifest 覆盖每个启用残基，且每个 ITP/GRO 真实存在于当前 run_dir。它只读取这些条目，不扫描目录。atomtype 的判定键是“类型名称 + 全部参数”：同名同参数去重，同名不同参数返回 `ErrorKind.ATOMTYPE_CONFLICT` 并停止。`gaff_uff` 组件可共同组装；任意跨 `forcefield_family` 组合均失败。`[ defaults ]` 和预处理定义由各 forcefield family 的模板生成。

组装时先从未修订的后端 `itp` 收集 atomtype，再复制为同一 run_dir 的专属 `.assembly_itp/<residue>.itp` 并仅修订该副本。`topol.top` 只 include 副本，因此既不会覆盖可能名为 `A.assembled` 的后端源，也能在首次成功后重试 assembly 时从完整源 ITP 收集 atomtype。组装前要求至少有一个 atomtype 定义，且所有 `[ atoms ]` 引用的类型均已定义；修订器按下一个 `[ section ]` 找到 `[ atomtypes ]` 的结束，保留注释和无关格式，只修改合法 `[ atoms ]` 数据行，并保持幂等。

## 5. Agent 边界

Topology Agent 的重试工具不接受任意 `.mol2/.chg` 路径，只从当前 manifest 取得输入。Sobtop 失败只能重试 Sobtop；OPLS-AA 失败只能重试 OPLS-AA。需要改用另一力场时，Agent 只能建议派生新的运行，不能在当前 manifest 混入另一后端产物。`tools_skip_molecule_topology` 暂时禁用，直到能原子同步 `residues` 和 manifest。

重试账本在 manifest 锁内更新：同一 `backend + molecule + action` 最多 2 次，整个拓扑层最多 4 次；`top_assembly` 也作为独立 action 受相同约束。达到上限后工具返回 `retry_limit_exceeded`，而不是继续调用外部程序。

## 6. 验收

最小回归命令：

```bash
pytest -q tests/test_topology_contract.py tests/test_top_assembly.py tests/test_toolist_quantum_topology.py
pytest -q
```

有 Sobtop 环境时，可额外以最小 `.mol2/.chg` 执行真实集成测试；测试产物必须位于 `md_run/<run_id>/`，不得残留在 `vendor/sobtop/`。

2026-08-05：Step 4 的外部命令统一接入受管进程组生命周期；替身回归覆盖停止、超时和脱敏审计，真实工具验收仍须显式启用 external smoke。
