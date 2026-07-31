# Willy Project Gap Analysis

> Generated 2026-07-29 · Coverage: security, architecture, code quality, documentation, testing

---

## CRITICAL

### CRIT-1 — Hardcoded Live API Key in `.env`

**File:** `.env`

A real DeepSeek API key is stored in plaintext. While `.env` is in `.gitignore`, any process or script that reads this file (e.g. `benchmarks/llm_score.py` line 14) can log or leak the key.

**Fix:** Rotate the key immediately, replace with placeholder `DEEPSEEK_API_KEY=your_api_key_here`. Use `benchmarks/llm_score.py`'s own key loading path rather than reading `.env` directly.

### CRIT-2 — Gradio Bound to `0.0.0.0`

**File:** `app.py:172-173`

```python
app.launch(server_name="0.0.0.0", server_port=7860, share=False, ...)
```

The Gradio UI is exposed on all network interfaces. An MD simulation control panel with shell-execution capability should not be reachable from the LAN without authentication.

**Fix:** Change to `server_name="127.0.0.1"`, or add `auth=(username, password)` for remote access.

### CRIT-3 — `tools_skip_molecule_simulation` Handler Exists but Tool Definition Missing

**File:** `src/willy/toolist_simulation.py`

The `handle_simulation_tool_call()` dispatch includes a `"tools_skip_molecule_simulation"` branch (line ~372), but the corresponding entry in `SIMULATION_TOOLS` (the JSON Schema list) is absent. The LLM never sees this tool — the Simulation Agent cannot skip molecules.

**Fix:** Add the tool definition entry to `SIMULATION_TOOLS`.

---

## HIGH

### HIGH-1 — Zero Structured Logging — `print()` Only

**Files:** entire `src/willy/` tree

No `logging` module usage anywhere. ~70 bare `print()` calls across pipeline orchestrator, simulation executors, topology modules, and env checker. Consequences: no log levels, no file rotation, thread-unsafe in Gradio context, production runs undebuggable.

**Fix:** Add `src/willy/_logging.py` with `logger = logging.getLogger("willy")`, replace all `print()` with `logger.info()`/`logger.warning()`/`logger.error()`.

### HIGH-2 — No Simulation Layer Design Document

**Files:** `docs/` directory

`docs/quantum_design.md` and `docs/topology_design.md` exist. `docs/simulation_design.md` is missing. The 7-module simulation layer (MDP generation, Packmol box, GROMACS EM/EQ/PROD, convergence checks) has no design documentation.

**Fix:** Write `docs/simulation_design.md` following the structure established by `quantum_design.md`.

### HIGH-3 — 11 Bare `raise` Statements Not Wrapped as `StepResult`

**Files:** `quantum/_orca_utils.py`, `quantum/resp_maker.py`, `quantum/mol2_g16.py`, `quantum/struct_g16.py`, `topology/topo_opls.py`, `simulation/box.py`, `topology/top_assembly.py`

Consensus principle #1 in `employees.md` requires all pipeline functions to return `StepResult`. Eleven bare `raise RuntimeError`/`ValueError`/`FileNotFoundError` violations exist. These are caught by broad `except Exception` blocks in tool handlers but lose structured error context (`ErrorKind`, `hint`, `raw_output`).

**Fix:** Wrap each `raise` in a `StepResult` with the appropriate `ErrorKind`.

### HIGH-4 — `os.system()` Command Injection Risk in `frontend_api.py`

**File:** `src/willy/frontend_api.py:79,89`

```python
os.system(f"pkill -9 -f '{name}' 2>/dev/null")
os.system(f"rm -f {ROOT}/model.inp {ROOT}/model.pdb")
```

The `pkill` line is vulnerable to command injection if `name` contains single quotes or shell metacharacters. Currently `name` comes from a hardcoded list, but this is fragile.

**Fix:** Replace with `subprocess.run([...], shell=False)` or use `os.kill(pid, signal.SIGKILL)`.

### HIGH-5 — `shell=True` in 5 Subprocess Calls

**Files:** `agent_config.py:148-150`, `simulation/eq.py:44-45`, `simulation/box.py:216-218`, `quantum/struct_g16.py:207-210,246-249`

All five locations use `shell=True`. Some are justified (environment variable injection for Gaussian), but most can be refactored to `shell=False` with explicit argument lists.

**Fix:** Convert to `shell=False` + `[cmd, arg1, arg2, ...]` where possible. Document remaining `shell=True` cases with inline comments explaining why.

### HIGH-6 — `docs/lithium-salts.md` is an Orphaned 303-line Document

**File:** `docs/lithium-salts.md`

A comprehensive research document on lithium salt electrolyte design. Referenced by zero files in the entire codebase — no imports, no document includes, no README links. Either integrate it or remove it.

**Fix:** Add a link in `README.md` under "更多文档", or consolidate relevant content into `knowledge.md`.

---

## MEDIUM

### MED-1 — Design Document Filenames Out of Sync with Actual Code

| Design Doc Reference | Actual File |
|------|------|
| `sobtop_interface.py` | `topo_gaff.py` |
| `ligpargen_interface.py` | `topo_opls.py` |
| `top_maker.py` | `top_assembly.py` |
| `mdp_maker.py` | `mdp.py` |
| `inp_generator.py` | `box.py` |
| `md_setup.py` | `setup.py` |
| `_md_utils.py` | `_gmx_utils.py` |
| `md_em.py` / `md_eq.py` / `md_prod.py` | `em.py` / `eq.py` / `prod.py` |

All imports in tool handlers and orchestrator use actual filenames (code works). Design docs are misleading.

**Fix:** Update `quantum_design.md`, `topology_design.md`, and `Willy.md` to reflect current filenames.

### MED-2 — `_step_to_dict()` Duplicated 3 Times

**Files:** `toolist_quantum.py:199-215`, `toolist_topology.py:185-201`, `toolist_simulation.py:257-273`

Three identical copies of the same ~15-line function. It converts a `StepResult` to a JSON-serializable dict with a `_step_result` sentinel.

**Fix:** Move to `errors.py` as `StepResult.to_dict()` method. Import once everywhere.

### MED-3 — `tools_skip_molecule_*` Logic Duplicated 4 Times

**Files:** `toolist_global.py`, `toolist_quantum.py`, `toolist_topology.py`, `toolist_simulation.py`

All four toolist files have near-identical `tools_skip_molecule_*` handler code that adds a molecule name to `config.json`'s `skipped_molecules` list.

**Fix:** Extract a shared `_apply_skip_molecule(name, reason, config_path)` helper function.

### MED-4 — No CI/CD Configuration

**File:** project root

No `.github/workflows/`, no `tox.ini`, no `Makefile`, no `Dockerfile`. `pyproject.toml` only declares 3 runtime dependencies (`gradio`, `openai`, `py3Dmol`) but `pytest`, `sklearn`, and other test dependencies are undeclared.

**Fix:** Add `[project.optional-dependencies]` with `test` and `dev` groups. Add a minimal GitHub Actions workflow that runs `pytest` on push.

### MED-5 — `llm_config.validate_config()` Lacks JSON Schema

**File:** `src/willy/llm_config.py`

Validation is done procedurally. Invalid config is caught by try/except blocks that silently continue instead of surfacing clear errors.

**Fix:** Add JSON Schema validation (using `jsonschema` library) or Pydantic models for `config.json` structure.

### MED-6 — No Retry / Backoff for DeepSeek API

**Files:** `agent_config.py`, `pipeline_orchestrator.py`

If the API key is valid but the service returns errors (rate limit, timeout, 5xx), there is no retry logic or exponential backoff. The `chat()` function in `agent_config.py` calls `client.chat.completions.create()` with no timeout and at most one retry via the outer `for attempt in range(3)` loop.

**Fix:** Add `timeout=60` and `max_retries=2` to the OpenAI client, or implement manual backoff in the tool-calling loop.

### MED-7 — `toolist_global.py` Internal Docstring Mismatch

**File:** `src/willy/toolist_global.py`

The module docstring reads `"""knowledge_tools.py — LLM function calling tools + TF-IDF 向量检索"""` but the actual filename is `toolist_global.py`.

**Fix:** Update docstring to match the current filename.

### MED-8 — `simulation/setup.py` Name Conflicts with Python Build Tool

**File:** `src/willy/simulation/setup.py`

The name `setup.py` collides with Python's standard build script convention. `from willy.simulation import setup` is ambiguous.

**Fix:** Rename to `md_setup.py` and update all imports.

---

## LOW

### LOW-1 — Mixed Chinese / English Error Messages

Error messages use both languages inconsistently: `raise ValueError(f"无法解析原子数: {fchk_path}")` alongside English `ErrorKind` enum values. Debug output is inconsistent for mixed-language audiences.

**Fix:** Standardize — either all English (preferred for code) or all Chinese.

### LOW-2 — `.md_counter` Not in `.gitignore`

**File:** `.gitignore`

The runtime counter file `.md_counter` (used by `simulation/setup.py` for run directory naming) is not listed in `.gitignore`. It could be accidentally committed.

**Fix:** Add `.md_counter` to `.gitignore`.

### LOW-3 — Incomplete Type Hints

Newer code (`toolist_*.py`, `pipeline_orchestrator.py`, `errors.py`) has good type annotations. Older code (`quantum/struct_g16.py`, `quantum/chg_g16.py`) is missing return type annotations. No `mypy` or `pyright` configuration exists.

**Fix:** Add `pyproject.toml` `[tool.mypy]` section. Gradually add type hints to older modules.

### LOW-4 — `benchmarks/` Directory is Minimal

**File:** `benchmarks/`

Only 2 files: `llm_score.py` (LLM output scoring) and `precheck_examples.jsonl` (10 NL test cases). No performance benchmarks, no regression tests, no simulation run comparisons.

**Fix:** Either expand with meaningful benchmarks or rename to `evals/` to reflect actual content.

### LOW-5 — Comments in `knowledge.md` Refer to Old Step Numbers

**File:** `docs/knowledge.md`

Some section references use old 8-step numbering (the pipeline currently has 7 steps). Not functionally broken but confusing.

---

## Summary Table

| Severity | Count | Key Themes |
|------|:---:|------|
| CRITICAL | 3 | API key leak, network exposure, missing tool definition |
| HIGH | 6 | Logging, bare raises, `shell=True`, orphaned doc, design doc gaps |
| MEDIUM | 8 | Code duplication, CI/CD, naming inconsistencies, validation |
| LOW | 5 | Type hints, mixed languages, `.gitignore`, benchmarks |
| **Total** | **22** | |

---

## Recommended Execution Order

### Sprint 1 (this week)
1. Rotate API key → placeholder in `.env` (CRIT-1)
2. `server_name="127.0.0.1"` in `app.py` (CRIT-2)
3. Register `tools_skip_molecule_simulation` in `SIMULATION_TOOLS` (CRIT-3)

### Sprint 2 (this month)
4. Extract `_step_to_dict` → `StepResult.to_dict()` (MED-2)
5. Wrap 11 bare `raise` → `StepResult` (HIGH-3)
6. Add `_logging.py` + replace `print()` (HIGH-1)
7. Write `docs/simulation_design.md` (HIGH-2)

### Sprint 3 (next iteration)
8. Refactor `shell=True` calls (HIGH-5)
9. Fix `frontend_api.py` command injection (HIGH-4)
10. Add CI/CD + `pyproject.toml` deps (MED-4)
11. Sync design doc filenames (MED-1)
