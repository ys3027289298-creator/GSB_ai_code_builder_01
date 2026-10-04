"""Regression tests for the six cross-module state-passing bug classes.

1. Empty prompt crashes the CLI with an unhandled traceback.
2. Overlong prompt input is accepted without any bound.
3. Duplicate submission of the same file under different path spellings
   fragments the version log.
4. Sandbox timeout kills only the direct child; grandchildren leak.
5. Version rollback leaves the version store and the workspace inconsistent.
6. A failed simulated deploy leaves half-baked artifacts behind.
"""

import hashlib
import time

import pytest

from ai_code_builder.cli import main
from ai_code_builder.deployment import DeploymentSimulator
from ai_code_builder.prompt_engine import PromptEngine
from ai_code_builder.sandbox import SecureSandbox
from ai_code_builder.versioning import VersionStore


# --- 1. empty prompt -------------------------------------------------------


def test_cli_rejects_empty_prompt_cleanly(capsys):
    rc = main(["plan", ""])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert "non-empty" in captured.err
    assert "Traceback" not in captured.err


def test_cli_empty_prompt_leaves_no_partial_state(capsys, tmp_path):
    workdir = tmp_path / "wd"
    store = tmp_path / "v"
    rc = main(["--store", str(store), "build", "   ", "-o", str(workdir)])
    capsys.readouterr()
    assert rc == 2
    assert not workdir.exists()
    assert not store.exists()


# --- 2. overlong input -----------------------------------------------------


def test_overlong_prompt_rejected():
    from ai_code_builder.prompt_engine import MAX_PROMPT_CHARS

    with pytest.raises(ValueError, match="too long"):
        PromptEngine().generate("x" * (MAX_PROMPT_CHARS + 1))


def test_prompt_at_max_length_accepted():
    from ai_code_builder.prompt_engine import MAX_PROMPT_CHARS

    project = PromptEngine().generate("a" * MAX_PROMPT_CHARS)
    assert project.files


def test_cli_overlong_prompt_is_a_clean_error(capsys):
    from ai_code_builder.prompt_engine import MAX_PROMPT_CHARS

    rc = main(["plan", "x" * (MAX_PROMPT_CHARS + 1)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "too long" in captured.err
    assert "Traceback" not in captured.err


# --- 3. duplicate submission -----------------------------------------------


def test_commit_unifies_path_spellings(tmp_path):
    store = VersionStore(tmp_path / "s")
    store.commit("./a.py", "print(1)\n")
    store.commit("a.py", "print(2)\n")
    assert store.list_files() == ["a.py"]
    assert [v.version for v in store.history("a.py")] == [1, 2]


def test_duplicate_commit_idempotent_across_spellings(tmp_path):
    store = VersionStore(tmp_path / "s")
    v1 = store.commit("./a.py", "print(1)\n")
    v2 = store.commit("a.py", "print(1)\n")
    assert v1.version == v2.version
    assert len(store.history("a.py")) == 1


# --- 4. sandbox timeout ----------------------------------------------------


def test_timeout_kills_whole_process_tree(tmp_path):
    marker = tmp_path / "marker"
    src = (
        "import os, time\n"
        "if os.fork() == 0:\n"
        "    time.sleep(1.0)\n"
        f"    open({str(marker)!r}, 'w').write('leaked')\n"
        "    os._exit(0)\n"
        "while True:\n"
        "    time.sleep(0.05)\n"
    )
    result = SecureSandbox(timeout=0.5).run_source(src)
    assert result.timed_out
    assert result.returncode == 124
    time.sleep(2.0)  # outlive the grandchild's scheduled marker write
    assert not marker.exists()


# --- 5. rollback / workspace consistency -----------------------------------


def test_restore_keeps_store_and_workspace_consistent(tmp_path):
    store = VersionStore(tmp_path / "s")
    store.commit("f.py", "v1\n")
    store.commit("f.py", "v2\n")
    target = tmp_path / "f.py"
    store.restore("f.py", 1, target)
    history = store.history("f.py")
    latest = history[-1]
    # The store's latest version must describe what is now in the workspace.
    assert latest.sha256 == hashlib.sha256(b"v1\n").hexdigest()
    assert store.read("f.py", latest.version) == target.read_text()


# --- 6. half-baked state after failed deploy --------------------------------


def test_failed_deploy_removes_stale_artifact(tmp_path):
    workdir = tmp_path / "wd"
    sim = DeploymentSimulator()
    project = PromptEngine().generate("build a CLI that greets a user")
    first = sim.deploy(project, workdir)
    assert first.status == "succeeded"
    tar = workdir / f"{project.name}.tar"
    assert tar.exists()

    project.files[0].content = "def broken(:\n"  # lint will fail
    second = sim.deploy(project, workdir)
    assert second.status == "failed"
    assert second.artifact is None
    assert not tar.exists()


def test_failed_package_leaves_no_partial_artifact(tmp_path):
    project = PromptEngine().generate("build a CLI")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    for f in project.files:
        (workdir / f.path).parent.mkdir(parents=True, exist_ok=True)
        (workdir / f.path).write_text(f.content)
    victim = workdir / project.entrypoint
    victim.chmod(0)  # unreadable -> tarfile.add raises mid-package
    try:
        report = DeploymentSimulator()._package(project, workdir)
    finally:
        victim.chmod(0o644)
    assert report.status == "failed"
    assert not (workdir / f"{project.name}.tar").exists()
