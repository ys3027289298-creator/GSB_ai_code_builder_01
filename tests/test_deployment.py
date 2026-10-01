from ai_code_builder.deployment import DeploymentSimulator
from ai_code_builder.prompt_engine import PromptEngine


def test_full_pipeline_succeeds(tmp_path):
    project = PromptEngine().generate("build a CLI that greets a user")
    sim = DeploymentSimulator()
    report = sim.deploy(project, tmp_path / "wd")
    assert report.status == "succeeded"
    names = [s.name for s in report.stages]
    assert names == list(sim.DEFAULT_STAGES)
    assert all(s.status == "passed" for s in report.stages)
    assert report.artifact and "tar" in report.artifact


def test_lint_failure_aborts(tmp_path):
    project = PromptEngine().generate("hello")
    # Inject a syntax error
    project.files[0].content = "def broken(:\n"
    sim = DeploymentSimulator()
    report = sim.deploy(project, tmp_path / "wd")
    assert report.status == "failed"
    assert report.stages[0].name == "lint"
    assert report.stages[0].status == "failed"


def test_unknown_stage_skipped(tmp_path):
    project = PromptEngine().generate("hello")
    sim = DeploymentSimulator(stages=("lint", "noop", "build"))
    report = sim.deploy(project, tmp_path / "wd")
    statuses = {s.name: s.status for s in report.stages}
    assert statuses["noop"] == "skipped"
    assert statuses["lint"] == "passed"
