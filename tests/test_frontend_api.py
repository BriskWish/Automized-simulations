"""前端运行控制的安全默认值测试。"""

from unittest.mock import patch

import willy.frontend_api as frontend_api


def test_kill_orphans_uses_argument_lists():
    with patch.object(frontend_api.subprocess, "run") as run:
        frontend_api._kill_orphans()

    assert run.call_count > 0
    for call in run.call_args_list:
        command = call.args[0]
        assert command[:3] == ["pkill", "-9", "-f"]
        assert isinstance(command[3], str)


def test_clean_stop_unlinks_only_known_root_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend_api, "ROOT", tmp_path)
    for name in ("model.inp", "model.pdb"):
        (tmp_path / name).write_text("artifact")

    with patch.object(frontend_api, "_kill_process_group"), \
         patch.object(frontend_api, "_kill_orphans"), \
         patch.object(frontend_api, "_reset_status_idle"):
        message = frontend_api.stop_pipeline(clean=True)

    assert message == "已中止，产物已清理"
    assert not (tmp_path / "model.inp").exists()
    assert not (tmp_path / "model.pdb").exists()
