# Pipeline State Machine & Invariants

This document describes how state flows through the six links of the
`acb build` chain — **prompt → code → debug → sandbox → version →
deploy** — and the invariants every step must establish before the next
step is allowed to consume its state.

## States

```
                 ┌──────────────┐
   raw argv  ──► │  INPUT       │  (unvalidated prompt string)
                 └──────┬───────┘
                        │ validate: non-empty, length <= MAX_PROMPT_CHARS
                        ▼
                 ┌──────────────┐        reject ─────► REJECTED (exit 2,
                 │ VALIDATED    │                       no side effects)
                 └──────┬───────┘
                        │ PromptEngine.generate (rule | llm→rule fallback)
                        ▼
                 ┌──────────────┐
                 │ GENERATED    │  GeneratedProject in memory
                 └──────┬───────┘  (entrypoint exists, files compile?)
                        │ materialise into workdir (idempotent overwrite)
                        ▼
                 ┌──────────────┐
                 │ MATERIALISED │  every project file present on disk
                 └──────┬───────┘
                        │ AutoDebugger.run: sandbox exec + repair, <= N shots
                        ▼
                 ┌──────────────┐   unrepairable/timeout
                 │ DEBUGGED     │ ─────────────────────► DEBUG_FAILED
                 └──────┬───────┘                          (workspace kept;
                        │ repaired source written back     no artifact)
                        ▼
                 ┌──────────────┐
                 │ VERSIONED    │  VersionStore.commit (content-addressed)
                 └──────┬───────┘  idempotent on any existing sha
                        │ DeploymentSimulator
                        ▼
   LINTED ──► BUILT ──► TESTED ──► PACKAGED ──► DEPLOYED
      │           │         │          │            │
      └───────────┴─────────┴──────────┴────────────┴──► DEPLOY_FAILED
             (pipeline aborts; partial artifact removed;
              report.artifact == None; materialised
              workspace retained for inspection)
```

`acb deploy` enters at `VALIDATED → GENERATED → MATERIALISED` and then runs
the five deploy stages. `acb run` exercises only `SecureSandbox.run_file`.
`acb commit` is an independent entry point into `VERSIONED`.

## Step-level invariants

### 1. INPUT → VALIDATED (prompt validation)

* Pre: nothing created on disk or in the version store.
* Post:
  * prompt is a `str`, stripped, non-empty.
  * `len(prompt) <= PromptEngine.MAX_PROMPT_CHARS` (4000).
  * On rejection no state is mutated and the CLI returns exit code 2 with
    a single-line message on stderr (no traceback on stdout/stderr).

### 2. VALIDATED → GENERATED (PromptEngine)

* Pre: validated prompt.
* Post:
  * `project.entrypoint` matches exactly one `f.path` in `project.files`.
  * every `f.path` is a relative, non-empty path.
  * the summary embedded in templates is bounded (`_summarise`, <= 140).
  * llm backend: any failure transparently falls back to the rule backend,
    so the post-conditions always hold.

### 3. GENERATED → MATERIALISED (write files to workdir)

* Pre: project invariants above hold.
* Post: for every file, `(workdir / f.path).is_file()` and its bytes equal
  `f.content`; parent directories exist. The operation is repeatable
  (re-running overwrites with identical bytes).

### 4. MATERIALISED → DEBUGGED (AutoDebugger + SecureSandbox)

* Pre: entrypoint exists on disk.
* Each sandbox run:
  * pre: temp workdir created; stripped env; `RLIMIT_*` and a new session
    (`setsid`) applied in the child.
  * post: temp workdir removed; on wall-clock timeout the **entire process
    group** is reaped (`SIGKILL`), no grandchild outlives the call;
    `timed_out ⇒ returncode == 124`.
* Debug loop post (success): `final_source` executes with `returncode == 0`
  and is written back to the same entrypoint; the in-memory project is
  updated so the deploy stage cannot re-materialise the broken original.
* Debug loop post (failure): the workspace file is not rewritten, the
  number of attempts is `<= max_attempts`, and `forbidden` code is never
  "repaired" (safety is never weakened).

### 5. DEBUGGED → VERSIONED (VersionStore.commit)

* Pre: content is the exact bytes of the workspace entrypoint.
* Post:
  * `index.json` is rewritten atomically (tmp + `os.replace`), so a crash
    never corrupts it.
  * `sha256(blob) == v.sha256` and blob bytes == committed content.
  * versions are gapless: `1..len(history)`.
  * **idempotency over the whole history**: committing content whose sha
    already appears in the file's history returns that existing Version
    and appends nothing. This makes both duplicate submission and
    "roll back to v1, then commit" converge to one consistent state.
* `restore` post: the target file is replaced atomically, so a failed
  rollback (missing version) leaves the pre-existing workspace file byte
  for byte untouched.

### 6. VERSIONED → LINTED → BUILT → TESTED → PACKAGED → DEPLOYED

* Pre: materialised tree + (in `build`) a committed version.
* Stage invariants:
  * lint: every python `f.content` `compile()`s; no disk mutation.
  * build: every project file exists at `workdir / f.path`.
  * test: entrypoint executed in the sandbox; `passed ⇔ result.ok`
    (timeout/forbidden/nonzero all map to failed).
  * package: artifact is first written to a temp name and atomically
    renamed into `<name>.tar`; an exception never leaves a partial tar.
  * deploy: last stage; only a passed deploy yields a release.
* Pipeline post:
  * all stages passed ⇒ `status == "succeeded"` and
    `report.artifact` names the existing tar.
  * any stage failed ⇒ `status == "failed"`, later stages did not run,
    `report.artifact is None`, and **no artifact (fresh or stale from a
    previous run) remains in workdir**. The materialised source tree is
    kept for inspection.

## Failure-recovery matrix

| Failure point | Observable state | Recovery |
|---|---|---|
| Empty / oversized prompt | REJECTED, no side effects, exit 2 | fix prompt, re-submit |
| Forbidden pattern pre-scan | returncode 126, no subprocess spawned | edit source, re-run |
| Sandbox wall-clock timeout | rc 124, `timed_out=True`, whole pgid killed | debugger records attempt, no repair attempted, retry/abort |
| Debugger exhausted shots | DEBUG_FAILED, workspace = last source shown | user edits; nothing versioned as "fixed" |
| Duplicate / rolled-back commit | existing Version returned, history unchanged | store already consistent |
| Deploy stage failure | DEPLOY_FAILED, tar removed, report.artifact None | fix cause, re-run `deploy` into same workdir |
