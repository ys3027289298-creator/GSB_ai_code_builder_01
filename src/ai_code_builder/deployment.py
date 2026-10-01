"""Deployment pipeline simulator.

Simulates the build / test / deploy pipeline of a typical CI system. The
simulator never touches a real cloud provider; it just produces a
realistic, structured log so the rest of the system can demonstrate a
full end-to-end flow.

Each stage runs sequentially. If a stage fails the pipeline is aborted
and reported as ``failed``. The simulator integrates with the secure
sandbox to run an actual smoke test against the generated entrypoint.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .prompt_engine import GeneratedProject
from .sandbox import SandboxResult, SecureSandbox


@dataclass
class StageReport:
    name: str
    status: str  # "passed" | "failed" | "skipped"
    duration_seconds: float
    log: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DeploymentReport:
    project: str
    status: str  # "succeeded" | "failed"
    started_at: float
    finished_at: float
    stages: list[StageReport] = field(default_factory=list)
    artifact: Optional[str] = None

    @property
    def duration_seconds(self) -> float:
        return round(self.finished_at - self.started_at, 4)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_seconds"] = self.duration_seconds
        return d


class DeploymentSimulator:
    """Simulates a multi-stage deployment pipeline."""

    DEFAULT_STAGES = ("lint", "build", "test", "package", "deploy")

    def __init__(
        self,
        sandbox: Optional[SecureSandbox] = None,
        stages: Optional[tuple[str, ...]] = None,
    ) -> None:
        self.sandbox = sandbox or SecureSandbox()
        self.stages = stages or self.DEFAULT_STAGES

    # ------------------------------------------------------------------

    def deploy(self, project: GeneratedProject, workdir: Path | str) -> DeploymentReport:
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)

        # Materialise files so the simulator can run real checks.
        for f in project.files:
            target = workdir / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f.content, encoding="utf-8")

        started = time.time()
        report = DeploymentReport(
            project=project.name,
            status="succeeded",
            started_at=started,
            finished_at=started,
        )

        runners = {
            "lint": lambda: self._lint(project),
            "build": lambda: self._build(project, workdir),
            "test": lambda: self._test(project, workdir),
            "package": lambda: self._package(project, workdir),
            "deploy": lambda: self._deploy(project, workdir),
        }

        for stage in self.stages:
            runner = runners.get(stage)
            if runner is None:
                report.stages.append(
                    StageReport(name=stage, status="skipped", duration_seconds=0.0,
                                log=f"unknown stage {stage!r}")
                )
                continue
            stage_report = runner()
            report.stages.append(stage_report)
            if stage_report.status == "failed":
                report.status = "failed"
                break
            if stage == "package" and stage_report.status == "passed":
                report.artifact = str(workdir / f"{project.name}.tar")

        report.finished_at = time.time()
        return report

    # ------------------------------------------------------------------
    # Individual stages
    # ------------------------------------------------------------------

    def _lint(self, project: GeneratedProject) -> StageReport:
        t0 = time.monotonic()
        problems: list[str] = []
        for f in project.files:
            if f.language != "python":
                continue
            try:
                compile(f.content, f.path, "exec")
            except SyntaxError as e:
                problems.append(f"{f.path}: {e.msg} (line {e.lineno})")
        if problems:
            return StageReport(
                name="lint",
                status="failed",
                duration_seconds=round(time.monotonic() - t0, 4),
                log="\n".join(problems),
            )
        return StageReport(
            name="lint",
            status="passed",
            duration_seconds=round(time.monotonic() - t0, 4),
            log=f"Linted {sum(1 for f in project.files if f.language == 'python')} python file(s)",
        )

    def _build(self, project: GeneratedProject, workdir: Path) -> StageReport:
        t0 = time.monotonic()
        for f in project.files:
            if not (workdir / f.path).is_file():
                return StageReport(
                    name="build",
                    status="failed",
                    duration_seconds=round(time.monotonic() - t0, 4),
                    log=f"missing file {f.path}",
                )
        return StageReport(
            name="build",
            status="passed",
            duration_seconds=round(time.monotonic() - t0, 4),
            log=f"Materialised {len(project.files)} file(s) into {workdir}",
        )

    def _test(self, project: GeneratedProject, workdir: Path) -> StageReport:
        t0 = time.monotonic()
        entry = workdir / project.entrypoint
        if not entry.is_file():
            return StageReport(
                name="test",
                status="failed",
                duration_seconds=round(time.monotonic() - t0, 4),
                log=f"entrypoint missing: {project.entrypoint}",
            )
        result: SandboxResult = self.sandbox.run_file(entry, cwd=workdir)
        status = "passed" if result.ok else "failed"
        log = (result.stdout or "") + (("\n[stderr]\n" + result.stderr) if result.stderr else "")
        if result.timed_out:
            log += "\n[sandbox] timed out"
        if result.forbidden:
            log += f"\n[sandbox] forbidden: {result.forbidden}"
        return StageReport(
            name="test",
            status=status,
            duration_seconds=round(time.monotonic() - t0, 4),
            log=log,
        )

    def _package(self, project: GeneratedProject, workdir: Path) -> StageReport:
        import tarfile

        t0 = time.monotonic()
        artifact = workdir / f"{project.name}.tar"
        try:
            with tarfile.open(artifact, "w") as tar:
                for f in project.files:
                    tar.add(workdir / f.path, arcname=f.path)
        except Exception as e:  # noqa: BLE001
            return StageReport(
                name="package",
                status="failed",
                duration_seconds=round(time.monotonic() - t0, 4),
                log=f"package error: {e}",
            )
        return StageReport(
            name="package",
            status="passed",
            duration_seconds=round(time.monotonic() - t0, 4),
            log=f"Created artifact {artifact.name} ({artifact.stat().st_size} bytes)",
        )

    def _deploy(self, project: GeneratedProject, workdir: Path) -> StageReport:
        t0 = time.monotonic()
        log_lines = [
            f"[deploy] target=simulated://{project.name}",
            "[deploy] uploading artifact... ok",
            "[deploy] running migrations... ok",
            "[deploy] flipping traffic 0% -> 100%... ok",
            "[deploy] health check ... 200 OK",
            f"[deploy] release v{int(time.time())} live",
        ]
        return StageReport(
            name="deploy",
            status="passed",
            duration_seconds=round(time.monotonic() - t0, 4),
            log="\n".join(log_lines),
        )
