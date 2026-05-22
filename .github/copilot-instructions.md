# Copilot Instructions

Use these project-specific rules when proposing or editing code in this repo.

## Project Shape

- Keep root scripts (`insv_accelerometer.py`, `insv_maneuver_analysis.py`, `insv_repair.py`) as thin compatibility launchers.
- Put implementation code in `src/insv_tools/`.
- Put user guides and references in `docs/`; put investigation reports in `docs/research/`.
- Put generated summaries, CSVs, plots, and temporary artifacts under `analysis_outputs/` or `.cache/`.
- Do not modify `pg-reference-docs/` unless explicitly asked.

## Coding Standards

- Target Python 3.9+ and use only the standard library in core implementation paths.
- Keep optional dependencies optional. Import Matplotlib/Tk only inside GUI or plotting paths.
- Prefer small, typed functions with clear names over broad procedural blocks.
- Use `from __future__ import annotations` in implementation modules.
- Preserve backwards-compatible CLI behavior unless the user explicitly asks for a breaking change.
- Avoid hard-coded absolute paths; use relative defaults from the repository root.

## Domain Rules

- Treat INSV as MP4/QuickTime plus an Insta360 proprietary trailer.
- Preserve trailer bytes when repairing files unless there is a clear reason not to.
- Keep accelerometer and gyro values in the camera/helmet frame unless an explicit orientation calibration is implemented.
- Do not call IMU-derived acceleration a true ground speed. Speed requires GPS, optical flow, video timing analysis, or another velocity source.
- Candidate maneuver events are review aids, not final labels.

## Maneuver Analysis

- `g_force` is acceleration magnitude and includes gravity.
- `gyro_norm` is total angular velocity magnitude.
- `jerk_g_s` is finite-difference acceleration change.
- Detection should remain conservative and explainable: threshold segments, merges, enrichment, and feature windows.
- If adding a metric, document its units, meaning, and whether it is raw sensor-frame, derived magnitude, or inferred.

## Documentation

- Update `docs/MANEUVER_ANALYSIS_USAGE.md` for commands and workflows.
- Update `docs/MANEUVER_ANALYSIS.md` for functionality, variables, algorithms, constants, and internals.
- Update `README.md` when project layout or top-level usage changes.
- Include limitations and assumptions, especially for sensor-frame data and unavailable values.

## Verification

- Run `python -m py_compile` for changed Python files.
- Run each touched CLI with `--help` when argument parsing changes.
- For analyzer changes, run a non-interactive smoke test such as:

```bash
python3 insv_maneuver_analysis.py --no-plot --plot-output analysis_outputs/infinite_and_helis_timeline.png
```

- Do not commit sample `.insv` files or large generated artifacts unless explicitly requested.
