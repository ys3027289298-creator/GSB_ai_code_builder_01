"""Auto-debugging loop.

The debugger consumes a generated source string, runs it inside the secure
sandbox, and tries to repair common syntax / runtime errors without human
intervention. It retries up to ``max_attempts`` times (default 3) and
returns a structured report.

The repair strategies are intentionally simple, deterministic, and safe
(string-level fixes that always reduce or keep the program's behaviour):

* Missing modules → wrap their import in ``try/except ImportError`` and
  set the symbol to ``None`` so downstream code can guard against it.
* ``IndentationError`` → re-indent the offending line to match its
  surrounding block.
* ``NameError`` for printf-style undefined names → inject a ``= None``
  default at module top.
* ``SyntaxError: unexpected EOF`` → append a ``pass`` statement.

Any unrepairable error is surfaced verbatim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .sandbox import SandboxResult, SecureSandbox


@dataclass
class DebugAttempt:
    attempt: int
    fix: str
    result: SandboxResult


@dataclass
class DebugResult:
    success: bool
    final_source: str
    attempts: list[DebugAttempt] = field(default_factory=list)

    @property
    def last_result(self) -> Optional[SandboxResult]:
        return self.attempts[-1].result if self.attempts else None


_MISSING_MODULE_RE = re.compile(r"No module named '(?P<name>[\w\.]+)'")
_NAME_ERROR_RE = re.compile(r"NameError: name '(?P<name>[\w_]+)' is not defined")
_EOF_RE = re.compile(r"SyntaxError: unexpected EOF while parsing")
_INDENT_RE = re.compile(r"IndentationError: .* line (?P<line>\d+)")


class AutoDebugger:
    """Iteratively run + repair source code."""

    def __init__(self, sandbox: Optional[SecureSandbox] = None, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.sandbox = sandbox or SecureSandbox()
        self.max_attempts = max_attempts

    # ------------------------------------------------------------------

    def run(self, source: str, *, filename: str = "main.py") -> DebugResult:
        attempts: list[DebugAttempt] = []
        current = source
        last_fix = "initial run"

        for i in range(1, self.max_attempts + 1):
            result = self.sandbox.run_source(current, filename=filename)
            attempts.append(DebugAttempt(attempt=i, fix=last_fix, result=result))
            if result.ok:
                return DebugResult(success=True, final_source=current, attempts=attempts)

            repaired, fix = self._repair(current, result)
            if repaired is None:
                break
            current = repaired
            last_fix = fix

        return DebugResult(success=False, final_source=current, attempts=attempts)

    # ------------------------------------------------------------------

    def _repair(self, source: str, result: SandboxResult) -> tuple[Optional[str], str]:
        text = (result.stderr or "") + "\n" + (result.stdout or "")

        if result.forbidden:
            # Unsafe code can't be auto-repaired; surface to caller.
            return None, "forbidden-pattern"

        m = _MISSING_MODULE_RE.search(text)
        if m:
            return self._guard_import(source, m.group("name")), f"guard-import:{m.group('name')}"

        if _EOF_RE.search(text):
            return source.rstrip() + "\npass\n", "append-pass"

        m = _NAME_ERROR_RE.search(text)
        if m:
            return self._define_default(source, m.group("name")), f"define-default:{m.group('name')}"

        m = _INDENT_RE.search(text)
        if m:
            return self._fix_indent(source, int(m.group("line"))), f"fix-indent:{m.group('line')}"

        return None, "no-repair"

    # -- repair helpers -------------------------------------------------

    @staticmethod
    def _guard_import(source: str, module: str) -> str:
        head = module.split(".")[0]
        guarded = (
            f"try:\n"
            f"    import {module}\n"
            f"except ImportError:\n"
            f"    {head} = None\n"
        )
        # Replace the first import line for this module.
        pattern = re.compile(rf"^\s*import\s+{re.escape(module)}\s*$", re.M)
        if pattern.search(source):
            return pattern.sub(guarded.rstrip(), source, count=1)
        # Also handle "from X import Y" forms by guarding the top-level package.
        from_pattern = re.compile(rf"^\s*from\s+{re.escape(module)}\b.*$", re.M)
        if from_pattern.search(source):
            return from_pattern.sub(guarded.rstrip(), source, count=1)
        return guarded + source

    @staticmethod
    def _define_default(source: str, name: str) -> str:
        if re.search(rf"^\s*{re.escape(name)}\s*=", source, re.M):
            return source  # already defined; can't help further
        prefix = f"{name} = None  # auto-defined by AutoDebugger\n"
        return prefix + source

    @staticmethod
    def _fix_indent(source: str, line_no: int) -> str:
        lines = source.splitlines()
        idx = line_no - 1
        if idx < 0 or idx >= len(lines):
            return source
        # Re-indent to match previous non-blank line's indent + 4 spaces if it ends with ':'.
        for prev in range(idx - 1, -1, -1):
            stripped = lines[prev].rstrip()
            if not stripped:
                continue
            indent = len(lines[prev]) - len(lines[prev].lstrip())
            target = " " * (indent + 4) if stripped.endswith(":") else " " * indent
            lines[idx] = target + lines[idx].lstrip()
            break
        return "\n".join(lines) + ("\n" if source.endswith("\n") else "")
