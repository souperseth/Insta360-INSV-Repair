# Documentation Index

This folder contains the project documentation and research notes.

## User Guides

- [Maneuver Analysis Usage](MANEUVER_ANALYSIS_USAGE.md): commands, plotting, labeling workflow, and output examples.
- [Maneuver Analysis Reference](MANEUVER_ANALYSIS.md): functionality, variables, algorithms, plotting internals, CLI flags, and performance notes.
- [INSV File Format](INSV_FORMAT.md): observed MP4/QuickTime structure, trailer layout, and sensor records.
- [INSV Repair Notes](INSV_REPAIR_NOTES.md): repair strategy, corruption patterns, and validation details.

## Research Notes

- [X3/X4 Format Comparison](research/X3_X4_FORMAT_COMPARISON.md): observed structural differences between sample X3 and X4 files.
- [X4 Accelerometer Investigation](research/X4_ACCELEROMETER_INVESTIGATION.md): IMU block analysis for X4 sample files.

## Root Launchers

The root scripts are compatibility launchers:

- `insv_accelerometer.py`
- `insv_maneuver_analysis.py`
- `insv_repair.py`

The implementation lives under `src/insv_tools/`.

Hex/editor inspection patterns live under `patterns/`.

External paragliding and aero reference documents live under `pg-reference-docs/`.

AI assistant guidance lives in [../.github/copilot-instructions.md](../.github/copilot-instructions.md).
