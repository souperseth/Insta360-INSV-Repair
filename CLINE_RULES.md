# Cline Rules — Insta360-INSV-Repair

These rules apply to all Cline-assisted work in this repository.

## Language & Compatibility

- All Python code must target **Python 3.9+**.
- Use `from __future__ import annotations` at the top of every module.
- Use type annotations on all function signatures and return types.

## Style & Naming

- **snake_case** for variables, functions, and module names.
- **PascalCase** for classes and dataclasses.
- **UPPER_SNAKE_CASE** for module-level constants.
- Private/internal helpers should be prefixed with a single underscore (`_helper`).
- Maximum line length: **100 characters** (soft limit; prefer readability).

## Documentation

- All public functions, classes, and modules must have docstrings.
- Use triple-double-quote (`"""`) docstrings.
- Update `docs/MANEUVER_ANALYSIS_USAGE.md`, `docs/MANEUVER_ANALYSIS.md`, or
  `README.md` when adding user-facing features or CLI flags.
- Keep inline comments concise; prefer self-documenting names over comments.

## Dependencies

- The core implementation modules in `src/insv_tools/` must remain
  **dependency-free** (stdlib only + local project imports).
- `matplotlib` and `numpy` are **optional** — only imported inside plotting/visualization code paths.
- Do **not** introduce new third-party dependencies without explicit user approval.

## Architecture

- Keep the analysis pipeline modular: raw data → derived samples → detection → enrichment → output.
- Prefer pure functions; use module-level caches only where clearly justified for performance.
- Do not use global mutable state beyond the existing `_motion_samples_cache` and `_candidate_segments_cache`.

## File & Directory Policy

- Do **not** modify files in `pg-reference-docs/`.
- Do **not** commit sample `.insv` files or large binary outputs to the repo.
- Keep `.gitignore` up to date when adding new generated output directories.

## CLI

- All CLI tools must support `--help` with descriptive argument help text.
- New CLI flags must have sensible defaults and be documented in
  `docs/MANEUVER_ANALYSIS_USAGE.md` or `docs/MANEUVER_ANALYSIS.md`.
- Prefer non-interactive CLI behavior (flags over prompts).

## Code Changes

- Prefer targeted, minimal diffs — avoid reformatting unrelated code.
- When refactoring, verify that all callers and tests still work.
- Use `replace_in_file` for surgical edits; use `write_to_file` only for new files or full rewrites.

## Git

- Write clear, concise commit messages describing *what* changed and *why*.
- Do not force-push to `main` or `upstream`.
