import textwrap

from ai_code_builder.sandbox import SecureSandbox, scan_for_forbidden


def test_runs_simple_program():
    sandbox = SecureSandbox(timeout=5)
    result = sandbox.run_source("print('hello')\n")
    assert result.ok
    assert "hello" in result.stdout


def test_captures_nonzero_exit():
    sandbox = SecureSandbox(timeout=5)
    result = sandbox.run_source("import sys; sys.exit(7)\n")
    assert not result.ok
    assert result.returncode == 7


def test_blocks_forbidden_patterns():
    sandbox = SecureSandbox(timeout=5)
    src = "import subprocess\nsubprocess.run(['ls'])\n"
    result = sandbox.run_source(src)
    assert not result.ok
    assert "subprocess" in result.forbidden


def test_allow_forbidden_flag():
    sandbox = SecureSandbox(timeout=5, allow_forbidden=True)
    result = sandbox.run_source("import subprocess; print('ok')\n")
    assert result.ok or result.returncode == 0


def test_timeout_is_enforced():
    sandbox = SecureSandbox(timeout=0.5)
    src = textwrap.dedent(
        """
        import time
        while True:
            time.sleep(0.1)
        """
    )
    result = sandbox.run_source(src)
    assert result.timed_out
    assert result.returncode == 124


def test_scan_for_forbidden_detects_eval():
    assert "eval-exec" in scan_for_forbidden("eval('1+1')")


def test_truncates_huge_output():
    sandbox = SecureSandbox(timeout=5, output_budget=128)
    src = "print('A' * 4096)\n"
    result = sandbox.run_source(src)
    assert "[truncated" in result.stdout


def test_timeout_kills_entire_process_group(tmp_path):
    """A timed-out run must not leak grandchildren: the child is executed in
    its own session so the whole process group can be reaped."""
    import os
    import time

    sentinel = tmp_path / "grandchild-was-here"
    src = textwrap.dedent(
        f"""
        import os, time
        if os.fork() == 0:
            time.sleep(1.5)
            open({str(sentinel)!r}, "w").write("leaked")
            os._exit(0)
        time.sleep(30)
        """
    )
    result = SecureSandbox(timeout=0.5).run_source(src)
    assert result.timed_out
    assert result.returncode == 124
    time.sleep(2.0)  # a leaked grandchild would fire within this window
    assert not os.path.exists(sentinel)
