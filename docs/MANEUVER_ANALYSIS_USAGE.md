# Maneuver Analysis Usage

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

---

# Metric Reference

This section documents all metrics used in analysis, plotting, and feature extraction.

| Metric         | Meaning / Physical Interpretation                                                                 | Units         | Usage                                      |
|----------------|--------------------------------------------------------------------------------------------------|---------------|---------------------------------------------|
| `accel_x`      | Raw accelerometer X-axis (camera/helmet frame)                                                    | g (gravity)   | Raw data, plotting, features                |
| `accel_y`      | Raw accelerometer Y-axis (camera/helmet frame)                                                    | g (gravity)   | Raw data, plotting, features                |
| `accel_z`      | Raw accelerometer Z-axis (camera/helmet frame)                                                    | g (gravity)   | Raw data, plotting, features                |
| `gyro_x`       | Raw gyroscope X-axis (angular velocity, camera/helmet frame)                                      | rad/s or dev. units | Raw data, plotting, features         |
| `gyro_y`       | Raw gyroscope Y-axis (angular velocity, camera/helmet frame)                                      | rad/s or dev. units | Raw data, plotting, features         |
| `gyro_z`       | Raw gyroscope Z-axis (angular velocity, camera/helmet frame)                                      | rad/s or dev. units | Raw data, plotting, features         |
| `g_force`      | Total acceleration magnitude: `sqrt(ax² + ay² + az²)`; proxy for “load” experienced by pilot      | g (gravity)   | Plotting, event detection, features         |
| `gyro_norm`    | Total angular velocity magnitude: `sqrt(gx² + gy² + gz²)`; proxy for “overall rotation rate”      | rad/s or dev. units | Plotting, event detection, features  |
| `jerk_g_s`     | Rate of change of acceleration magnitude (finite-difference of `g_force`)                         | g/s           | Features, event enrichment                  |
| `dominant_gyro_axis` | Which axis (x/y/z) dominates rotation in a segment/window                                   | (x/y/z)       | Event/feature enrichment                    |
| `gyro_axis_dominance`| Fraction of total rotation energy in the dominant axis                                      | [0, 1]        | Event/feature enrichment                    |
| `gyro_sign_changes_x/y/z` | Number of zero-crossings (sign changes) in each gyro axis (proxy for oscillation)      | count         | Features                                   |
| `mean`, `std`, `min`, `max`, `median`, `p95` | Statistical features computed per window/segment for each signal above | varies        | ML features, event stats                    |

**Notes:**
- All accelerometer and gyroscope axes are in the camera/helmet frame, not Earth or wing frame.
- `g_force` is a proxy for “load” (includes gravity); high values indicate strong forces (e.g., bottom of a tumble).
- `gyro_norm` is a proxy for “how fast the pilot/camera is rotating” regardless of axis.
- `jerk_g_s` captures how quickly load is changing (e.g., sharp transitions).
- `dominant_gyro_axis` and `gyro_axis_dominance` help distinguish single-axis spins (e.g., heli) from multi-axis maneuvers.
- `gyro_sign_changes_*` are a proxy for oscillatory or reversing motion.

**Usage in Pipeline:**
- **Plotting:** All metrics above can be plotted via CLI (`--plot-metrics ...`).
- **Event Detection:** `g_force` and `gyro_norm` are thresholded for candidate detection; dominance metrics for spin detection.

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
  ../sample_insvs_x4/infinite_and_helis.insv \
  --summary \
  --summary-output analysis_outputs/infinite_and_helis_summary.txt \
  --events analysis_outputs/infinite_and_helis_events.json \
  --features analysis_outputs/infinite_and_helis_features.csv
```

```bash
python3 insv_maneuver_analysis.py \
  ../sample_insvs_x4/infinite_and_helis.insv \
  --summary \
  --start-seconds 24.0 \
  --end-seconds 40.0 \
  --plot-raw \
  --plot
```

**Restrict analysis to a time window (optional):**

```bash
python3 insv_maneuver_analysis.py \
  ../sample_insvs_x4/infinite_and_helis.insv \
  --summary \
  --start-seconds 25.0 \
  --end-seconds 39.0
```

- `--start-seconds`: Only analyze samples at or after this time (seconds).
- `--end-seconds`: Only analyze samples at or before this time (seconds).
- If omitted, the full file is analyzed.

The JSON contains recording-level statistics and candidate intervals.  The CSV
contains one row per sliding window and is intended as input to future
supervised learning.

## Plot The Timeline

The analyzer can open an interactive matplotlib GUI plotting selected metrics
against time on one shared axis, with candidate intervals shaded and labeled.
Metric lines are plotted as raw values by default.  Jerk is still available, but
it is not plotted by default because differentiating acceleration amplifies
sensor noise and can visually dominate the other traces.

One-time setup:

```bash
python3 -m pip install -r requirements.txt
brew install python-tk@3.12
```

Open the default plot:

```bash
python3 insv_maneuver_analysis.py
```

This defaults to:

```bash
python3 insv_maneuver_analysis.py \
  ../sample_insvs_x4/infinite_and_helis.insv \
  --summary-output analysis_outputs/infinite_and_helis_summary.txt \
  --plot \
  --plot-raw \
  --plot-max-points 10000
```

Open the file/plot launcher:

```bash
python3 insv_maneuver_analysis.py --gui
```

The launcher includes file selection, optional start/end seconds, max plot
points, raw/normalized plotting, a **Preview Stats** button, and a scrollable
stats box for the selected file or time window. The stats box currently shows
sample count, duration, sample rate, g-force min/mean/median/p95/max,
acceleration magnitude in m/s², peak acceleration axis sample, gyro norm
min/mean/median/p95/max, peak gyro axis sample, jerk, axis averages/ranges,
candidate event count, and damping fit when available.

Save the same plot to a PNG instead of opening the GUI:

```bash
python3 insv_maneuver_analysis.py \
  ../sample_insvs_x4/infinite_and_helis.insv \
  --summary-output analysis_outputs/infinite_and_helis_summary.txt \
  --plot-output analysis_outputs/infinite_and_helis_timeline.png \
  --no-plot
```

Useful plot options:

```bash
python3 insv_maneuver_analysis.py video.insv \
  --plot \
  --plot-metrics g_force gyro_norm accel_x accel_y accel_z \
  --plot-smooth-seconds 0.2
```

Use `--plot-raw` if you want the original metric scales instead of normalized
values. Use `--plot-normalized` to compare selected metrics on a shared robust
normalized scale.

Stats panel options worth adding next:

- Load metrics: max/mean/min/median/p95/p99 g-force, max acceleration in m/s²,
  load-pulse count, peak/valley g labels.
- Rotation metrics: gyro norm max/mean/p95, per-axis gyro min/max/mean, dominant
  gyro axis, axis dominance, gyro sign changes.
- Time-window metrics: selected start/end/duration, sample count, sample rate,
  event count by candidate type.
- Damping metrics: peak count, peak period, damping coefficient, half-life,
  damping ratio estimate, fit quality.
- Raw-axis metrics: accelerometer and gyro x/y/z ranges, means, standard
  deviations, absolute peak axis samples.
- Feature-window metrics: strongest feature window, per-window max load,
  per-window max rotation, candidate overlap.
- Speed/velocity metrics: unavailable until a GPS, velocity, optical-flow, or
  video-derived speed source is parsed.

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

On `../sample_insvs_x4/infinite_and_helis.insv`, the first pass reports:

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
../sample_insvs_x4/infinite_and_helis.insv,25.3,39.0,infinite_tumbling,
../sample_insvs_x4/infinite_and_helis.insv,44.6,49.2,heli,
```

Once enough intervals are labeled, feature windows can be assigned labels by
window midpoint and used to train a supervised maneuver classifier.

---


## More Detail

See [MANEUVER_ANALYSIS.md](MANEUVER_ANALYSIS.md) for functionality, variables, algorithms, plotting internals, and CLI reference.
