# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
