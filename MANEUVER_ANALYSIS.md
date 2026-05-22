# Acro Maneuver Analysis

This project now includes a first-pass analysis layer for using Insta360 INSV
IMU data from paragliding acro flights.  The analyzer is intentionally
dependency-free and builds on `insv_accelerometer.py`.

The current goal is not final maneuver recognition.  It is to create useful
derived signals, candidate maneuver intervals, and machine-learning-ready
feature windows that can be checked and labeled against video or pilot memory.

## Sensor Frame Caveat

The camera is mounted on the pilot's helmet, not on the paraglider wing.  The
IMU therefore measures the motion and load at the suspended pilot/camera body.
For acro, that body behaves like a pendulum under the wing through the risers
and line cascade.  Treat acceleration magnitude as a useful load proxy, but do
not treat camera-axis acceleration as wing-axis, pilot-axis, or Earth-axis
motion until the camera orientation has been calibrated or estimated.

This especially matters for 3D path reconstruction.  Without reliable
orientation, gravity removal, and drift correction, double-integrating
accelerometer data will produce quickly drifting position estimates.

## Derived Signals

For each IMU sample, `insv_maneuver_analysis.py` computes:

- `g_force`: vector magnitude of accelerometer channels.
- `gyro_norm`: vector magnitude of angular-velocity channels.
- `jerk_g_s`: rate of change of acceleration magnitude.

For sliding windows, it exports summary features for each acceleration and gyro
axis, plus force, rotation, jerk, dominant axes, and gyro sign changes.

## Candidate Event Types

Detected events are conservative candidates:

- `high_g_load_pulse_candidate`: sustained load above the high-G threshold.
  This is a bottom/load phase candidate, not a maneuver label by itself.
- `fast_rotation_candidate`: sustained angular velocity above the rotation
  threshold.
- `loaded_rotation_phase_candidate`: high load and fast rotation at the same
  time.
- `infinite_tumble_candidate`: sustained rotation with repeated high-G load
  pulses, matching the expected infinite tumbling pattern where the strongest
  load is felt as the pilot passes under the glider at the bottom of rotation.
- `single_axis_spin_candidate`: fast rotation dominated by one gyro axis.

These event names are deliberately not final labels.  High-G load pulses can
occur inside infinite tumbling, spirals, exits, or other maneuvers.  For
example, a heli may appear as a `single_axis_spin_candidate`, but the actual
label should be confirmed by video, pilot notes, or a training label file.

## Run The Analyzer

```bash
python3 insv_maneuver_analysis.py \
  sample_insvs_x4/infinite_and_helis.insv \
  --summary \
  --summary-output analysis_outputs/infinite_and_helis_summary.txt \
  --events analysis_outputs/infinite_and_helis_events.json \
  --features analysis_outputs/infinite_and_helis_features.csv
```

The JSON contains recording-level statistics and candidate intervals.  The CSV
contains one row per sliding window and is intended as input to future
supervised learning.

## Plot The Timeline

The analyzer can open an interactive matplotlib GUI plotting selected metrics
against time on one shared axis, with candidate intervals shaded and labeled.
Metric lines are robust-normalized by default so G-force and rotation can be
compared on the same graph.  Jerk is still available, but it is not plotted by
default because differentiating acceleration amplifies sensor noise and can
visually dominate the other traces.

One-time setup:

```bash
python3 -m pip install -r requirements.txt
```

Open the GUI:

```bash
python3 insv_maneuver_analysis.py \
  sample_insvs_x4/infinite_and_helis.insv \
  --summary-output analysis_outputs/infinite_and_helis_summary.txt \
  --plot
```

Save the same plot to a PNG instead of opening the GUI:

```bash
python3 insv_maneuver_analysis.py \
  sample_insvs_x4/infinite_and_helis.insv \
  --summary-output analysis_outputs/infinite_and_helis_summary.txt \
  --plot-output analysis_outputs/infinite_and_helis_timeline.png
```

Useful plot options:

```bash
python3 insv_maneuver_analysis.py video.insv \
  --plot \
  --plot-metrics g_force gyro_norm accel_x accel_y accel_z \
  --plot-smooth-seconds 0.2
```

Use `--plot-raw` if you want the original metric scales instead of normalized
values.

Useful threshold options:

```bash
python3 insv_maneuver_analysis.py video.insv \
  --high-g-threshold 2.0 \
  --rotation-threshold 2.0 \
  --infinite-tumble-min-load-pulses 2 \
  --smooth-seconds 0.5 \
  --min-duration-seconds 0.75
```

## First Sample Result

On `sample_insvs_x4/infinite_and_helis.insv`, the first pass reports:

- 176,976 IMU samples over 176.333 seconds.
- Mean load magnitude of about 1.17 g.
- Maximum load magnitude of about 6.12 g.
- Maximum gyro magnitude of about 5.60 in the extracted gyro units.
- 13 candidate event intervals with default thresholds.

The strongest cluster is around 25-39 seconds.  The detector now reports this
as an `infinite_tumble_candidate` because a sustained rotation interval contains
repeated high-G load pulses.  Several lower-G, single-axis rotation candidates
appear later and are plausible heli-labeling targets.

## Labeling Next

The next step is a label CSV checked against video:

```csv
source_path,start_seconds,end_seconds,label,notes
sample_insvs_x4/infinite_and_helis.insv,25.3,39.0,infinite_tumbling,
sample_insvs_x4/infinite_and_helis.insv,44.6,49.2,heli,
```

Once enough intervals are labeled, feature windows can be assigned labels by
window midpoint and used to train a supervised maneuver classifier.
