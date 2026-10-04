# Pipeline State Machine & Invariants

The six links of `ai-code-builder` pass state to each other. This document
is the contract: every transition lists the invariants that must hold
before (pre) and after (post) it runs, plus the failure-recovery path.

## State machine

```
                        ┌──────────────────────────────────────────────┐
                        │                  CLI (acb)                    │
                        └──────────────────────────────────────────────┘
                                          │ raw prompt (str)
                                          ▼
   ┌─────────────┐  validate   ┌──────────────────┐  generate()   ┌─────────────┐
   │ RAW_PROMPT  │────────────▶│ VALIDATED_PROMPT │──────────────▶│  PROJECT    │
   └─────────────┘             └──────────────────┘               │ (in-memory) │
        │  invalid: ValueError ──▶ CLI: stderr + exit 2, no fs state touched
        │                                                       ▼
        │                                              ┌──────────────────┐
        │                                              │ WORKDIR_FILES    │ materialise
        │                                              └──────────────────┘
        │                                                       │ entrypoint source
        │                                                       ▼
        │                                  run→repair (≤ max_attempts)
        │                              ┌──────────────────────────────┐
        │                              │ AutoDebugger                 │
        │                              │  DEBUGGED_SOURCE │ UNREPAIRABLE│
        │                              └──────────────────────────────┘
        │                                 │ each attempt runs in ──────┐
        │                                 ▼                            ▼
        │                        ┌─────────────────┐        ┌─────────────────┐
        │                        │ (synced back to │        │ SecureSandbox   │
        │                        │  project+workdir│        │  CODE → RESULT  │
        │                        │  before deploy) │        │  ok|fail|timeout│
        │                        └─────────────────┘        │  |forbidden     │
        │                                 │                 └─────────────────┘
        │                                 ▼ (file, content)
        │                        ┌─────────────────┐
        │                        │ VersionStore    │  commit / restore
        │                        │ VERSION_LOG     │◀───────┐
        │                        └─────────────────┘        │ rollback = new
        │                                 │                 │ version, keeps
        │                                 ▼                 │ store==workspace
        │                        ┌─────────────────┐─────────┘
        │                        │ DeploymentSim.  │
        │                        │ lint→build→test │──▶ abort on first failure
        │                        │ →package→deploy │
        │                        └─────────────────┘
        │                                 │
        ▼                                 ▼
   REPORT (stdout/JSON, exit code)  ARTIFACT (only when succeeded)
```

## Per-link invariants

### 1. Prompt validation (CLI → PromptEngine)
- **Pre:** `prompt` is a `str` (may be empty/oversized).
- **Post:** `prompt` is non-empty after stripping and `len(prompt) <= MAX_PROMPT_CHARS`;
  `GeneratedProject.files` is non-empty and `entrypoint` names one of `files`.
- **Failure:** `ValueError` → CLI prints one line to **stderr**, exit code `2`,
  and creates **no** workdir, store, or file state.

### 2. Auto-debug (AutoDebugger)
- **Pre:** `source` is the entrypoint content; `1 <= max_attempts`.
- **Post:** `len(attempts) <= max_attempts`; `final_source` is exactly the last
  source that was executed; `success ⟺ last attempt result.ok`.
- **Failure:** unrepairable errors surface verbatim; caller decides whether the
  original (un-debugged) source proceeds.

### 3. Sandbox (SecureSandbox)
- **Pre:** code is scanned before execution; `timeout > 0`.
- **Post:** `forbidden ⇒ returncode 126 and nothing executed`;
  `timed_out ⇒ returncode 124 and the whole process tree is dead`;
  `ok ⟺ returncode == 0 ∧ ¬timed_out ∧ ¬forbidden`;
  `|stdout|, |stderr| ≤ output_budget (+ truncation marker)`.
- **Failure:** timeout kills the entire process group, temp dir is removed,
  partial output is still returned.

### 4. Versioning (VersionStore)
- **Pre:** `file` non-empty; it is normalised (`os.path.normpath`) so different
  spellings of one path share a single log.
- **Post:** version numbers per file are strictly increasing from 1; two
  consecutive entries never share a sha256 (duplicate submissions are
  idempotent); the latest entry's content equals the last committed content.
- **Rollback:** `restore(file, v, target)` writes the blob **and** appends a
  new version recording the rollback, so `history(file)[-1]` always describes
  the workspace content.

### 5. Deployment simulation (DeploymentSimulator)
- **Pre:** `project.files` materialised into `workdir`; any stale
  `<name>.tar` from a previous run is removed first.
- **Post:** stages run in declared order and abort on the first failure;
  `status == "succeeded" ⟺ all stages passed ⟺ artifact file exists ∧
  report.artifact` points at it; `status == "failed" ⇒ report.artifact is
  None ∧ no artifact file remains in workdir` (no half-baked state).
- **Failure:** a failed `package` stage deletes its partial `.tar`; a failed
  pipeline deletes any stale artifact.

### 6. CLI orchestration (`acb build`)
- **Order:** validate → generate → materialise → debug → sync repaired source
  back into `project.files` → commit → deploy → summarise.
- **Post:** the committed content equals the workdir entrypoint content; the
  deploy consumes the debugged source, never the original broken one.
- **Failure:** validation errors leave zero filesystem state; a failed deploy
  still leaves a consistent version log (the commit reflects the workspace).
