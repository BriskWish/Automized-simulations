# 拓扑层

拓扑层只消费当前 run 中经过验证的 `.mol2` 与 `.chg`，并生成可由 GROMACS 组装器消费的 `.itp`、`.gro` 和 `topol.top`。

## 后端

| 配置 | family | 范围 |
|---|---|---|
| `sobtop` / `gaff_uff` | `gaff_uff` | 默认小分子参数化；GAFF 优先、UFF 补齐。 |
| `oplsaa` / `oplsaa` | `oplsaa` | LigParGen/BOSS 路径；需要完整外部依赖。 |

同一 run 只能使用一个 forcefield family。AMBER、离子 OPLS 和不同 family 的静默混用不受支持。OPLS 路径仅面向可由外部参数化依赖正确处理的组分；缺少专用参数的金属或无氢离子必须拒绝而非回退到另一后端。

## Manifest 契约

每个组件在 `run_manifest.json` 的私有 topology section 中登记：分子与残基标识、数量、输入与产物、后端、family、真实电荷/自旋、验证状态和受限重试账本。Step 5 只消费 `success=true` 且 `validated=true` 的当前 run 路径，不扫描目录猜测输入。

ITP 必须含合法 `[ moleculetype ]` 和 `[ atoms ]`；残基名、ITP 原子数和 GRO 原子数必须一致。主拓扑要求覆盖全部启用组件，并拒绝冲突 atomtype 或跨 family 组合。

## 组装与执行

Sobtop、LigParGen/BOSS 和 Open Babel 均由受管进程组执行。Sobtop 临时输出受锁保护，只有本次调用生成且通过格式校验的文件能进入 run 工作区。OPLS 组件在组装副本中使用确定性 atomtype 命名空间，源 ITP 不被修改。

重试仅允许读取 manifest 中的当前组件和后端；切换力场必须派生新 run。每个组件动作和整个拓扑层均有固定重试上限。
