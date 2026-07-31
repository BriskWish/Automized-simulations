"""
conftest.py —— 共享 fixtures 和 mock 基础设施。

本测试套件专注于错误路径测试 —— 所有外部依赖均已 mock，
因此测试无需 GROMACS/Gaussian/ORCA/Sobtop 即可运行。
"""

from __future__ import annotations
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# 确保 src/willy 在 path 中
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


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
        "md": {
            "dt": 0.001, "ref_t": 298.15, "ref_p": 1.01325,
            "eq_ns": 10, "prod_ns": 10,
        },
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
        "md": {
            "dt": 0.001, "ref_t": 298.15, "ref_p": 1.01325,
            "eq_ns": 10, "prod_ns": 10,
        },
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
