# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- CLI: empty/oversized prompts and missing files now exit with code 2 and a
  single clean stderr line instead of an uncaught traceback.
- `prompt_engine`: prompts longer than `MAX_PROMPT_CHARS` (4000) are
  rejected before template rendering or any LLM call.
- `versioning`: `commit` is idempotent over the whole file history, so
  duplicate submissions and commit-after-rollback no longer append
  duplicate versions; `restore` swaps the target file atomically so a
  failed rollback never corrupts the workspace.
- `sandbox`: sandboxed children run in their own session and a wall-clock
  timeout now kills the entire process group, so forked grandchildren can
  no longer outlive a timed-out run.
- `deployment`: a failed pipeline removes both stale artifacts from
  previous runs and half-baked artifacts from the current run, and
  `DeploymentReport.artifact` is only set when the pipeline succeeded;
  packaging writes via a temp file with atomic rename.

## [0.1.0] - 2026-04-30

### Added
- Initial release of AI-Powered Code Builder.
- `prompt_engine`: rule-based template matcher (CLI / web / math / data /
  hello) with optional OpenAI-compatible LLM backend that falls back to rules.
- `debugger`: 3-shot auto-repair loop covering missing modules, indentation
  errors, unexpected EOF, and undefined names.
- `sandbox`: subprocess sandbox with `RLIMIT_AS` / `RLIMIT_CPU` /
  `RLIMIT_NOFILE`, wall-clock timeout, stripped environment, static denylist,
  and 64 KiB output budget.
- `versioning`: content-addressed JSON-backed version store with `commit`,
  `history`, `restore`, and unified `diff`.
- `deployment`: pipeline simulator running lint → build → test → package →
  deploy, integrating the secure sandbox for the `test` stage.
- `acb` CLI with subcommands: `plan`, `generate`, `run`, `debug`, `commit`,
  `history`, `diff`, `deploy`, `build`. All commands accept `--json`.
- 34 pytest tests covering all five modules plus the CLI.

[0.1.0]: https://github.com/nubenersu-ux/ai-code-builder/releases/tag/v0.1.0
