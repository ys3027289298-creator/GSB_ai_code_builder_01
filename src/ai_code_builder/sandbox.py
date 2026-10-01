"""Secure execution sandbox.

The sandbox runs generated code in a fresh subprocess with several layers of
defence so that untrusted, freshly-generated code cannot harm the host:

* Code is written into a fresh temporary directory and executed there as the
  working directory.
* The subprocess receives a stripped-down environment (no inherited secrets).
* CPU time and memory limits are applied via ``resource`` on POSIX systems.
* A wall-clock timeout aborts runaway programs.
* Static checks reject obviously dangerous calls before execution.
* stdout/stderr are captured and truncated to a fixed budget.

The sandbox is intentionally implemented with the standard library only so it
runs anywhere Python runs, including read-only or air-gapped environments.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Patterns we refuse to execute. These are deliberately conservative; the
# debugger uses the same list to flag generated code.
_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("subprocess", re.compile(r"\b(subprocess|os\.system|os\.popen|pty\.spawn)\b")),
    ("network", re.compile(r"\bsocket\.(socket|create_connection)\b")),
    ("delete-tree", re.compile(r"\bshutil\.rmtree\b")),
    ("eval-exec", re.compile(r"\b(eval|exec)\s*\(")),
    ("dynamic-import", re.compile(r"\b__import__\s*\(")),
)

DEFAULT_OUTPUT_BUDGET = 64 * 1024  # 64 KiB
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MEMORY_LIMIT_MB = 256
DEFAULT_CPU_SECONDS = 4


@dataclass
class SandboxResult:
    """Outcome of running a single command inside the sandbox."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    forbidden: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.forbidden


def scan_for_forbidden(source: str) -> list[str]:
    """Return the list of forbidden-pattern labels found in ``source``."""
    return sorted({label for label, pat in _FORBIDDEN_PATTERNS if pat.search(source)})


def _build_preexec_fn(memory_mb: int, cpu_seconds: int):
    if os.name != "posix":
        return None

    def _limit() -> None:  # pragma: no cover - exercised in subprocess
        import resource

        bytes_limit = memory_mb * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        except (ValueError, OSError):
            pass

    return _limit


class SecureSandbox:
    """Runs short-lived code snippets with resource limits."""

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
        cpu_seconds: int = DEFAULT_CPU_SECONDS,
        output_budget: int = DEFAULT_OUTPUT_BUDGET,
        allow_forbidden: bool = False,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.timeout = timeout
        self.memory_limit_mb = memory_limit_mb
        self.cpu_seconds = cpu_seconds
        self.output_budget = output_budget
        self.allow_forbidden = allow_forbidden

    # ------------------------------------------------------------------

    def run_source(
        self,
        source: str,
        *,
        filename: str = "snippet.py",
        argv: Optional[list[str]] = None,
        stdin: Optional[str] = None,
    ) -> SandboxResult:
        """Write ``source`` into a temp dir and execute it."""
        forbidden = scan_for_forbidden(source)
        if forbidden and not self.allow_forbidden:
            return SandboxResult(returncode=126, stdout="", stderr="", forbidden=forbidden)

        workdir = Path(tempfile.mkdtemp(prefix="acb-sandbox-"))
        try:
            target = workdir / filename
            target.write_text(source, encoding="utf-8")
            return self.run_file(target, argv=argv, stdin=stdin)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def run_file(
        self,
        path: Path | str,
        *,
        argv: Optional[list[str]] = None,
        stdin: Optional[str] = None,
        cwd: Optional[Path | str] = None,
    ) -> SandboxResult:
        """Execute an existing python file inside the sandbox."""
        path = Path(path).resolve()
        if not path.is_file():
            return SandboxResult(returncode=127, stdout="", stderr=f"not a file: {path}")
        source = path.read_text(encoding="utf-8")
        forbidden = scan_for_forbidden(source)
        if forbidden and not self.allow_forbidden:
            return SandboxResult(returncode=126, stdout="", stderr="", forbidden=forbidden)

        cmd = [sys.executable, "-I", "-S", str(path)]
        if argv:
            cmd.extend(argv)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
            "ACB_SANDBOX": "1",
        }
        preexec = _build_preexec_fn(self.memory_limit_mb, self.cpu_seconds)
        resolved_cwd = Path(cwd).resolve() if cwd is not None else path.parent
        return self._spawn(cmd, env=env, cwd=resolved_cwd, stdin=stdin, preexec=preexec)

    # ------------------------------------------------------------------

    def _spawn(
        self,
        cmd: list[str],
        *,
        env: dict[str, str],
        cwd: Path | str,
        stdin: Optional[str],
        preexec,
    ) -> SandboxResult:
        import time

        start = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(  # noqa: S603 - controlled command
                cmd,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(cwd),
                env=env,
                check=False,
                preexec_fn=preexec,  # type: ignore[arg-type]
            )
            stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            stdout = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            returncode = 124
        duration = time.monotonic() - start

        return SandboxResult(
            returncode=returncode,
            stdout=self._truncate(stdout),
            stderr=self._truncate(stderr),
            timed_out=timed_out,
            duration_seconds=round(duration, 4),
        )

    def _truncate(self, text: str) -> str:
        if len(text) <= self.output_budget:
            return text
        head = text[: self.output_budget]
        return head + f"\n...[truncated {len(text) - self.output_budget} bytes]"
