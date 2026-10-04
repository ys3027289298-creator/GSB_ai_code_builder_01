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


def test_failed_deploy_removes_stale_artifact_from_previous_run(tmp_path):
    project = PromptEngine().generate("build a CLI that greets a user")
    sim = DeploymentSimulator()
    workdir = tmp_path / "wd"
    first = sim.deploy(project, workdir)
    assert first.status == "succeeded"
    artifact = workdir / f"{project.name}.tar"
    assert artifact.is_file()

    project.files[0].content = "def broken(:\n"  # break the project
    second = sim.deploy(project, workdir)

    assert second.status == "failed"
    assert second.artifact is None
    assert not artifact.exists(), "stale artifact must not survive a failed deploy"


def test_failed_deploy_after_package_leaves_no_half_baked_artifact(tmp_path, monkeypatch):
    from ai_code_builder.deployment import StageReport

    project = PromptEngine().generate("hello")
    sim = DeploymentSimulator()
    monkeypatch.setattr(
        sim,
        "_deploy",
        lambda p, w: StageReport(name="deploy", status="failed",
                                 duration_seconds=0.0, log="simulated outage"),
    )
    workdir = tmp_path / "wd"
    report = sim.deploy(project, workdir)

    assert report.status == "failed"
    assert report.artifact is None
    assert not (workdir / f"{project.name}.tar").exists()
