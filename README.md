# AI-Powered Code Builder

A self-contained, modular system that turns natural-language prompts into
runnable code, auto-debugs it, executes it inside a constrained sandbox,
tracks file versions, and simulates a full build/test/deploy pipeline.

The whole system is pure-Python, standard-library only at runtime, and
ships with a single CLI binary `acb`.

## Why

Most "AI codegen" tools stop at *generation*. This project demonstrates the
five components you actually need to ship generated code safely:

| Capability | Module |
|---|---|
| Prompt → code | `ai_code_builder.prompt_engine` |
| Auto-debug loop | `ai_code_builder.debugger` |
| Secure execution sandbox | `ai_code_builder.sandbox` |
| File versioning + diffs | `ai_code_builder.versioning` |
| Deployment simulator | `ai_code_builder.deployment` |

Each module is independently usable; the CLI just composes them.

## Project structure

```
ai-code-builder/
├── pyproject.toml
├── README.md
├── LICENSE
├── .github/workflows/ci.yml
├── src/ai_code_builder/
│   ├── __init__.py
│   ├── cli.py              # argparse-based CLI (`acb`)
│   ├── prompt_engine.py    # rule-based + optional LLM generator
│   ├── debugger.py         # 3-shot auto-repair loop
│   ├── sandbox.py          # subprocess sandbox w/ rlimits + denylist
│   ├── versioning.py       # JSON-backed content-addressed version store
│   └── deployment.py       # lint → build → test → package → deploy
├── tests/
│   ├── test_prompt_engine.py
│   ├── test_sandbox.py
│   ├── test_debugger.py
│   ├── test_versioning.py
│   ├── test_deployment.py
│   └── test_cli.py
└── examples/
    └── hello.py
```

## Install

Requires Python 3.9+.

```bash
pip install -e ".[dev]"
```

After install, the `acb` command is on your `$PATH`.

## CLI usage

All commands accept `--json` for machine-readable output.

```bash
# 1. Plan only
acb plan "build a CLI that greets a user"

# 2. Generate the project on disk
acb generate "build a CLI that greets a user" -o ./greeter

# 3. Run any python file inside the sandbox
acb run ./greeter/main.py

# 4. Auto-debug a (possibly broken) file, up to 3 attempts
acb debug ./greeter/main.py --write

# 5. Track versions
acb commit ./greeter/main.py -m "v1"
acb history ./greeter/main.py
acb diff ./greeter/main.py 1 2

# 6. Simulate the full deployment pipeline
acb deploy "tiny http server returning JSON"

# 7. End-to-end demo: generate + debug + version + deploy
acb build "build a CLI that greets a user"
```

## Architecture

```
                ┌────────────────┐
   prompt ───►  │  PromptEngine  │ ──► GeneratedProject
                └────────────────┘            │
                                              ▼
                                  ┌──────────────────────┐
                                  │   AutoDebugger (3x)  │
                                  └──────────────────────┘
                                              │
                                              ▼
                                  ┌──────────────────────┐
                                  │   SecureSandbox      │
                                  │  (rlimits + denylist)│
                                  └──────────────────────┘
                                              │
                                              ▼
                                  ┌──────────────────────┐
                                  │   VersionStore       │
                                  │  (sha256 content    │
                                  │   addressed blobs)   │
                                  └──────────────────────┘
                                              │
                                              ▼
                                  ┌──────────────────────┐
                                  │ DeploymentSimulator  │
                                  │ lint→build→test→     │
                                  │ package→deploy       │
                                  └──────────────────────┘
```

### Prompt-to-code engine

Two backends:

* **`rule` (default)** — pattern-matches the prompt against a small set of
  templates (CLI, web, math, data, hello). Deterministic, offline, and
  testable.
* **`llm`** — adapter for any OpenAI-compatible chat completions endpoint
  (set `OPENAI_API_KEY` and optionally `OPENAI_BASE_URL`). Returns strict
  JSON via `response_format=json_object`. Falls back to the rule backend if
  the API call fails.

### Auto-debugging loop

`AutoDebugger.run(source)`:

1. Execute the source in the sandbox.
2. If it failed, classify the error (missing module, `NameError`,
   `IndentationError`, unexpected EOF) and apply a deterministic fix.
3. Retry up to `max_attempts` (default 3).

Repairs always make the program *narrower* (e.g. `try/except ImportError` to
guard a missing module) — they never widen access or weaken safety.

### Secure sandbox

`SecureSandbox.run_source(text)` runs Python in a fresh subprocess with:

* A clean environment (no inherited secrets) and `ACB_SANDBOX=1`.
* `python -I -S` to skip user `site-packages` and isolation flags.
* On POSIX, `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_NOFILE` via `preexec_fn`.
* A wall-clock timeout (default 5 s).
* A static denylist that blocks `subprocess`, `os.system`, raw sockets,
  `eval`/`exec`, dynamic imports, and `shutil.rmtree`.
* Output truncation to a 64 KiB budget.

The sandbox is **not** a substitute for OS-level isolation (containers,
seccomp, gVisor) for hostile workloads — it's a defence-in-depth layer for
freshly generated code that the user has not yet reviewed.

### Versioning

`VersionStore` is a tiny content-addressed log:

* `index.json` — `{file: [Version, ...]}`.
* `blobs/<sha256>` — raw file contents, deduplicated.
* `commit`, `history`, `read`, `restore`, `diff` operations.
* Idempotent: re-committing identical content returns the existing version.

### Deployment simulator

Five stages run in order; any failure aborts the pipeline:

1. **lint** — `compile()` every Python file.
2. **build** — verify the materialised tree.
3. **test** — run the entrypoint inside the sandbox.
4. **package** — produce a `tar` artifact.
5. **deploy** — emit a structured deployment log.

The simulator returns a `DeploymentReport` with per-stage timing and logs.

## Example I/O

Input:

```
$ acb build "build a CLI that greets a user"
```

Output:

```
== Project: build-a-cli-that-greets-a-user ==
Summary: build a CLI that greets a user
Files written to ./.acb_workspace/build-a-cli-that-greets-a-user:
  - main.py
  - README.md
Auto-debug: success=True attempts=1
Versioned: main.py@v1 sha=8f3a...
Deployment: succeeded (0.18s)
  - lint: passed
  - build: passed
  - test: passed
  - package: passed
  - deploy: passed
```

JSON variant (`acb --json build ...`) returns the structured report
suitable for piping into another tool.

## Programmatic API

```python
from ai_code_builder import (
    PromptEngine, AutoDebugger, SecureSandbox, VersionStore, DeploymentSimulator,
)

project = PromptEngine().generate("tiny http server returning JSON")
debug = AutoDebugger().run(project.files[0].content)
assert debug.success

store = VersionStore("./.acb_versions")
store.commit(project.entrypoint, debug.final_source, message="auto")

report = DeploymentSimulator().deploy(project, "./.acb_workspace/demo")
print(report.status, [s.name for s in report.stages])
```

## Tests

```bash
pytest -q
ruff check src tests
```

CI runs both on every push/PR across Python 3.10/3.11/3.12.

## License

MIT — see [LICENSE](./LICENSE).
