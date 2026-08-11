import json

from willy.errors import StepResult
from willy.pipeline_orchestrator import PipelineOrchestrator


def test_pipeline_step_result_is_written_to_the_bound_run(tmp_path):
    run_dir = tmp_path / "md_run" / "md_structured"
    run_dir.mkdir(parents=True)
    orchestrator = PipelineOrchestrator(backend="g16", use_llm=False)
    orchestrator._run_dir = run_dir

    orchestrator._record_step_result(
        StepResult(
            "eq",
            9,
            False,
            duration_s=0.25,
            target_type="stage",
            target="eq",
        ),
        "GROMACS 三点式退火平衡",
    )

    rows = [
        json.loads(line)
        for line in (run_dir / "logs" / "structured.jsonl").read_text().splitlines()
    ]
    assert rows[0]["event_code"] == "step_result"
    assert rows[0]["step"] == 9
    assert rows[0]["step_name"] == "GROMACS 三点式退火平衡"
    assert rows[0]["outcome"] == "failed"
    assert rows[0]["duration_ms"] == 250.0
