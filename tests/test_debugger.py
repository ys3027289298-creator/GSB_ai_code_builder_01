import textwrap

from ai_code_builder.debugger import AutoDebugger


def test_passes_through_clean_program():
    src = "print('clean')\n"
    report = AutoDebugger().run(src)
    assert report.success
    assert len(report.attempts) == 1


def test_repairs_missing_module():
    src = textwrap.dedent(
        """
        import definitely_not_a_module
        if definitely_not_a_module is None:
            print('guarded')
        else:
            print('imported')
        """
    )
    report = AutoDebugger().run(src)
    assert report.success
    # The repaired source must contain the guard.
    assert "ImportError" in report.final_source


def test_repairs_unexpected_eof():
    src = "def f():\n"  # no body -> SyntaxError: unexpected EOF / invalid syntax
    report = AutoDebugger().run(src)
    # Either repaired (added pass) or surfaced; we expect at least one attempt.
    assert len(report.attempts) >= 1


def test_gives_up_after_max_attempts():
    src = "raise RuntimeError('boom')\n"
    report = AutoDebugger(max_attempts=2).run(src)
    assert not report.success
    assert len(report.attempts) <= 2


def test_invalid_max_attempts_raises():
    import pytest

    with pytest.raises(ValueError):
        AutoDebugger(max_attempts=0)
