# Project Instructions For AI Coding Agents

Follow [.github/copilot-instructions.md](.github/copilot-instructions.md) for
project structure, coding standards, domain assumptions, documentation rules,
and verification commands.

Important defaults:

- Keep implementation code in `src/insv_tools/`.
- Keep root scripts as compatibility launchers.
- Keep user workflows in `docs/MANEUVER_ANALYSIS_USAGE.md`.
- Keep functionality, variables, and algorithms in `docs/MANEUVER_ANALYSIS.md`.
- Do not modify `pg-reference-docs/` unless explicitly requested.
- Do not describe IMU-derived acceleration as true speed; true speed needs a
  parsed velocity source.
