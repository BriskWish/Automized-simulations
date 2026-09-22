# 量子层

量子层负责将经过审计的原始输入转换为可供 RESP 和拓扑使用的量子产物。G16/G09 使用 `.gjf`，ORCA 使用 `.inp`；两种输入不可互相静默转写。

## 流程与产物

```text
G16/G09: .gjf -> 结构优化 -> .fchk -> 单点 -> *_opt.fchk -> .mol2 + .chg
ORCA:    .inp -> 结构优化 -> .molden -> 单点 -> *_opt.molden
         -> *_opt.fchk + .mol2 + .chg
```

ORCA 的 MOL2 连通性和 Mayer 键级来自 Molden 路径；RESP 统一消费 `*_opt.fchk`。单原子输入使用不会改写原始文件的受控 SP 处理，以满足下游波函数契约。

## 输入审计

启动候选方案和创建 run 前必须审计原始输入：

| 后端 | 必需条件 |
|---|---|
| G16/G09 | route card、charge/multiplicity、有限坐标和至少一个原子。 |
| ORCA | `!` 关键词行、内嵌 `* xyz <charge> <multiplicity>` 坐标块、闭合 `*`、有限坐标和 `Opt`。 |

文件内的电荷和自旋是权威值。名称识别、知识库默认值和自然语言不能覆盖原始输入。缺失、交叉后缀、外链坐标、无效坐标或不一致的输入会阻止启动。

体系净电荷按原始输入的整数电荷乘以组分数量求和；非零且未明确 `ion_compensation` 或
`non_neutral_confirmed=true` 时，配置阶段以 `charge_imbalance` 阻塞。模拟层允许的
`0.15 e` 参数化舍入容差仅用于 `grompp` 的单一 Ewald 净电荷 warning，不放宽本层输入审计；
详见 [`ERR_WARN_Build.md`](ERR_WARN_Build.md) 与 [`simulation.md`](simulation.md)。

## 上传规范化

方案页的上传入口只接收 `.gjf` 与 `.inp`。上传时验证内嵌坐标、电荷和自旋，并以安全的核心文件名重建原始输入；上传文件中的 route、方法、基组、`mem`、`nproc` 和其他执行指令不会被继承。默认基组及计算资源由候选 `config.json` 决定，确认启动时再按本机容量规范化 `nproc`。

`.mol2`、`.pdb`、`.xyz` 不含统一、可验证的自旋语义，因此不能作为量子原始输入上传或自动转写。用户需要提供后端原生、带内嵌电荷和自旋的 `.gjf` 或 `.inp`。

## 资源与进程

`defaults.nproc` 由环境注册表规范化；量子程序按受管进程组运行。外部进程的启动、超时和停止写入私有生命周期记录，公开状态只包含步骤和脱敏错误摘要。

## Gaussian SMD 溶剂

`struct/smd_solvents/gaussian_builtin.json` 保存一次性抓取的 184 个 Gaussian 内置溶剂名称及
`epsilon`；不补造官网没有列出的 `epsinf`，不保存网页来源，不提供 refresh。
`gaussian_manual.json` 单独保存人工条目，包含 `epsilon`、`epsinf` 和 `manual=true`。
名称按去除首尾空白后的大小写不敏感精确匹配判重；内置和人工名称都不能被同名覆盖。
`gas` 为保留值，表示关闭 SMD。

方案页的“溶剂库”入口允许查询和登记。只提交介电常数与极限介电常数时，依次分配
`default_1`、`default_2` 等未占用名称；必须满足有限数值 `epsilon >= epsinf >= 1`。
登记只是写入溶剂库，不修改已确认工程。检索先精确匹配，未命中时采用与结构库类似的字符
TF-IDF/余弦相似度返回候选，模糊候选不等于已选择溶剂。

方案按 `molecules.<name>.solvent` 保存分子级选择，并将参数冻结到 `solvent_ref`。
用户未限定分子时，对方案全部选中分子统一应用；指定分子时仅修改该分子。
优化与后续单点统一消费同一选择，RESP 间接使用该单点波函数。
内置溶剂生成 `SCRF=(SMD,Solvent=<name>)`；人工溶剂生成
`SCRF=(SMD,Solvent=Generic,Read)` 并在坐标段后追加 `Eps=...`、`EpsInf=...` 和结束空行。
多行 route 中已有 SCRF 会先移除再插入，选择 `gas` 时移除原有 SCRF；方案卡片显示覆盖或移除情况。
人工快照随工程冻结，原库条目后续变化不能悄悄改变已确认计算。
ORCA 当前不接入 SMD，只允许 `gas`。

本机 G16 的 EC 优化已验证仅给 `Eps/EpsInf` 的 Generic/Read 输入可接受并收敛，证据见
[`gaussian_smd_generic_20260921.json`](../tests/reports/audits/gaussian_smd_generic_20260921.json)。
这证明写法与参数读取可行，不证明该二参数输入构成完整的 SMD 溶剂参数化；未指定的
非静电溶剂描述符在本机输出为零。因此人工二参数条目明确作为介电近似使用，不宣称完整溶剂化自由能精度。

## 边界

G09 仅保留接口，不构成可靠全链路承诺。量子成功只证明下游输入就绪，不代表分子性质、力场质量或模拟科学结论。
