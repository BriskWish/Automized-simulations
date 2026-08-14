"""
conftest.py —— 共享 fixtures 和 mock 基础设施。

测试默认使用临时工作区和替身，不执行外部科学计算。标记为
``external`` 的真实工具 smoke 仅在显式传入 ``--run-external`` 时运行；
真实 LLM 连通性检查需要单独传入 ``--run-llm-connection``。
"""

from __future__ import annotations
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest


_TEST_LAYER_BY_FILE = {
    "test_env_checker.py": "unit",
    "test_env_registry.py": "unit",
    "test_errors.py": "unit",
    "test_log_parsers.py": "unit",
    "test_pipeline_state.py": "unit",
    "test_llm_config.py": "unit",
    "test_workflow_config.py": "contract",
    "test_simulation_protocol.py": "contract",
    "test_toolist_global.py": "contract",
    "test_quantum_input_audit.py": "contract",
    "test_toolist_quantum_topology.py": "contract",
    "test_toolist_simulation.py": "contract",
    "test_action_contract.py": "contract",
    "test_top_assembly.py": "contract",
    "test_frontend_api.py": "integration",
    "test_app_ui.py": "integration",
    "test_layer_agent.py": "integration",
    "test_mdrun_eta.py": "integration",
    "test_process_lifecycle.py": "integration",
    "test_remote_execution.py": "unit",
    "test_run_provenance.py": "integration",
    "test_run_store.py": "integration",
    "test_step_registry.py": "contract",
    "test_pipeline_launch.py": "integration",
    "test_pipeline_orchestrator.py": "integration",
    "test_postprocess.py": "integration",
    "test_run_assistant.py": "integration",
    "test_run_control.py": "integration",
    "test_simulation_execution.py": "integration",
    "test_mdrun_knowledge.py": "contract",
    "test_gateway.py": "integration",
    "test_gateway_admin.py": "integration",
    "test_gateway_archive.py": "integration",
    "test_managed_gateway.py": "integration",
    "test_topology_contract.py": "integration",
    "test_external_profile_evidence.py": "contract",
    "test_release_staging.py": "contract",
}


def pytest_addoption(parser):
    parser.addoption(
        "--run-external",
        action="store_true",
        default=False,
        help="run tests that invoke installed scientific tools",
    )
    parser.addoption(
        "--run-llm-connection",
        action="store_true",
        default=False,
        help="run the real OpenAI-compatible LLM connection test",
    )
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="run browser tests against a disposable fake-executor service",
    )


def pytest_collection_modifyitems(config, items):
    """Classify every pytest case and keep real-tool smoke opt-in."""
    run_external = config.getoption("--run-external")
    run_llm_connection = config.getoption("--run-llm-connection")
    run_e2e = config.getoption("--run-e2e")
    skip_external = pytest.mark.skip(reason="requires --run-external")
    skip_llm_connection = pytest.mark.skip(reason="requires --run-llm-connection")
    skip_e2e = pytest.mark.skip(reason="requires --run-e2e")

    for item in items:
        category = _TEST_LAYER_BY_FILE.get(Path(str(item.fspath)).name)
        if category:
            item.add_marker(getattr(pytest.mark, category))
        if "external" in item.keywords and not run_external:
            item.add_marker(skip_external)
        if "llm_connection" in item.keywords and not run_llm_connection:
            item.add_marker(skip_llm_connection)
        if "e2e" in item.keywords and not run_e2e:
            item.add_marker(skip_e2e)

# 确保 src/willy 在 path 中
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _v2_md(**overrides):
    from willy.simulation.protocol import default_md_config
    md = default_md_config()
    md.update(overrides)
    return md


# ============================================================
# 临时目录 Fixtures
# ============================================================

@pytest.fixture
def tmp_project_root(tmp_path, monkeypatch):
    """创建一个临时项目根目录，包含 struct/ 子目录。

    覆盖 _paths.get_project_root() 以返回此临时路径，
    这样测试就不会触及真实文件系统。
    """
    struct_dir = tmp_path / "struct"
    struct_dir.mkdir()
    (tmp_path / "config.json").write_text(json.dumps({
        "backend": "g16",
        "residues": {"LiTFSI": 10, "FEC": 120},
        "molecules": {
            "LiTFSI": {"charge": 0, "spin": 1, "basis": "b3lyp/6-311+g(d,p)", "solvent": "acetone"},
            "FEC": {"charge": 0, "spin": 1, "basis": "b3lyp/6-311+g(d,p)", "solvent": "acetone"},
        },
        "md": _v2_md(),
    }, indent=2))
    monkeypatch.setenv("WILLY_ROOT", str(tmp_path))
    # 重新 import _paths 以使用新环境变量
    import willy._paths
    monkeypatch.setattr(willy._paths, "get_project_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def sample_config_dict():
    """最小合法 config dict。"""
    return {
        "backend": "g16",
        "residues": {"LiTFSI": 10, "FEC": 120},
        "molecules": {
            "LiTFSI": {"charge": 0, "spin": 1, "basis": "b3lyp/6-311+g(d,p)"},
            "FEC": {"charge": 0, "spin": 1, "basis": "b3lyp/6-311+g(d,p)"},
        },
        "md": _v2_md(),
    }


# ============================================================
# Mock Fixtures
# ============================================================

@pytest.fixture
def mock_llm_client():
    """返回一个 mock OpenAI 客户端。"""
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock()
    return client


@pytest.fixture
def mock_subprocess_run():
    """Mock subprocess.run 以返回成功。"""
    with patch("subprocess.run") as m:
        result = MagicMock()
        result.returncode = 0
        result.stdout = "Normal termination"
        result.stderr = ""
        m.return_value = result
        yield m


@pytest.fixture
def mock_failed_subprocess_run():
    """Mock subprocess.run 以返回失败。"""
    with patch("subprocess.run") as m:
        result = MagicMock()
        result.returncode = 1
        result.stdout = "Error"
        result.stderr = "segmentation fault"
        m.return_value = result
        yield m


# ============================================================
# 共享的 StepResult 构造器
# ============================================================

@pytest.fixture
def make_step_result():
    """工厂函数：创建带有预设值的 StepResult。"""
    from willy.errors import StepResult, StepError, ErrorKind

    def _make(success=True, step_name="test_step", step_index=1,
              error_kind=None, error_message="", raw_output="", hint=""):
        err = None
        if not success or error_kind:
            err = StepError(
                kind=error_kind or ErrorKind.UNKNOWN,
                message=error_message,
                raw_output=raw_output,
                hint=hint,
            )
        return StepResult(
            step_name=step_name, step_index=step_index, success=success,
            error=err, outputs={}, artifacts=[], duration_s=0.5,
        )
    return _make
