from ai_code_builder.prompt_engine import GeneratedProject, PromptEngine


def test_generates_cli_project_from_cli_prompt():
    project = PromptEngine().generate("build a CLI that greets a user")
    assert isinstance(project, GeneratedProject)
    assert project.entrypoint == "main.py"
    assert any(f.path == "main.py" for f in project.files)
    main = next(f for f in project.files if f.path == "main.py")
    assert "argparse" in main.content
    assert project.tasks  # non-empty plan


def test_generates_web_project_from_web_prompt():
    project = PromptEngine().generate("tiny http server returning JSON")
    main = next(f for f in project.files if f.path == "main.py")
    assert "HTTPServer" in main.content


def test_generates_math_project():
    project = PromptEngine().generate("math utility for factorials and means")
    main = next(f for f in project.files if f.path == "main.py")
    assert "factorial" in main.content


def test_falls_back_to_hello_for_unrelated_prompt():
    project = PromptEngine().generate("xyzzy")
    main = next(f for f in project.files if f.path == "main.py")
    assert "Hello from" in main.content


def test_plan_returns_task_list():
    tasks = PromptEngine().plan("build a CLI")
    assert isinstance(tasks, list)
    assert len(tasks) >= 4


def test_empty_prompt_raises():
    import pytest

    with pytest.raises(ValueError):
        PromptEngine().generate("   ")


def test_unknown_backend_raises():
    import pytest

    with pytest.raises(ValueError):
        PromptEngine(backend="bogus")
