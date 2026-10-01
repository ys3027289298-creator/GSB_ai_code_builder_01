"""Command-line interface for AI-Powered Code Builder.

Subcommands:

* ``acb plan "<prompt>"`` — show the structured plan only.
* ``acb generate "<prompt>" -o <dir>`` — write the generated project to disk.
* ``acb run <file>`` — execute a Python file in the secure sandbox.
* ``acb debug <file>`` — auto-debug a Python file (up to 3 attempts).
* ``acb commit <file> [-m msg]`` — record a version snapshot.
* ``acb history <file>`` — list known versions.
* ``acb diff <file> <a> <b>`` — show diff between two versions.
* ``acb deploy "<prompt>" [-o <dir>]`` — generate, debug, then simulate
  the full build/test/deploy pipeline. Prints a JSON report.
* ``acb build "<prompt>"`` — full end-to-end demo (generate + debug +
  version + deploy) with a human-readable summary.

Examples
--------
    acb build "build a CLI that greets a user"
    acb deploy "tiny http server returning JSON"
    acb run examples/hello.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .debugger import AutoDebugger
from .deployment import DeploymentSimulator
from .prompt_engine import PromptEngine
from .sandbox import SecureSandbox
from .versioning import VersionStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _engine(args: argparse.Namespace) -> PromptEngine:
    return PromptEngine(backend=args.backend, model=args.model)


def _sandbox(args: argparse.Namespace) -> SecureSandbox:
    return SecureSandbox(
        timeout=args.timeout,
        memory_limit_mb=args.memory_mb,
        cpu_seconds=args.cpu_seconds,
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_plan(args: argparse.Namespace) -> int:
    project = _engine(args).generate(args.prompt)
    if args.json:
        _print_json({"project": project.to_dict(), "tasks": project.tasks})
    else:
        print(f"Project: {project.name}")
        print(f"Summary: {project.summary}")
        print("Tasks:")
        for i, t in enumerate(project.tasks, 1):
            print(f"  {i}. {t}")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    project = _engine(args).generate(args.prompt, project_name=args.name)
    out = Path(args.output or f"./{project.name}")
    out.mkdir(parents=True, exist_ok=True)
    for f in project.files:
        target = out / f.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content, encoding="utf-8")
    if args.json:
        _print_json({"output": str(out), "project": project.to_dict()})
    else:
        print(f"Wrote {len(project.files)} file(s) to {out}")
        for f in project.files:
            print(f"  - {f.path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    sandbox = _sandbox(args)
    result = sandbox.run_file(args.file)
    if args.json:
        _print_json({
            "ok": result.ok,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
            "forbidden": result.forbidden,
            "duration_seconds": result.duration_seconds,
        })
    else:
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.timed_out:
            print("[sandbox] timed out", file=sys.stderr)
        if result.forbidden:
            print(f"[sandbox] forbidden: {result.forbidden}", file=sys.stderr)
    return 0 if result.ok else result.returncode or 1


def cmd_debug(args: argparse.Namespace) -> int:
    src = Path(args.file).read_text(encoding="utf-8")
    debugger = AutoDebugger(sandbox=_sandbox(args), max_attempts=args.max_attempts)
    report = debugger.run(src, filename=Path(args.file).name)
    if args.write and report.success:
        Path(args.file).write_text(report.final_source, encoding="utf-8")
    payload = {
        "success": report.success,
        "attempts": [
            {
                "attempt": a.attempt,
                "fix": a.fix,
                "ok": a.result.ok,
                "stderr": a.result.stderr,
                "stdout": a.result.stdout,
                "returncode": a.result.returncode,
            }
            for a in report.attempts
        ],
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"success={report.success} attempts={len(report.attempts)}")
        for a in report.attempts:
            print(f"  [{a.attempt}] fix={a.fix} ok={a.result.ok}")
    return 0 if report.success else 1


def cmd_commit(args: argparse.Namespace) -> int:
    store = VersionStore(args.store)
    content = Path(args.file).read_text(encoding="utf-8")
    v = store.commit(args.file, content, message=args.message or "")
    if args.json:
        _print_json(v.to_dict())
    else:
        print(f"{args.file}: v{v.version} sha={v.sha256[:12]} bytes={v.bytes}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    store = VersionStore(args.store)
    history = store.history(args.file)
    if args.json:
        _print_json([v.to_dict() for v in history])
    else:
        for v in history:
            print(f"v{v.version}\t{v.sha256[:12]}\t{v.bytes}B\t{v.message}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    store = VersionStore(args.store)
    print(store.diff(args.file, args.a, args.b))
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    project = _engine(args).generate(args.prompt, project_name=args.name)
    workdir = Path(args.output or f"./.acb_workspace/{project.name}")
    sim = DeploymentSimulator(sandbox=_sandbox(args))
    report = sim.deploy(project, workdir)
    if args.json:
        _print_json(report.to_dict())
    else:
        print(f"project={report.project} status={report.status} duration={report.duration_seconds}s")
        for s in report.stages:
            print(f"  {s.name}: {s.status} ({s.duration_seconds}s)")
            if s.log:
                for line in s.log.splitlines():
                    print(f"      {line}")
        if report.artifact:
            print(f"artifact: {report.artifact}")
    return 0 if report.status == "succeeded" else 1


def cmd_build(args: argparse.Namespace) -> int:
    """End-to-end demo: generate -> debug -> version -> deploy."""
    engine = _engine(args)
    sandbox = _sandbox(args)
    project = engine.generate(args.prompt, project_name=args.name)

    workdir = Path(args.output or f"./.acb_workspace/{project.name}")
    workdir.mkdir(parents=True, exist_ok=True)
    for f in project.files:
        (workdir / f.path).parent.mkdir(parents=True, exist_ok=True)
        (workdir / f.path).write_text(f.content, encoding="utf-8")

    debugger = AutoDebugger(sandbox=sandbox, max_attempts=args.max_attempts)
    debug_report = debugger.run(
        (workdir / project.entrypoint).read_text(encoding="utf-8"),
        filename=project.entrypoint,
    )
    if debug_report.success:
        (workdir / project.entrypoint).write_text(debug_report.final_source, encoding="utf-8")
        # Sync the in-memory project so the deployment simulator does not
        # re-materialise the original (un-debugged) entrypoint and overwrite
        # the repaired source on disk.
        for f in project.files:
            if f.path == project.entrypoint:
                f.content = debug_report.final_source
                break

    store = VersionStore(args.store)
    version = store.commit(
        project.entrypoint,
        (workdir / project.entrypoint).read_text(encoding="utf-8"),
        message=f"acb build: {project.summary}",
    )

    sim = DeploymentSimulator(sandbox=sandbox)
    deploy_report = sim.deploy(project, workdir)

    summary = {
        "project": project.to_dict(),
        "debug": {
            "success": debug_report.success,
            "attempts": len(debug_report.attempts),
        },
        "version": version.to_dict(),
        "deployment": deploy_report.to_dict(),
        "workdir": str(workdir),
    }
    if args.json:
        _print_json(summary)
    else:
        print(f"== Project: {project.name} ==")
        print(f"Summary: {project.summary}")
        print(f"Files written to {workdir}:")
        for f in project.files:
            print(f"  - {f.path}")
        print(f"Auto-debug: success={debug_report.success} attempts={len(debug_report.attempts)}")
        print(f"Versioned: {version.file}@v{version.version} sha={version.sha256[:12]}")
        print(f"Deployment: {deploy_report.status} ({deploy_report.duration_seconds}s)")
        for s in deploy_report.stages:
            print(f"  - {s.name}: {s.status}")
    rc = 0 if (debug_report.success and deploy_report.status == "succeeded") else 1
    return rc


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="acb", description="AI-Powered Code Builder")
    p.add_argument("--backend", default="rule", choices=["rule", "llm"], help="Prompt engine backend")
    p.add_argument("--model", default="gpt-4o-mini", help="Model name for the llm backend")
    p.add_argument("--timeout", type=float, default=5.0, help="Sandbox wall-clock timeout (s)")
    p.add_argument("--memory-mb", type=int, default=256, help="Sandbox memory cap (MB)")
    p.add_argument("--cpu-seconds", type=int, default=4, help="Sandbox CPU cap (s)")
    p.add_argument("--max-attempts", type=int, default=3, help="Auto-debug attempts")
    p.add_argument("--store", default=".acb_versions", help="Version store directory")
    p.add_argument("--json", action="store_true", help="Emit JSON output")

    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("plan", help="Plan a project from a prompt")
    sp.add_argument("prompt")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("generate", help="Generate a project from a prompt")
    sp.add_argument("prompt")
    sp.add_argument("-o", "--output", help="Output directory")
    sp.add_argument("--name", help="Override project name")
    sp.set_defaults(func=cmd_generate)

    sp = sub.add_parser("run", help="Run a python file in the sandbox")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("debug", help="Auto-debug a python file")
    sp.add_argument("file")
    sp.add_argument("--write", action="store_true", help="Write the repaired source back")
    sp.set_defaults(func=cmd_debug)

    sp = sub.add_parser("commit", help="Snapshot a file in the version store")
    sp.add_argument("file")
    sp.add_argument("-m", "--message", default="")
    sp.set_defaults(func=cmd_commit)

    sp = sub.add_parser("history", help="Show the version history of a file")
    sp.add_argument("file")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("diff", help="Diff two versions of a file")
    sp.add_argument("file")
    sp.add_argument("a", type=int)
    sp.add_argument("b", type=int)
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("deploy", help="Run the deployment simulator on a generated project")
    sp.add_argument("prompt")
    sp.add_argument("-o", "--output")
    sp.add_argument("--name")
    sp.set_defaults(func=cmd_deploy)

    sp = sub.add_parser("build", help="Full end-to-end demo (generate+debug+version+deploy)")
    sp.add_argument("prompt")
    sp.add_argument("-o", "--output")
    sp.add_argument("--name")
    sp.set_defaults(func=cmd_build)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
