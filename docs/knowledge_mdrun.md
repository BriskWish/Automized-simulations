# GROMACS mdrun 诊断知识库

> 维护范围：EQ 失败后的受限 Simulation Agent proposal。知识来源为 GROMACS User Guide 的人工整理索引，不在运行期联网抓取。条目用于生成待确认建议，不替代当前 run 的阶段验收、ErrorKind、原始证据或人工确认。

## 使用边界

- 模型最初只获得条目 `number + name` 索引；它必须通过 `tools_lookup_mdrun_knowledge` 以数字和名称成对读取内容。
- 单次读取最多 3 条；同一实际失败诊断周期最多 2 次。第二次真实失败新建周期并重新计数，重复提问或替换同一待确认方案不重置预算。
- 命中条目仍可能与本机 GROMACS 版本、力场、约束、体系大小或硬件不兼容。建议必须展示兼容性提醒，并经用户确认后才能改变协议。
- 未命中、知识源不可用或未读取条目时，建议必须标为“LLM 未经知识库验证的推断”。
- `ErrorKind=unknown` 不得据此生成默认调参或回退方案。运行会记录“申请联网检索/人工审核”的升级事实；当前版本没有运行期外网检索提供方，因此不会自动联网或编造检索结论。未来经批准的外部结论也只能在它明确支持“当前步调参重试”或“打回第 7 步建盒重试”之一，并再次通过参数白名单和配置契约时，才能替换待确认方案。

## 条目索引

| number | name | category |
|---:|---|---|
| 1 | Constraint instability (LINCS, SETTLE, SHAKE) | runtime_error |
| 2 | Pressure scaling exceeded safe limit | runtime_error |
| 3 | Non-finite force during energy minimization | runtime_error |
| 4 | Cutoff exceeds periodic box dimension | runtime_error |
| 5 | Coordinate and topology atom-count mismatch | runtime_error |
| 6 | No simulation output or incomplete run | runtime_error |
| 7 | Out of memory while allocating | runtime_error |
| 8 | Checkpoint continuation and append compatibility | mdrun_feature |
| 9 | Stopping a running simulation safely | mdrun_feature |
| 10 | Integration time step (`dt`) | mdp_option |
| 11 | Pressure coupling time constant (`tau-p`) | mdp_option |
| 12 | Isothermal compressibility | mdp_option |
| 13 | Temperature coupling time constant (`tau-t`) | mdp_option |

## Entries

### Entry 1: Constraint instability (LINCS, SETTLE, SHAKE)
- category: runtime_error
- source_url: https://manual.gromacs.org/documentation/2025.0/user-guide/run-time-errors.html
- source_section: LINCS, SETTLE and SHAKE warnings
- applicable_when: EQ or PROD reports constraint warnings, rapidly growing bond deviations, or an mdrun constraint fatal error.
- facts: Constraint failures can indicate an unstable integration step, invalid starting geometry, excessive heating, or unsuitable constraint settings; they do not identify one unique cause.
- evidence_needed: Stage, public error kind, constraint warning class, temperature trend, and whether EM completed are needed before selecting a remedy.
- allowed_adjustment_fields: dt,lincs_iter,lincs_order,tau_t
- compatibility_notice: Parameter meanings and warning thresholds can vary across GROMACS versions and force fields; verify against the installed version before confirmation.

### Entry 2: Pressure scaling exceeded safe limit
- category: runtime_error
- source_url: https://manual.gromacs.org/documentation/2025.0/user-guide/run-time-errors.html
- source_section: Pressure scaling more than 1%
- applicable_when: NPT EQ reports a large pressure-scaling warning, abrupt volume change, low density, or a vacuum-region observation.
- facts: Large pressure scaling can result from an unsuitable starting volume, strong transient pressure response, or a coupling setup that is too aggressive for the current structure; it is not proof that one parameter alone is wrong.
- evidence_needed: Final-window density and pressure statistics, vacuum detection, initial box audit, and the active pressure-coupling settings are required.
- allowed_adjustment_fields: box_density,eq_tau_p
- compatibility_notice: The warning text and suitable coupling values depend on ensemble, system compressibility, and GROMACS version; confirm the proposed range for the active force field.

### Entry 3: Non-finite force during energy minimization
- category: runtime_error
- source_url: https://manual.gromacs.org/documentation/2025.0/user-guide/run-time-errors.html
- source_section: Non-finite force during energy minimization
- applicable_when: EM reports NaN, infinite force, or an energy-minimization force calculation failure.
- facts: Non-finite forces usually require inspecting the initial structure, topology, non-bonded parameters, and close contacts before later-stage protocol changes are considered.
- evidence_needed: EM error kind, close-contact or topology evidence, Packmol audit, and affected stage are required.
- allowed_adjustment_fields: box_density
- compatibility_notice: This entry is an upstream EM diagnostic reference. It does not authorize an EQ-only proposal to bypass EM or topology validation.

### Entry 4: Cutoff exceeds periodic box dimension
- category: runtime_error
- source_url: https://manual.gromacs.org/documentation/2025.0/user-guide/run-time-errors.html
- source_section: The cut-off length is longer than half the shortest box vector
- applicable_when: grompp or mdrun rejects the cutoff relative to a periodic box vector.
- facts: A cutoff/box conflict is a geometric input-contract problem. Adjusting a thermostat or extending EQ does not resolve it.
- evidence_needed: Public grompp error category, requested box vectors, and active cutoff settings are required.
- allowed_adjustment_fields: box_density
- compatibility_notice: Exact cutoff restrictions depend on electrostatics and periodic-boundary settings; preserve the active topology and MDP contract when correcting the box.

### Entry 5: Coordinate and topology atom-count mismatch
- category: runtime_error
- source_url: https://manual.gromacs.org/documentation/2025.0/user-guide/run-time-errors.html
- source_section: Number of coordinates in coordinate file does not match topology
- applicable_when: grompp reports different atom counts between the coordinate file and topology.
- facts: This is an input-contract mismatch, commonly caused by stale coordinates or topology assembly changes. MD protocol changes do not repair it.
- evidence_needed: Public stage, grompp error category, and registered input-contract status are required.
- allowed_adjustment_fields:
- compatibility_notice: This entry is for escalation and input reconstruction, not for silent changes to an active scientific protocol.

### Entry 6: No simulation output or incomplete run
- category: runtime_error
- source_url: https://manual.gromacs.org/documentation/2025.0/user-guide/run-time-errors.html
- source_section: Simulation is not producing output or ended unexpectedly
- applicable_when: mdrun exits or stalls without the required stage artifacts.
- facts: Missing output can arise from a stopped process, filesystem or resource failure, or an earlier engine error. Artifact absence alone does not establish numerical instability.
- evidence_needed: Managed process state, public exit category, required artifact contract, and stop-request status are required.
- allowed_adjustment_fields:
- compatibility_notice: Do not infer a restart command from this entry. Continuation remains controlled by the run-local manifest and checkpoint fingerprints.

### Entry 7: Out of memory while allocating
- category: runtime_error
- source_url: https://manual.gromacs.org/documentation/2025.0/user-guide/run-time-errors.html
- source_section: Out of memory when allocating
- applicable_when: GROMACS reports an allocation failure or the managed process reports an out-of-memory exit.
- facts: Allocation failure is a resource-capacity problem. It may depend on system size, neighbor-list settings, trajectory output, GPU memory, or concurrent workloads.
- evidence_needed: Public error kind, stage, resource report, system-size summary, and active output settings are required.
- allowed_adjustment_fields:
- compatibility_notice: Hardware and decomposition behavior are host-specific. This entry supports explanation and escalation, not autonomous resource reconfiguration.

### Entry 8: Checkpoint continuation and append compatibility
- category: mdrun_feature
- source_url: https://manual.gromacs.org/documentation/2025.0/user-guide/mdrun-features.html
- source_section: Restarting simulations and appending output
- applicable_when: PROD recovery considers a checkpoint or append after an interrupted stage.
- facts: Checkpoint continuation relies on compatible run inputs and output state. A changed protocol or unaccepted upstream EQ cannot safely be treated as an appendable continuation.
- evidence_needed: Stage acceptance, checkpoint existence, and run-local input fingerprints are required.
- allowed_adjustment_fields:
- compatibility_notice: Continuation flags and checkpoint compatibility behavior are version-dependent; the manifest gate remains authoritative.

### Entry 9: Stopping a running simulation safely
- category: mdrun_feature
- source_url: https://manual.gromacs.org/documentation/2025.0/user-guide/mdrun-features.html
- source_section: Terminating simulations
- applicable_when: A user requests an orderly stop of a managed GROMACS stage.
- facts: A safe stop should preserve the system-managed checkpoint path where possible. Stopping is an operational action, not evidence that EQ has passed or failed.
- evidence_needed: Managed process lifecycle state, active stage, and stop-request acknowledgement are required.
- allowed_adjustment_fields:
- compatibility_notice: Signal and checkpoint behavior differ by scheduler, operating system, and GROMACS build; only the managed lifecycle controller may execute a stop.

### Entry 10: Integration time step (`dt`)
- category: mdp_option
- source_url: https://manual.gromacs.org/documentation/2025.0/user-guide/mdp-options.html
- source_section: Run control - dt
- applicable_when: Public evidence suggests integration instability, constraint failure, or temperature runaway during EQ.
- facts: The integration time step controls temporal resolution and interacts with constraints, masses, temperature, and force-field stability. Reducing it can be a candidate, not a diagnosis.
- evidence_needed: Error category, temperature evidence, constraints, and current dt are required.
- allowed_adjustment_fields: dt
- compatibility_notice: Suitable values depend on the active constraint model, virtual sites, force field, and GROMACS version; do not apply a generic value without confirmation.

### Entry 11: Pressure coupling time constant (`tau-p`)
- category: mdp_option
- source_url: https://manual.gromacs.org/documentation/2025.0/user-guide/mdp-options.html
- source_section: Pressure coupling - tau-p
- applicable_when: NPT EQ shows pressure or volume transients after the input-contract and vacuum evidence have been examined.
- facts: The pressure-coupling time constant controls the response rate of the barostat. Changing it affects relaxation behavior but cannot replace a valid initial box or stable integration setup.
- evidence_needed: Pressure and density final-window statistics, volume or vacuum evidence, active coupling algorithm, and current tau-p are required.
- allowed_adjustment_fields: eq_tau_p
- compatibility_notice: Recommended values depend on the selected barostat and ensemble. Some algorithms have distinct equilibration limitations in different GROMACS versions.

### Entry 12: Isothermal compressibility
- category: mdp_option
- source_url: https://manual.gromacs.org/documentation/2025.0/user-guide/mdp-options.html
- source_section: Pressure coupling - compressibility
- applicable_when: Pressure coupling is being assessed for a liquid or mixed electrolyte and a physically justified compressibility is available.
- facts: Compressibility is a material and ensemble input to pressure coupling. An arbitrary change can alter volume response and should not be used as a generic numerical-stability fix.
- evidence_needed: Material model, force field, target phase, coupling algorithm, and current compressibility are required.
- allowed_adjustment_fields:
- compatibility_notice: This repository does not expose compressibility as a normal EQ pending-action field. Any future change needs an explicit protocol contract and version review.

### Entry 13: Temperature coupling time constant (`tau-t`)
- category: mdp_option
- source_url: https://manual.gromacs.org/documentation/2025.0/user-guide/mdp-options.html
- source_section: Temperature coupling - tau-t
- applicable_when: EQ final-window temperature fails acceptance or tracks the scheduled temperature poorly without a stronger input-contract failure.
- facts: The temperature-coupling time constant sets thermostat response. It can affect temperature tracking and transient behavior, but it does not independently prove the source of density, pressure, or constraint problems.
- evidence_needed: Temperature final-window statistics, active thermostat, annealing segment, dt, and constraint evidence are required.
- allowed_adjustment_fields: tau_t
- compatibility_notice: Thermostat behavior and recommended values are algorithm- and version-dependent; confirm compatibility with the installed GROMACS release before applying.
