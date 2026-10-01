"""Prompt-to-Code engine.

Converts a plain-language prompt into a structured plan and a set of
ready-to-run source files. Two backends are supported:

* ``rule`` (default, no external deps): a deterministic pattern matcher that
  recognises common programming intents (CLI tools, web servers, math
  utilities, data scripts, etc.) and emits a complete project skeleton.
* ``llm``: a thin adapter that calls an OpenAI-compatible chat completion API
  *if* ``OPENAI_API_KEY`` is set in the environment. The adapter is optional
  so the rest of the system stays runnable offline.

The engine always returns a :class:`GeneratedProject` that downstream
components (debugger, sandbox, versioning, deployment) can consume.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class GeneratedFile:
    """A single source file produced by the engine."""

    path: str
    content: str
    language: str = "python"


@dataclass
class GeneratedProject:
    """A complete project produced from a prompt."""

    name: str
    summary: str
    entrypoint: str
    files: list[GeneratedFile] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "summary": self.summary,
            "entrypoint": self.entrypoint,
            "files": [asdict(f) for f in self.files],
            "tasks": list(self.tasks),
        }


# ---------------------------------------------------------------------------
# Rule-based templates
# ---------------------------------------------------------------------------

_HELLO_TEMPLATE = '''\
"""Auto-generated entrypoint for: {summary}"""


def main() -> int:
    print("Hello from {name}!")
    print("Prompt was: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

_CLI_TEMPLATE = '''\
"""Auto-generated CLI for: {summary}"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description={summary!r})
    parser.add_argument("--name", default="world", help="Who to greet")
    parser.add_argument("--shout", action="store_true", help="Uppercase output")
    return parser


def run(name: str, shout: bool) -> str:
    msg = f"Hello, {{name}}!"
    return msg.upper() if shout else msg


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(run(args.name, args.shout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

_WEB_TEMPLATE = '''\
"""Auto-generated minimal HTTP server for: {summary}

Uses only the Python standard library so it runs in any sandbox.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        body = json.dumps({{"ok": True, "path": self.path, "project": {name!r}}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # silence default logging
        return


def serve(host: str = "127.0.0.1", port: int = 0) -> HTTPServer:
    return HTTPServer((host, port), Handler)


def main() -> int:
    server = serve()
    host, port = server.server_address
    print(f"Serving {name!r} on http://{{host}}:{{port}}")
    print("Smoke check: server constructed successfully.")
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

_MATH_TEMPLATE = '''\
"""Auto-generated math utility for: {summary}"""

from __future__ import annotations

from typing import Iterable


def add(a: float, b: float) -> float:
    return a + b


def mean(values: Iterable[float]) -> float:
    values = list(values)
    if not values:
        raise ValueError("mean() requires at least one value")
    return sum(values) / len(values)


def factorial(n: int) -> int:
    if n < 0:
        raise ValueError("factorial is undefined for negatives")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result


def main() -> int:
    print("add(2, 3) =", add(2, 3))
    print("mean([1, 2, 3, 4]) =", mean([1, 2, 3, 4]))
    print("factorial(6) =", factorial(6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

_DATA_TEMPLATE = '''\
"""Auto-generated data-processing script for: {summary}"""

from __future__ import annotations

import csv
import io
from typing import Iterable


SAMPLE_CSV = """name,score
alice,90
bob,75
carol,82
"""


def parse(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def average_score(rows: Iterable[dict[str, str]]) -> float:
    rows = list(rows)
    if not rows:
        return 0.0
    return sum(float(r["score"]) for r in rows) / len(rows)


def main() -> int:
    rows = parse(SAMPLE_CSV)
    print(f"Parsed {{len(rows)}} rows")
    print(f"Average score: {{average_score(rows):.2f}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


_TEMPLATE_REGISTRY: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b(cli|command[- ]?line|argparse)\b", re.I), _CLI_TEMPLATE, "cli"),
    (re.compile(r"\b(web|http|server|api|rest|endpoint)\b", re.I), _WEB_TEMPLATE, "web"),
    (re.compile(r"\b(math|calc|calculator|factorial|mean|average|sum)\b", re.I), _MATH_TEMPLATE, "math"),
    (re.compile(r"\b(csv|data|parse|process|rows?|table)\b", re.I), _DATA_TEMPLATE, "data"),
]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def _slugify(text: str, fallback: str = "project") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return (slug[:40] or fallback).strip("-") or fallback


def _summarise(prompt: str) -> str:
    cleaned = " ".join(prompt.split())
    return cleaned if len(cleaned) <= 140 else cleaned[:137] + "..."


def _select_template(prompt: str) -> tuple[str, str]:
    for pattern, template, kind in _TEMPLATE_REGISTRY:
        if pattern.search(prompt):
            return template, kind
    return _HELLO_TEMPLATE, "hello"


class PromptEngine:
    """Convert prompts into :class:`GeneratedProject` objects.

    Parameters
    ----------
    backend:
        ``"rule"`` (default) uses local templates. ``"llm"`` calls an
        OpenAI-compatible API via :func:`urllib.request` if
        ``OPENAI_API_KEY`` is configured; if not configured it transparently
        falls back to the rule-based backend.
    model:
        Model name passed to the LLM backend. Ignored for the rule backend.
    """

    def __init__(self, backend: str = "rule", model: str = "gpt-4o-mini") -> None:
        if backend not in {"rule", "llm"}:
            raise ValueError(f"unknown backend: {backend!r}")
        self.backend = backend
        self.model = model

    # -- public API ---------------------------------------------------------

    def generate(self, prompt: str, project_name: Optional[str] = None) -> GeneratedProject:
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("prompt must be a non-empty string")

        if self.backend == "llm" and os.environ.get("OPENAI_API_KEY"):
            try:
                return self._generate_via_llm(prompt, project_name)
            except Exception:  # noqa: BLE001 - fallback path is intentional
                pass

        return self._generate_via_rules(prompt, project_name)

    def plan(self, prompt: str) -> list[str]:
        """Return the structured task list a project would expand into."""
        return self.generate(prompt).tasks

    # -- backends -----------------------------------------------------------

    def _generate_via_rules(self, prompt: str, project_name: Optional[str]) -> GeneratedProject:
        template, kind = _select_template(prompt)
        name = project_name or _slugify(prompt)
        summary = _summarise(prompt)
        rendered = template.format(name=name, summary=summary)
        files = [GeneratedFile(path="main.py", content=rendered, language="python")]
        files.append(
            GeneratedFile(
                path="README.md",
                content=textwrap.dedent(
                    f"""\
                    # {name}

                    Generated by AI-Powered Code Builder (rule backend, kind=`{kind}`).

                    ## Prompt

                    > {summary}

                    ## Run

                    ```
                    python main.py
                    ```
                    """
                ),
                language="markdown",
            )
        )
        tasks = [
            f"Plan: classify prompt as `{kind}` task",
            "Design: scaffold a single-file project with stdlib only",
            f"Build: emit `{files[0].path}` and README",
            "Debug: run the auto-debug loop",
            "Test: execute via the secure sandbox",
            "Deploy: simulate the deployment pipeline",
        ]
        return GeneratedProject(
            name=name,
            summary=summary,
            entrypoint=files[0].path,
            files=files,
            tasks=tasks,
        )

    def _generate_via_llm(self, prompt: str, project_name: Optional[str]) -> GeneratedProject:
        # Imported lazily so the rule backend has no network deps.
        import urllib.request

        api_key = os.environ["OPENAI_API_KEY"]
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        sys_prompt = (
            "You convert short user prompts into runnable Python projects. "
            "Reply with strict JSON: {\"name\": str, \"entrypoint\": str, "
            "\"files\": [{\"path\": str, \"content\": str}], \"tasks\": [str]}. "
            "Each file must be standalone. Prefer stdlib only."
        )
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
            }
        ).encode()
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
        content = payload["choices"][0]["message"]["content"]
        data = json.loads(content)
        files = [
            GeneratedFile(path=f["path"], content=f["content"], language="python")
            for f in data["files"]
        ]
        return GeneratedProject(
            name=project_name or data.get("name") or _slugify(prompt),
            summary=_summarise(prompt),
            entrypoint=data.get("entrypoint", files[0].path if files else "main.py"),
            files=files,
            tasks=list(data.get("tasks", [])),
        )
