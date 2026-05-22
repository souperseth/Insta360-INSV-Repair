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
  sample_insvs_x4/infinite_and_helis.insv \
  --summary \
  --summary-output analysis_outputs/infinite_and_helis_summary.txt \
  --events analysis_outputs/infinite_and_helis_events.json \
  --features analysis_outputs/infinite_and_helis_features.csv
```

```bash
python3 insv_maneuver_analysis.py \
  sample_insvs_x4/infinite_and_helis.insv \
  --summary \
  --start-seconds 24.0 \
  --end-seconds 40.0 \
  --plot-raw \
  --plot
```

**Restrict analysis to a time window (optional):**

```bash
python3 insv_maneuver_analysis.py \
  sample_insvs_x4/infinite_and_helis.insv \
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
  --start-seconds 24.0 \
  --end-seconds 40.0 \
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

---

# Internal Logic Reference

This section documents the algorithms, data structures, and internal logic of
`insv_maneuver_analysis.py` in detail.

## 1. Pipeline Architecture & Data Flow

The analysis follows a linear pipeline:

```
INSV file
  → insv_accelerometer.read_accelerometer_timeseries()   [raw IMU frames]
  → derive_motion_samples()                               [MotionSample list]
  → detect_candidate_segments()                           [enriched event dicts]
  → make_feature_windows()                                [ML feature rows]
  → estimate_damping()                                    [decay fit dict]
  → output (JSON / CSV / human summary / matplotlib plot)
```

`analyze_file()` orchestrates one pass through this pipeline for a single INSV
file.  The CLI `main()` iterates over multiple input files and dispatches
results to the requested outputs.

### Caching

Two module-level dictionaries avoid recomputing expensive results when the same
data is accessed more than once (e.g. summary + plot in one run):

| Cache                       | Key                                      | Stored value            |
|-----------------------------|------------------------------------------|-------------------------|
| `_motion_samples_cache`     | `data.source_path` (file path string)    | `list[MotionSample]`    |
| `_candidate_segments_cache` | Tuple of `(id(samples), sample_rate_hz, smooth_seconds, …)` — all detection parameters | `list[dict]` (enriched events) |

Cache keys are designed so that changing any detection threshold produces a
cache miss and a fresh computation.

## 2. Core Data Structures

### `MotionSample` (frozen dataclass)

One row of IMU data plus derived scalars:

| Field       | Source                                          |
|-------------|-------------------------------------------------|
| `index`     | Original sample index from the INSV file        |
| `time`      | `relative_seconds` from the accelerometer layer |
| `accel_x/y/z` | Raw accelerometer channels                   |
| `gyro_x/y/z`  | Raw gyroscope channels                       |
| `g_force`   | `√(ax² + ay² + az²)` — total acceleration magnitude |
| `gyro_norm` | `√(gx² + gy² + gz²)` — total angular velocity magnitude |
| `jerk_g_s`  | `‖Δa‖ / Δt` — finite-difference jerk (0.0 for the first sample) |

### `Segment` (frozen dataclass)

A detected candidate time interval before enrichment:

| Field          | Meaning                              |
|----------------|--------------------------------------|
| `kind`         | Event type string (e.g. `'high_g_load_pulse_candidate'`) |
| `start` / `end` | Start/end times in seconds         |
| `sample_start` / `sample_end` | Index range into the sample list |
| `duration`     | Computed property: `end - start`     |

After detection, segments are enriched into plain dicts with statistics,
dominant axis info, damping estimates, and load pulse records.

## 3. Derived Signal Computation

### `derive_motion_samples(data)`

Iterates over raw accelerometer samples once:

1. **G-force**: Euclidean norm of the three accelerometer axes.  This is the
   total acceleration magnitude in g-units as reported by the sensor — it
   includes gravity, so quiet sitting reads ≈1.0 g.

2. **Gyro norm**: Euclidean norm of the three gyroscope axes, giving a single
   scalar rotation rate regardless of axis.

3. **Jerk**: For each consecutive pair of samples, compute:
   ```
   jerk = ‖a[i] − a[i−1]‖ / (t[i] − t[i−1])
   ```
   This is the Euclidean norm of the acceleration *difference* vector divided
   by the time step — a proxy for how quickly load is changing.  The first
   sample gets `jerk = 0.0`.

## 4. Signal Processing Helpers

### `_rolling_mean(values, window_size)`

A causal (left-aligned) sliding-window average.  During the ramp-up phase
(fewer than `window_size` values accumulated), it averages all values seen so
far.  This avoids shrinking the output array but means the first few values
have a shorter effective window.

Used for smoothing both detection signals and plot lines.

### `_find_peaks(values, min_distance, min_value)`

A greedy non-maximum-suppression peak detector:

1. Collect all local maxima: indices where `values[i] ≥ values[i−1]` and
   `values[i] > values[i+1]` and `values[i] ≥ min_value`.
2. Sort candidates by amplitude (descending).
3. Greedily select peaks: accept a candidate only if it is at least
   `min_distance` samples away from every already-selected peak.
4. Return selected indices sorted by time.

This ensures the strongest peaks are kept and weaker nearby peaks are
suppressed, similar to how scipy's `find_peaks(distance=…)` works but without
the dependency.

### `_normalize_for_plot(values)`

Robust percentile normalization for overlaying different-scale metrics:

1. Sort values; compute p1 and p99 percentiles.
2. Add 5% padding on each side: `span_min = p1 − 0.05·(p99−p1)`,
   `span_max = p99 + 0.05·(p99−p1)`.
3. Linearly map values into `[0, 1]` using this span.
4. Return the normalized list and a human-readable scale label.

If the signal is constant (p99 ≤ p1), all values map to 0.5.

### `_percentile_sorted(values, percentile)`

Linear interpolation percentile on a pre-sorted list.  Avoids importing numpy
for a single scalar percentile.

### `_stats(values)`

Returns a dict with `count`, `mean`, `std` (population), `min`, `max`,
`median`, `p95`, `p99` — used throughout for per-segment and per-window summary
statistics.

## 5. Segment Detection Algorithms

All detectors operate on smoothed signals.  The smoothing window width is
`round(sample_rate_hz × smooth_seconds)` samples (default 0.5 s).

### 5.1 Threshold Segmentation — `_threshold_segments()`

The simplest detector.  Given a 1-D signal and a threshold:

1. Walk through samples.  Mark the start of a run when the value first reaches
   the threshold; mark the end when it drops below.
2. Discard runs shorter than `min_duration_seconds`.
3. Pass surviving runs to `_merge_segments()`.

No hysteresis band is used — the same threshold is used for entering and
leaving.  This is adequate for smoothed signals.

### 5.2 Combined (AND-gate) Segments — `_combined_segments()`

Creates a synthetic binary signal:
```
combined[i] = 1.0  if  left[i] ≥ left_threshold  AND  right[i] ≥ right_threshold
              0.0  otherwise
```
Then feeds it to `_threshold_segments` with `threshold=1.0`.

Used for `loaded_rotation_phase_candidate`: intervals where *both* g-force and
rotation exceed their thresholds simultaneously.

### 5.3 Single-Axis Spin — `_single_axis_spin_segments()`

Detects fast rotation dominated by a single gyroscope axis:

1. Smooth the absolute value of each gyro axis independently.
2. At each sample, compute the dominance ratio:
   `dominance = max(|gx|, |gy|, |gz|) / (|gx| + |gy| + |gz|)`
3. Mark a sample as "spin" if `gyro_norm ≥ rotation_threshold` AND
   `dominance ≥ dominance_threshold` (default 0.58).
4. Feed the binary signal to `_threshold_segments`.

A dominance of 0.58 means one axis carries ≥58% of the total rotation energy,
indicating rotation primarily around one body axis (e.g. a helicopter spin
around the yaw axis).

### 5.4 Infinite Tumble — `_infinite_tumble_segments()`

The most composite detector.  Infinite tumbling produces sustained rotation
*and* periodic high-G load pulses at the bottom of each rotation (when the
pilot swings under the glider):

1. Start from already-detected `rotation_segments`.
2. Expand each rotation segment by `context_seconds` (default 1.0 s) on both
   sides.
3. Find all `load_segments` that overlap this expanded window.
4. Count the number of load *peak indices* (from `_find_peaks` on the smoothed
   g-force signal) that fall within the combined rotation + load sample range.
5. If the peak count ≥ `min_load_pulses` (default 2), emit a candidate.
6. Merge nearby candidates with `_merge_segments`.

This is intentionally conservative — two distinct load spikes during sustained
rotation is the minimum evidence for the tumbling pattern.

### 5.5 Segment Merging — `_merge_segments()`

Given a list of `(start_index, end_index)` spans:

- If the gap between the end of one span and the start of the next is
  ≤ `merge_gap_seconds`, merge them into one continuous segment.
- Convert merged index spans into `Segment` objects with proper times.

### 5.6 Segment Enrichment — `_enrich_segment()`

Converts a raw `Segment` into a rich dict by computing over the segment's
sample window:

1. **Statistics**: `_stats()` for g_force, gyro_norm, and jerk within the
   segment.
2. **Dominant gyro axis**: which of x/y/z has the highest mean absolute
   gyroscope reading, and what fraction of total rotation it represents.
3. **Damping estimate**: calls `estimate_damping()` on the segment's samples.
4. **Load pulse records** (if load peaks fall inside the segment):
   - For each peak index, search a local window (±`load_peak_spacing` seconds)
     for the true maximum and minimum g-force samples.
   - Record peak time, peak g-force, valley time, and valley g-force.
   - Deduplicate overlapping peaks by keeping the strongest.
   - Compute median period between peaks.
5. **Phase role annotations**: semantic labels like `load_phase_not_maneuver`
   for high-G segments and `rotation_with_repeated_load_pulses` for infinite
   tumble candidates.

## 6. Exponential Decay / Damping Estimation

### `estimate_damping(samples, signal_name, …)`

Estimates how quickly oscillations decay — useful for assessing exit/recovery
quality from a maneuver:

1. Extract the chosen signal (default: g_force) from all samples.
2. Compute the baseline as `median(signal)`.
3. Compute amplitude: `|value − baseline|` for each sample.
4. Find peaks in the amplitude envelope using `_find_peaks`.
5. If fewer than 3 peaks, return early (insufficient data for a fit).

### `_fit_exponential_decay(peak_times, peak_amplitudes)`

Fits an exponential envelope `A·e^(−λt)` to the sequence of peak amplitudes:

1. Take the natural log of each amplitude: `y = ln(amplitude)`.
2. Perform ordinary least-squares linear regression of `y` vs. `t`:
   ```
   slope = Σ(t−t̄)(y−ȳ) / Σ(t−t̄)²
   intercept = ȳ − slope·t̄
   ```
3. The damping coefficient is `λ = −slope`.
4. Half-life: `t½ = ln(2) / λ`.
5. Estimate the median oscillation period from the peak times.
6. Approximate angular frequency: `ω = 2π / period`.
7. Damping ratio estimate: `ζ = λ / √(ω² + λ²)`.
8. R² goodness-of-fit on the log-transformed data.

All of this is done without scipy — just basic linear algebra on the
log-transformed peak amplitudes.

## 7. ML Feature Windows

### `make_feature_windows(source_path, samples, window_seconds, step_seconds)`

Generates sliding-window feature rows for supervised learning:

1. Slide a window of `window_seconds` (default 2.0 s) with a step of
   `step_seconds` (default 0.5 s) across the recording.
2. For each window, call `_window_features()`.

### `_window_features(window)`

Computes per-window features:

| Feature group            | Signals                                           | Stats computed          |
|--------------------------|---------------------------------------------------|-------------------------|
| Per-signal statistics    | accel_x/y/z, gyro_x/y/z, g_force, gyro_norm, jerk_g_s | mean, std, min, max, median, p95 |
| Dominant axis            | accel (x/y/z), gyro (x/y/z)                      | axis letter, dominance ratio |
| Gyro sign changes        | gyro_x, gyro_y, gyro_z                           | count of zero-crossings |

This produces ~60+ numeric features per window, suitable for tree-based
classifiers or neural networks.

## 8. Plotting Pipeline

### `plot_timeline()` — Layout

The figure uses a 2×2 `GridSpec`:

```
┌──────────────────────────────┬────────────────┐
│  Metric lines (ax)           │                │
│  with event shading          │  Candidate Key │
│  and peak/valley markers     │  (key_ax)      │
├──────────────────────────────┤                │
│  Event lane bars (event_ax)  │                │
│  with hover tooltips         │                │
└──────────────────────────────┴────────────────┘
```

Width ratio 5.8 : 1.45, height ratio 4.6 : 1.55.

### Metric Line Rendering

1. **Downsampling**: If the recording has more than `max_points` (default
   60,000) samples, a stride is applied: `stride = ceil(N / max_points)`.
2. **Smoothing**: A rolling mean of `plot_smooth_seconds` (default 0.1 s) is
   applied to each metric signal.
3. **Normalization**: Each metric is independently normalized via
   `_normalize_for_plot()` (p1/p99 robust scaling to [0,1]) unless
   `--plot-raw` is specified.
4. Lines are plotted with colors from `PLOT_COLORS`.

### Peak / Valley Marker Alignment

G-force peak and valley markers from load pulse records must align visually
with the *plotted* g-force line (which is smoothed, downsampled, and
normalized).  Rather than re-normalizing raw peak values, the code:

1. Stores the plotted g_force line's `(times, values)` after all
   transformations.
2. For each peak/valley time, uses `np.interp(time, plot_times, plot_values)`
   to find the exact y-coordinate on the rendered line.
3. Places the marker at `interp_y ± marker_offset` and the label at
   `interp_y ± (marker_offset + label_offset)`.

This guarantees markers sit precisely on the line regardless of normalization
or smoothing parameters.

### Event Bars & Hover Tooltips

- Each candidate event is drawn as a `Rectangle` patch in the event lane axis,
  positioned by event kind (reversed `EVENT_ORDER` determines lane index).
- Text labels inside rectangles are conditionally shown: on each
  `xlim_changed` or `resize_event`, the code measures the pixel width of the
  rectangle vs. the rendered text width (plus padding).  Labels are hidden when
  the bar is too narrow.
- When hovering over a bar whose label is hidden, a tooltip annotation appears
  above the bar showing the event summary (index, type, time range, pulse
  count).
- The candidate key (right panel) lists all events with colored swatches.

### Event Shading

Each candidate event is shaded on *both* the metric axis (α=0.16) and the
event axis (α=0.08), using the event's type color from `EVENT_COLORS`.

## 9. CLI Interface

### Output Modes

| Flag              | Output                                    |
|-------------------|-------------------------------------------|
| `--summary`       | Human-readable text to stdout            |
| `--summary-output`| Human-readable text to file              |
| `--events`        | JSON with summary + enriched events      |
| `--features`      | CSV with sliding-window feature rows     |
| `--plot`          | Interactive matplotlib GUI               |
| `--plot-output`   | Save plot to image file (PNG, etc.)      |

If none of `--events`, `--features`, `--summary-output` are given, `--summary`
is implied.

### Detection Threshold Defaults

| Parameter                          | Default | Meaning                                    |
|------------------------------------|---------|--------------------------------------------|
| `--smooth-seconds`                 | 0.50    | Rolling-mean window for detection signals  |
| `--min-duration-seconds`           | 0.75    | Minimum segment duration to keep           |
| `--merge-gap-seconds`              | 0.50    | Maximum gap before merging adjacent segments |
| `--high-g-threshold`               | 2.0     | G-force threshold for load pulse detection |
| `--rotation-threshold`             | 2.0     | Gyro norm threshold for rotation detection |
| `--single-axis-dominance`          | 0.58    | Minimum single-axis fraction for spin detection |
| `--load-peak-spacing`              | 0.35    | Minimum seconds between accepted load peaks |
| `--infinite-tumble-min-load-pulses`| 2       | Minimum load peaks to flag infinite tumble |
| `--infinite-tumble-context-seconds`| 1.0     | Time padding around rotation for pulse search |

### Plot Options

| Parameter              | Default       | Meaning                                |
|------------------------|---------------|----------------------------------------|
| `--plot-metrics`       | g_force gyro_norm | Which signals to render             |
| `--plot-raw`           | off           | Skip normalization                     |
| `--plot-max-points`    | 60000         | Downsample limit per line              |
| `--plot-smooth-seconds`| 0.10          | Visual smoothing (separate from detection) |

### Damping Options

| Parameter                | Default  | Meaning                              |
|--------------------------|----------|--------------------------------------|
| `--damping-signal`       | g_force  | Signal to analyze for decay          |
| `--damping-peak-spacing` | 0.35     | Minimum spacing between decay peaks  |
| `--damping-min-amplitude`| 0.08     | Minimum amplitude to count as a peak |

### Feature Window Options

| Parameter          | Default | Meaning                          |
|--------------------|---------|----------------------------------|
| `--window-seconds` | 2.0     | Sliding window width             |
| `--step-seconds`   | 0.5     | Sliding window step              |

## 10. Performance Notes

- **Matplotlib backend**: Selected early at import time — `TkAgg` for
  interactive use (`--plot`), `Agg` otherwise, avoiding GUI toolkit overhead
  for file-only output.
- **In-memory caching**: `derive_motion_samples` and
  `detect_candidate_segments` results are cached by file path / parameter
  tuple, so running summary + plot in one invocation doesn't recompute.
- **Downsampling**: The plot limits rendered points to `max_points` (default
  60,000) via strided indexing, keeping rendering fast even for long
  recordings.
- **No external dependencies** for analysis: only stdlib + `insv_accelerometer`.
  Matplotlib and numpy are optional, required only for `--plot`.
