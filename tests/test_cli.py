import json

from ai_code_builder.cli import main


def test_plan_command(capsys):
    rc = main(["--json", "plan", "build a CLI"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 0
    assert payload["project"]["entrypoint"] == "main.py"
    assert payload["tasks"]


def test_generate_command(capsys, tmp_path):
    out_dir = tmp_path / "proj"
    rc = main(["--json", "generate", "build a CLI", "-o", str(out_dir)])
    assert rc == 0
    assert (out_dir / "main.py").is_file()
    payload = json.loads(capsys.readouterr().out)
    assert payload["output"] == str(out_dir)


def test_build_end_to_end(capsys, tmp_path):
    rc = main([
        "--json",
        "--store", str(tmp_path / "v"),
        "build", "build a CLI that greets",
        "-o", str(tmp_path / "wd"),
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["debug"]["success"]
    assert payload["deployment"]["status"] == "succeeded"
    assert payload["version"]["version"] >= 1


def test_run_executes_file(capsys, tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("print('cli-run-ok')\n")
    rc = main(["run", str(f)])
    assert rc == 0
    assert "cli-run-ok" in capsys.readouterr().out


def test_build_deploy_uses_debugged_source(monkeypatch, capsys, tmp_path):
    """Regression: cmd_build must not let DeploymentSimulator overwrite the
    debugger's repaired entrypoint with the original broken content.

    Ref: PR review BUG_pr-review-job-77d8ae0c17994a05bc01dffdb30f326b_0001.
    """
    from ai_code_builder import prompt_engine as pe

    broken_source = (
        "import definitely_not_a_real_module_xyz\n"
        "if definitely_not_a_real_module_xyz is None:\n"
        "    print('guarded')\n"
        "else:\n"
        "    print('imported')\n"
    )

    def fake_generate(self, prompt, project_name=None):
        return pe.GeneratedProject(
            name="broken-fixture",
            summary=prompt,
            entrypoint="main.py",
            files=[pe.GeneratedFile(path="main.py", content=broken_source, language="python")],
            tasks=["plan"],
        )

    monkeypatch.setattr(pe.PromptEngine, "generate", fake_generate)

    rc = main([
        "--json",
        "--store", str(tmp_path / "v"),
        "build", "anything",
        "-o", str(tmp_path / "wd"),
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    # The debugger had to do real repair work.
    assert payload["debug"]["success"]
    assert payload["debug"]["attempts"] >= 2
    # Deployment must succeed end-to-end (test stage uses the *debugged* file).
    assert payload["deployment"]["status"] == "succeeded"
    assert all(s["status"] == "passed" for s in payload["deployment"]["stages"])
    # And the file on disk must contain the guarded import, not the original.
    final = (tmp_path / "wd" / "main.py").read_text()
    assert "ImportError" in final


def test_commit_history_diff(capsys, tmp_path):
    f = tmp_path / "x.py"
    store = tmp_path / "store"

    f.write_text("print(1)\n")
    main(["--store", str(store), "commit", str(f), "-m", "first"])
    f.write_text("print(2)\n")
    main(["--store", str(store), "commit", str(f), "-m", "second"])

    capsys.readouterr()  # clear
    rc = main(["--json", "--store", str(store), "history", str(f)])
    assert rc == 0
    history = json.loads(capsys.readouterr().out)
    assert len(history) == 2

    rc = main(["--store", str(store), "diff", str(f), "1", "2"])
    out = capsys.readouterr().out
    assert "-print(1)" in out and "+print(2)" in out
