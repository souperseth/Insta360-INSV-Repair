#!/usr/bin/env python3
"""Analyze Insta360 INSV IMU data for acro paragliding maneuvers.

--------------------------------------------------------------------------------
Candidate Intervals (Event Detection)
--------------------------------------------------------------------------------

A “candidate” is a coarse, threshold-based time interval that may correspond to
an acro maneuver or a phase of interest (e.g., high-G load, fast rotation, etc.).
Candidates are NOT final maneuver labels—they are intended for review, labeling,
or as input to further machine learning.

Candidate detection is performed by `detect_candidate_segments()` and related
helpers. The following candidate types are detected:

- high_g_load_pulse_candidate: sustained load above the high-G threshold (bottom/load phase)
- fast_rotation_candidate: sustained angular velocity above the rotation threshold
- loaded_rotation_phase_candidate: high load and fast rotation at the same time
- infinite_tumble_candidate: sustained rotation with repeated high-G load pulses
- single_axis_spin_candidate: fast rotation dominated by one gyro axis

Detection logic:
- Signals are smoothed and thresholded to find above-threshold runs.
- Segments are merged if close in time.
- Composite candidates (e.g., loaded_rotation_phase_candidate) require multiple conditions.
- Infinite tumble candidates require both sustained rotation and repeated load pulses.
- Each candidate segment is “enriched” with statistics, dominant axis, damping, and load pulse records.

Intended use:
- Candidates are for review, labeling, or as ML features—not for direct maneuver classification.
- See docs/MANEUVER_ANALYSIS_USAGE.md “Candidate Event Types” and “Metric Reference” for more.

--------------------------------------------------------------------------------

This module intentionally keeps the first analysis layer dependency-free.  It
extracts accelerometer/gyro samples with ``insv_accelerometer.py``, computes
derived load and rotation signals, finds coarse candidate maneuver intervals,
and exports machine-learning feature windows.
"""

from __future__ import annotations

import os
import sys
import importlib

os.environ.setdefault(
    'MPLCONFIGDIR',
    os.path.join(os.getcwd(), '.cache', 'matplotlib'),
)
os.environ.setdefault(
    'XDG_CACHE_HOME',
    os.path.join(os.getcwd(), '.cache'),
)


def _configure_matplotlib_backend() -> None:
    """Select a Matplotlib backend before pyplot is imported."""
    try:
        import matplotlib
    except ImportError:
        return

    script_name = os.path.basename(sys.argv[0])
    script_invocation = script_name in {
        os.path.basename(__file__),
        'insv_maneuver_analysis.py',
    }
    interactive_plot = (
        "--plot" in sys.argv
        or (
            script_invocation
            and "--no-plot" not in sys.argv
            and "--gui" not in sys.argv
        )
    )
    if not interactive_plot:
        matplotlib.use("Agg")
        return

    candidates = []
    if sys.platform == "darwin":
        candidates.append(("MacOSX", "matplotlib.backends.backend_macosx"))
    candidates.extend(
        [
            ("TkAgg", "matplotlib.backends.backend_tkagg"),
            ("QtAgg", "matplotlib.backends.backend_qtagg"),
        ]
    )
    for backend_name, module_name in candidates:
        try:
            importlib.import_module(module_name)
            matplotlib.use(backend_name)
            return
        except Exception:
            continue
    matplotlib.use("Agg")


_configure_matplotlib_backend()

import argparse
import csv
import json
import math
import os
import statistics
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Optional, TextIO, cast

try:
    from .accelerometer import (
        AccelerometerData,
        INSVAccelerometerError,
        read_accelerometer_timeseries,
    )
except ImportError:
    # Allow direct execution of this file during development.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from insv_tools.accelerometer import (  # type: ignore[no-redef]
        AccelerometerData,
        INSVAccelerometerError,
        read_accelerometer_timeseries,
    )


DEFAULT_SMOOTH_SECONDS = 0.50
DEFAULT_MIN_DURATION_SECONDS = 0.75
DEFAULT_MERGE_GAP_SECONDS = 0.50
DEFAULT_HIGH_G_THRESHOLD = 2.0
DEFAULT_ROTATION_THRESHOLD = 2.0
DEFAULT_SINGLE_AXIS_DOMINANCE = 0.58
DEFAULT_LOAD_PEAK_SPACING_SECONDS = 0.35
DEFAULT_INFINITE_TUMBLE_MIN_LOAD_PULSES = 2
DEFAULT_INFINITE_TUMBLE_CONTEXT_SECONDS = 1.0
DEFAULT_WINDOW_SECONDS = 2.0
DEFAULT_STEP_SECONDS = 0.5
DEFAULT_INPUT_PATH = '../sample_insvs_x4/infinite_and_helis.insv'
DEFAULT_SUMMARY_OUTPUT = 'analysis_outputs/infinite_and_helis_summary.txt'
DEFAULT_PLOT_MAX_POINTS = 10000
DEFAULT_INTERACTIVE_PLOT_MAX_POINTS = 20000
STANDARD_GRAVITY_M_S2 = 9.80665
DEFAULT_PLOT_METRICS = ('g_force', 'gyro_norm')
PLOT_METRIC_CHOICES = (
    'g_force',
    'gyro_norm',
    'jerk_g_s',
    'accel_x',
    'accel_y',
    'accel_z',
    'gyro_x',
    'gyro_y',
    'gyro_z',
)
PLOT_COLORS = {
    'g_force': '#d62728',
    'gyro_norm': '#1f77b4',
    'jerk_g_s': '#2ca02c',
    'accel_x': '#9467bd',
    'accel_y': '#8c564b',
    'accel_z': '#e377c2',
    'gyro_x': '#17becf',
    'gyro_y': '#bcbd22',
    'gyro_z': '#ff7f0e',
}
EVENT_COLORS = {
    'infinite_tumble_candidate': '#7b2cbf',
    'high_g_load_pulse_candidate': '#e63946',
    'loaded_rotation_phase_candidate': '#f77f00',
    'fast_rotation_candidate': '#1d4ed8',
    'single_axis_spin_candidate': '#15803d',
}
EVENT_LABELS = {
    'infinite_tumble_candidate': 'Infinite tumble',
    'high_g_load_pulse_candidate': 'Load pulse',
    'loaded_rotation_phase_candidate': 'Loaded rotation',
    'fast_rotation_candidate': 'Fast rotation',
    'single_axis_spin_candidate': 'Single-axis spin',
}
EVENT_ORDER = (
    'infinite_tumble_candidate',
    'loaded_rotation_phase_candidate',
    'high_g_load_pulse_candidate',
    'fast_rotation_candidate',
    'single_axis_spin_candidate',
)


@dataclass(frozen=True)
class MotionSample:
    """One IMU sample plus derived force/rotation features."""

    index: int
    time: float
    accel_x: float
    accel_y: float
    accel_z: float
    gyro_x: float
    gyro_y: float
    gyro_z: float
    g_force: float
    gyro_norm: float
    jerk_g_s: float


@dataclass(frozen=True)
class Segment:
    """A detected candidate interval."""

    kind: str
    start: float
    end: float
    sample_start: int
    sample_end: int

    @property
    def duration(self) -> float:
        return self.end - self.start


# --- In-memory cache for expensive computations ---
_motion_samples_cache = {}
_candidate_segments_cache = {}

def derive_motion_samples(data: AccelerometerData) -> list[MotionSample]:
    """Return samples with vector magnitudes and jerk estimates."""
    cache_key = getattr(data, "source_path", None)
    if cache_key in _motion_samples_cache:
        return _motion_samples_cache[cache_key]

    derived: list[MotionSample] = []
    previous = None
    for sample in data.samples:
        g_force = _norm3(sample.accel_x, sample.accel_y, sample.accel_z)
        gyro_norm = _norm3(sample.gyro_x, sample.gyro_y, sample.gyro_z)
        jerk = 0.0
        if previous is not None:
            dt = sample.relative_seconds - previous.relative_seconds
            if dt > 0:
                da = _norm3(
                    sample.accel_x - previous.accel_x,
                    sample.accel_y - previous.accel_y,
                    sample.accel_z - previous.accel_z,
                )
                jerk = da / dt
        derived.append(MotionSample(
            index=sample.index,
            time=sample.relative_seconds,
            accel_x=sample.accel_x,
            accel_y=sample.accel_y,
            accel_z=sample.accel_z,
            gyro_x=sample.gyro_x,
            gyro_y=sample.gyro_y,
            gyro_z=sample.gyro_z,
            g_force=g_force,
            gyro_norm=gyro_norm,
            jerk_g_s=jerk,
        ))
        previous = sample
    if cache_key is not None:
        _motion_samples_cache[cache_key] = derived
    return derived


def stabilize_gyro_samples(
    samples: list[MotionSample],
    sample_rate_hz: float,
    *,
    method: str = 'rolling-mean',
    window_seconds: float = 2.0,
    strength: float = 1.0,
) -> list[MotionSample]:
    """Return samples with slow gyro bias/drift removed and gyro_norm recomputed."""
    if not samples:
        return []
    if window_seconds <= 0:
        raise ValueError('gyro stabilizer window must be positive')
    if not 0 <= strength <= 1:
        raise ValueError('gyro stabilizer strength must be between 0 and 1')

    gyro_x = [sample.gyro_x for sample in samples]
    gyro_y = [sample.gyro_y for sample in samples]
    gyro_z = [sample.gyro_z for sample in samples]

    if method == 'mean':
        baseline_x = [statistics.fmean(gyro_x) for _ in samples]
        baseline_y = [statistics.fmean(gyro_y) for _ in samples]
        baseline_z = [statistics.fmean(gyro_z) for _ in samples]
    elif method == 'rolling-mean':
        window_n = max(1, round(sample_rate_hz * window_seconds))
        baseline_x = _centered_rolling_mean(gyro_x, window_n)
        baseline_y = _centered_rolling_mean(gyro_y, window_n)
        baseline_z = _centered_rolling_mean(gyro_z, window_n)
    else:
        raise ValueError(f'unknown gyro stabilizer method {method!r}')

    stabilized: list[MotionSample] = []
    for sample, bx, by, bz in zip(samples, baseline_x, baseline_y, baseline_z):
        gx = sample.gyro_x - strength * bx
        gy = sample.gyro_y - strength * by
        gz = sample.gyro_z - strength * bz
        stabilized.append(MotionSample(
            index=sample.index,
            time=sample.time,
            accel_x=sample.accel_x,
            accel_y=sample.accel_y,
            accel_z=sample.accel_z,
            gyro_x=gx,
            gyro_y=gy,
            gyro_z=gz,
            g_force=sample.g_force,
            gyro_norm=_norm3(gx, gy, gz),
            jerk_g_s=sample.jerk_g_s,
        ))
    return stabilized


def summarize_motion(data: AccelerometerData, samples: list[MotionSample]) -> dict[str, object]:
    """Build recording-level summary statistics."""
    g_force = [sample.g_force for sample in samples]
    gyro = [sample.gyro_norm for sample in samples]
    jerk = [sample.jerk_g_s for sample in samples[1:]]
    duration_seconds = samples[-1].time - samples[0].time if len(samples) > 1 else 0.0
    sample_rate_hz = _sample_rate_from_times([sample.time for sample in samples]) if len(samples) > 1 else 0.0
    return {
        'source_path': data.source_path,
        'sample_count': len(samples),
        'duration_seconds': duration_seconds,
        'sample_rate_hz': sample_rate_hz,
        'source_sample_count': data.sample_count,
        'source_duration_seconds': data.duration_seconds,
        'source_sample_rate_hz': data.sample_rate_hz,
        'trailer_used_directory_table': data.trailer.used_directory_table,
        'g_force': _stats(g_force),
        'gyro_norm': _stats(gyro),
        'jerk_g_s': _stats(jerk),
    }


def detect_candidate_segments(
    samples: list[MotionSample],
    sample_rate_hz: float,
    smooth_seconds: float = DEFAULT_SMOOTH_SECONDS,
    min_duration_seconds: float = DEFAULT_MIN_DURATION_SECONDS,
    merge_gap_seconds: float = DEFAULT_MERGE_GAP_SECONDS,
    high_g_threshold: float = DEFAULT_HIGH_G_THRESHOLD,
    rotation_threshold: float = DEFAULT_ROTATION_THRESHOLD,
    single_axis_dominance: float = DEFAULT_SINGLE_AXIS_DOMINANCE,
    load_peak_spacing_seconds: float = DEFAULT_LOAD_PEAK_SPACING_SECONDS,
    infinite_tumble_min_load_pulses: int = DEFAULT_INFINITE_TUMBLE_MIN_LOAD_PULSES,
    infinite_tumble_context_seconds: float = DEFAULT_INFINITE_TUMBLE_CONTEXT_SECONDS,
) -> list[dict[str, object]]:
    """Detect coarse acro maneuver candidates from load and rotation signals."""

    # Build a cache key from the main parameters
    cache_key = (
        id(samples),
        sample_rate_hz,
        smooth_seconds,
        min_duration_seconds,
        merge_gap_seconds,
        high_g_threshold,
        rotation_threshold,
        single_axis_dominance,
        load_peak_spacing_seconds,
        infinite_tumble_min_load_pulses,
        infinite_tumble_context_seconds,
    )
    if cache_key in _candidate_segments_cache:
        return _candidate_segments_cache[cache_key]

    if not samples:
        return []

    smooth_n = max(1, round(sample_rate_hz * smooth_seconds))
    times = [sample.time for sample in samples]
    g_smooth = _rolling_mean([sample.g_force for sample in samples], smooth_n)
    gyro_smooth = _rolling_mean([sample.gyro_norm for sample in samples], smooth_n)

    load_peak_indices = _find_peaks(
        g_smooth,
        min_distance=max(1, round(sample_rate_hz * load_peak_spacing_seconds)),
        min_value=high_g_threshold,
    )

    segments: list[Segment] = []
    load_segments = _threshold_segments(
        kind='high_g_load_pulse_candidate',
        times=times,
        values=g_smooth,
        threshold=high_g_threshold,
        min_duration_seconds=min_duration_seconds,
        merge_gap_seconds=merge_gap_seconds,
    )
    rotation_segments = _threshold_segments(
        kind='fast_rotation_candidate',
        times=times,
        values=gyro_smooth,
        threshold=rotation_threshold,
        min_duration_seconds=min_duration_seconds,
        merge_gap_seconds=merge_gap_seconds,
    )

    segments.extend(load_segments)
    segments.extend(rotation_segments)
    segments.extend(_combined_segments(
        kind='loaded_rotation_phase_candidate',
        times=times,
        left=g_smooth,
        left_threshold=high_g_threshold,
        right=gyro_smooth,
        right_threshold=rotation_threshold,
        min_duration_seconds=min_duration_seconds,
        merge_gap_seconds=merge_gap_seconds,
    ))
    segments.extend(_infinite_tumble_segments(
        times=times,
        rotation_segments=rotation_segments,
        load_segments=load_segments,
        load_peak_indices=load_peak_indices,
        min_load_pulses=infinite_tumble_min_load_pulses,
        context_seconds=infinite_tumble_context_seconds,
        merge_gap_seconds=merge_gap_seconds,
    ))
    segments.extend(_single_axis_spin_segments(
        samples=samples,
        times=times,
        gyro_smooth=gyro_smooth,
        rotation_threshold=rotation_threshold,
        dominance_threshold=single_axis_dominance,
        min_duration_seconds=min_duration_seconds,
        merge_gap_seconds=merge_gap_seconds,
        sample_rate_hz=sample_rate_hz,
    ))

    enriched = [_enrich_segment(segment, samples, load_peak_indices) for segment in segments]
    # Ensure enriched is a list, not object
    enriched = cast(list, enriched)
    enriched.sort(key=lambda event: (event['start_seconds'], event['kind']))
    _candidate_segments_cache[cache_key] = enriched
    return enriched


def make_feature_windows(
    source_path: str,
    samples: list[MotionSample],
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    step_seconds: float = DEFAULT_STEP_SECONDS,
) -> list[dict[str, object]]:
    """Create ML-ready feature rows for sliding windows."""
    if not samples:
        return []
    if window_seconds <= 0 or step_seconds <= 0:
        raise ValueError('window and step must be positive')

    rows: list[dict[str, object]] = []
    start = samples[0].time
    final_time = samples[-1].time
    source_name = os.path.basename(source_path)
    cursor = start
    sample_cursor = 0
    while cursor + window_seconds <= final_time + 1e-9:
        end = cursor + window_seconds
        while sample_cursor < len(samples) and samples[sample_cursor].time < cursor:
            sample_cursor += 1
        sample_end = sample_cursor
        while sample_end < len(samples) and samples[sample_end].time < end:
            sample_end += 1
        window = samples[sample_cursor:sample_end]
        if window:
            row = _window_features(window)
            row.update({
                'source_path': source_path,
                'source_name': source_name,
                'start_seconds': _round(cursor),
                'end_seconds': _round(end),
                'center_seconds': _round((cursor + end) / 2),
                'duration_seconds': _round(end - cursor),
                'sample_count': len(window),
            })
            rows.append(row)
        cursor += step_seconds
    return rows


def estimate_damping(
    samples: list[MotionSample],
    signal_name: str = 'g_force',
    min_peak_spacing_seconds: float = 0.35,
    min_amplitude: float = 0.08,
) -> dict[str, object]:
    """Estimate exponential decay from peaks in an oscillating signal.

    The result is most meaningful on an exit/recovery interval rather than a
    full recording.  It is still useful globally as a quick diagnostic.
    """
    if len(samples) < 3:
        return {'signal': signal_name, 'peak_count': 0, 'fit': None}

    times = [sample.time for sample in samples]
    values = [_sample_signal(sample, signal_name) for sample in samples]
    baseline = statistics.median(values)
    amplitudes = [abs(value - baseline) for value in values]
    sample_rate_hz = _sample_rate_from_times(times)
    min_distance = max(1, round(sample_rate_hz * min_peak_spacing_seconds))
    peaks = _find_peaks(amplitudes, min_distance=min_distance, min_value=min_amplitude)

    if len(peaks) < 3:
        return {
            'signal': signal_name,
            'baseline': baseline,
            'peak_count': len(peaks),
            'fit': None,
        }

    peak_times = [times[index] for index in peaks]
    peak_amplitudes = [amplitudes[index] for index in peaks]
    fit = _fit_exponential_decay(peak_times, peak_amplitudes)
    return {
        'signal': signal_name,
        'baseline': baseline,
        'peak_count': len(peaks),
        'first_peak_seconds': _round(peak_times[0]),
        'last_peak_seconds': _round(peak_times[-1]),
        'first_peak_amplitude': _round(peak_amplitudes[0]),
        'last_peak_amplitude': _round(peak_amplitudes[-1]),
        'fit': fit,
    }


def analyze_file(
    path: str,
    args: argparse.Namespace,
) -> tuple[dict[str, object], list[dict[str, object]], list[MotionSample]]:
    """Read and analyze one INSV file."""
    data = read_accelerometer_timeseries(path)
    samples = derive_motion_samples(data)
    # Filter by start/end seconds if provided
    start_sec = args.start_seconds if hasattr(args, "start_seconds") and args.start_seconds is not None else None
    end_sec = args.end_seconds if hasattr(args, "end_seconds") and args.end_seconds is not None else None
    if start_sec is not None or end_sec is not None:
        samples = [
            s for s in samples
            if (start_sec is None or s.time >= start_sec) and (end_sec is None or s.time <= end_sec)
        ]
        if not samples:
            print(f"[ERROR] No samples found in the specified time window ({start_sec} to {end_sec} seconds).", file=sys.stderr)
        else:
            print(f"[INFO] {len(samples)} samples in time window {start_sec} to {end_sec} seconds.", file=sys.stderr)
    if args.gyro_stabilize:
        samples = stabilize_gyro_samples(
            samples,
            sample_rate_hz=data.sample_rate_hz,
            method=args.gyro_stabilizer_method,
            window_seconds=args.gyro_stabilizer_window_seconds,
            strength=args.gyro_stabilizer_strength,
        )
        print(
            "[INFO] stabilized gyro data "
            f"method={args.gyro_stabilizer_method} "
            f"window={args.gyro_stabilizer_window_seconds:.3f}s "
            f"strength={args.gyro_stabilizer_strength:.3f}",
            file=sys.stderr,
        )
    summary = summarize_motion(data, samples)
    summary['gyro_stabilizer'] = {
        'enabled': bool(args.gyro_stabilize),
        'method': args.gyro_stabilizer_method if args.gyro_stabilize else None,
        'window_seconds': args.gyro_stabilizer_window_seconds if args.gyro_stabilize else None,
        'strength': args.gyro_stabilizer_strength if args.gyro_stabilize else None,
    }
    events = detect_candidate_segments(
        samples=samples,
        sample_rate_hz=data.sample_rate_hz,
        smooth_seconds=args.smooth_seconds,
        min_duration_seconds=args.min_duration_seconds,
        merge_gap_seconds=args.merge_gap_seconds,
        high_g_threshold=args.high_g_threshold,
        rotation_threshold=args.rotation_threshold,
        single_axis_dominance=args.single_axis_dominance,
        load_peak_spacing_seconds=args.load_peak_spacing,
        infinite_tumble_min_load_pulses=args.infinite_tumble_min_load_pulses,
        infinite_tumble_context_seconds=args.infinite_tumble_context_seconds,
    )
    summary['candidate_event_count'] = len(events)
    summary['damping'] = estimate_damping(
        samples,
        signal_name=args.damping_signal,
        min_peak_spacing_seconds=args.damping_peak_spacing,
        min_amplitude=args.damping_min_amplitude,
    )
    return {'summary': summary, 'events': events}, make_feature_windows(
        source_path=path,
        samples=samples,
        window_seconds=args.window_seconds,
        step_seconds=args.step_seconds,
    ), samples


def write_features_csv(rows: list[dict[str, object]], out: TextIO) -> None:
    """Write feature rows to CSV."""
    preferred = [
        'source_path',
        'source_name',
        'start_seconds',
        'end_seconds',
        'center_seconds',
        'duration_seconds',
        'sample_count',
    ]
    fields = preferred + sorted({key for row in rows for key in row if key not in preferred})
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)


def format_human_summary(results: list[dict[str, object]]) -> str:
    """Return compact text summaries for CLI use."""
    lines: list[str] = []
    for result in results:
        summary = cast(dict, result['summary'])
        lines.append(os.path.basename(str(summary['source_path'])))
        lines.append(
            f"  samples={summary['sample_count']:,} "
            f"duration={summary['duration_seconds']:.3f}s "
            f"rate={summary['sample_rate_hz']:.3f}Hz"
        )
        g_force = cast(dict, summary['g_force'])
        gyro = cast(dict, summary['gyro_norm'])
        lines.append(
            f"  g-force mean={g_force['mean']:.3f} "
            f"p95={g_force['p95']:.3f} max={g_force['max']:.3f}"
        )
        lines.append(
            f"  gyro norm mean={gyro['mean']:.3f} "
            f"p95={gyro['p95']:.3f} max={gyro['max']:.3f}"
        )
        stabilizer = summary.get('gyro_stabilizer')
        if isinstance(stabilizer, dict) and stabilizer.get('enabled'):
            lines.append(
                "  gyro stabilizer="
                f"{stabilizer.get('method')} "
                f"window={float(cast(float, stabilizer.get('window_seconds', 0.0))):.3f}s "
                f"strength={float(cast(float, stabilizer.get('strength', 0.0))):.3f}"
            )
        lines.append(f"  candidate events={summary['candidate_event_count']}")
        events = cast(list, result['events'])
        for event_obj in events[:12]:
            event = cast(dict, event_obj)
            g_force = cast(dict, event['g_force'])
            gyro_norm = cast(dict, event['gyro_norm'])
            lines.append(
                f"    {event['kind']} "
                f"{event['start_seconds']:.3f}-{event['end_seconds']:.3f}s "
                f"dur={event['duration_seconds']:.3f}s "
                f"max_g={g_force['max']:.3f} "
                f"max_gyro={gyro_norm['max']:.3f} "
                f"axis={event['dominant_gyro_axis']}"
                f"{_format_load_pulse_count(event)}"
            )
        events_len = len(events)
        if events_len > 12:
            lines.append(f"    ... {events_len - 12} more")
    return '\n'.join(lines) + '\n'


def print_human_summary(results: list[dict[str, object]], out: TextIO = sys.stdout) -> None:
    """Print compact text summaries for CLI use."""
    out.write(format_human_summary(results))


def plot_timeline(
    result: dict[str, object],
    samples: list[MotionSample],
    metrics: list[str],
    normalize: bool = True,
    max_points: int = 60000,
    smooth_seconds: float = 0.10,
    output: Optional[str] = None,
    show: bool = True,
    plot_cache_dir: Optional[str] = None,
    plot_cache_bypass: bool = False,
) -> None:
    """Open or save a matplotlib timeline plot for one analyzed file, with optional persistent image caching."""
    import hashlib

    # --- Persistent plot/image caching ---
    cache_dir = plot_cache_dir or os.path.join(os.getcwd(), '.cache', 'plots')
    # Only use persistent cache for non-interactive (output file) plots
    use_cache = output and not plot_cache_bypass and not (show and not output)
    if use_cache:
        os.makedirs(cache_dir, exist_ok=True)
        # Compose a cache key from input file, plot params, and code version
        source_path = str(result.get('summary', {}).get('source_path', ''))
        # Optionally, use file mtime for cache busting
        try:
            file_mtime = str(os.path.getmtime(source_path)) if source_path and os.path.exists(source_path) else ''
        except Exception:
            file_mtime = ''
        try:
            code_mtime = str(os.path.getmtime(__file__))
        except Exception:
            code_mtime = ''
        # Use git commit hash if available
        git_hash = ''
        git_head_path = os.path.join(os.getcwd(), '.git', 'HEAD')
        if os.path.exists(git_head_path):
            try:
                with open(git_head_path, 'r') as f:
                    ref = f.read().strip()
                if ref.startswith('ref:'):
                    ref_path = os.path.join(os.getcwd(), '.git', ref.split(' ')[1])
                    if os.path.exists(ref_path):
                        with open(ref_path, 'r') as f:
                            git_hash = f.read().strip()
                else:
                    git_hash = ref
            except Exception:
                pass
        sample_signature = (
            len(samples),
            _round(samples[0].time) if samples else None,
            _round(samples[-1].time) if samples else None,
            _round(samples[0].gyro_norm) if samples else None,
            _round(samples[-1].gyro_norm) if samples else None,
        )
        summary_for_cache = result.get('summary', {})
        stabilizer_signature = (
            summary_for_cache.get('gyro_stabilizer')
            if isinstance(summary_for_cache, dict) else None
        )
        events_signature = tuple(
            (
                event.get('kind'),
                event.get('start_seconds'),
                event.get('end_seconds'),
            )
            for event in result.get('events', [])
            if isinstance(event, dict)
        )
        cache_key_str = repr((
            source_path,
            file_mtime,
            metrics,
            normalize,
            max_points,
            smooth_seconds,
            output,
            git_hash,
            code_mtime,
            sample_signature,
            stabilizer_signature,
            events_signature,
        ))
        cache_key = hashlib.sha256(cache_key_str.encode('utf-8')).hexdigest()
        cache_file = os.path.join(cache_dir, f"{cache_key}.png")
        if os.path.exists(cache_file):
            import shutil
            shutil.copyfile(cache_file, output)
            # Do NOT display static image for interactive plots; always generate live plot for show=True
            return

    try:
        # Matplotlib already imported and backend set at top
        from matplotlib import pyplot as plt
        from matplotlib.backend_bases import MouseEvent
        from matplotlib.patches import Rectangle
    except ImportError as exc:
        raise RuntimeError(
            'matplotlib is required for --plot; install it with '
            '`.venv/bin/python -m pip install matplotlib`'
        ) from exc

    if not samples:
        raise ValueError('no samples available to plot')

    metrics = metrics or list(DEFAULT_PLOT_METRICS)
    unknown = sorted(set(metrics) - set(PLOT_METRIC_CHOICES))
    if unknown:
        raise ValueError(f'unknown plot metric(s): {", ".join(unknown)}')

    if show and not output:
        max_points = min(max_points, DEFAULT_INTERACTIVE_PLOT_MAX_POINTS)

    stride = max(1, math.ceil(len(samples) / max_points))
    times_all = [sample.time for sample in samples]
    times = times_all[::stride]
    sample_rate_hz = _sample_rate_from_times(times_all)
    smooth_n = max(1, round(sample_rate_hz * smooth_seconds))

    fig = plt.figure(figsize=(22, 9.6))
    grid = fig.add_gridspec(
        3,
        2,
        width_ratios=[5.65, 1.75],
        height_ratios=[3.25, 0.82, 1.55],
        hspace=0.025,
        wspace=0.04,
    )
    ax = fig.add_subplot(grid[0, 0])
    # --- New: Pitch/Roll/Yaw subplot ---
    pry_ax = fig.add_subplot(grid[1, 0], sharex=ax)
    event_ax = fig.add_subplot(grid[2, 0], sharex=ax)
    key_ax = fig.add_subplot(grid[:, 1])
    key_ax.axis('off')
    fig.subplots_adjust(
        left=0.055,
        right=0.985,
        top=0.93,
        bottom=0.075,
        hspace=0.18,
        wspace=0.08,
    )

    # --- Set xlim to exact data range (no x padding) ---
    if len(times) > 1:
        x_start = times[0]
        x_end = times[-1]
        ax.set_xlim(x_start, x_end)
        pry_ax.set_xlim(x_start, x_end)
        event_ax.set_xlim(x_start, x_end)

    # --- Plot pitch, roll, yaw rates ---
    times_all = [sample.time for sample in samples]
    stride = max(1, math.ceil(len(samples) / max_points))
    times = times_all[::stride]
    sample_rate_hz = _sample_rate_from_times(times_all)
    smooth_n = max(1, round(sample_rate_hz * smooth_seconds))
    roll = [_sample_signal(sample, "gyro_x") for sample in samples]
    pitch = [_sample_signal(sample, "gyro_y") for sample in samples]
    yaw = [_sample_signal(sample, "gyro_z") for sample in samples]
    if smooth_n > 1:
        roll = _rolling_mean(roll, smooth_n)
        pitch = _rolling_mean(pitch, smooth_n)
        yaw = _rolling_mean(yaw, smooth_n)
    roll = roll[::stride]
    pitch = pitch[::stride]
    yaw = yaw[::stride]
    pry_ax.plot(times, pitch, color="red", linewidth=1.0, label="Pitch rate (gyro_y)")
    pry_ax.plot(times, roll, color="blue", linewidth=1.0, label="Roll rate (gyro_x)")
    pry_ax.plot(times, yaw, color="gold", linewidth=1.0, label="Yaw rate (gyro_z)")
    pry_ax.set_ylabel("Gyro (device units)")
    pry_ax.set_xlabel('Time (seconds)')
    pry_ax.legend(loc="upper right", fontsize=8)
    pry_ax.grid(True, alpha=0.25)
    pry_ax.set_title("Pitch, Roll, Yaw Rates")
    pry_ax.tick_params(axis='y', labelsize=9)
    summary = cast(dict[str, object], result['summary'])
    ax.set_title(f"{os.path.basename(str(summary['source_path']))} IMU timeline")
    ax.set_ylabel('Normalized metric value' if normalize else 'Raw metric value')
    ax.grid(True, alpha=0.25)

    # --- Store normalization parameters for g_force for marker alignment ---
    g_force_norm_params = None
    g_force_raw = [_sample_signal(sample, "g_force") for sample in samples]
    g_force_smoothed = _rolling_mean(g_force_raw, smooth_n) if smooth_n > 1 else g_force_raw
    g_force_smoothed = g_force_smoothed[::stride]
    if "g_force" in metrics and normalize:
        # Use the same normalization as the plot for g_force
        sorted_values = sorted(g_force_smoothed)
        low = _percentile_sorted(sorted_values, 1)
        high = _percentile_sorted(sorted_values, 99)
        if high <= low:
            low = sorted_values[0]
            high = sorted_values[-1]
        padding = 0.05 * (high - low)
        span_min = low - padding
        span_max = high + padding
        g_force_norm_params = (span_min, span_max)
    else:
        g_force_norm_params = None

    g_force_plot_times: Optional[list[float]] = None
    g_force_plot_values: Optional[list[float]] = None
    g_force_plot_raw_values: Optional[list[float]] = None
    for metric in metrics:
        values = [_sample_signal(sample, metric) for sample in samples]
        if smooth_n > 1:
            values = _rolling_mean(values, smooth_n)
        values = values[::stride]
        if normalize:
            plotted, scale_label = _normalize_for_plot(values)
            label = f'{metric} ({scale_label})'
        else:
            plotted = values
            label = metric
        ax.plot(
            times,
            plotted,
            color=PLOT_COLORS.get(metric),
            linewidth=1.0,
            label=label,
        )
        if metric == "g_force":
            g_force_plot_times = times
            g_force_plot_values = plotted
            g_force_plot_raw_values = values

    def _g_force_plot_y(raw_g_force: float) -> float:
        if normalize and g_force_norm_params is not None:
            span_min, span_max = g_force_norm_params
            span = span_max - span_min
            if span > 0:
                return (raw_g_force - span_min) / span
        return raw_g_force

    def _g_force_anchor(
        target_time: float,
        fallback_g_force: float,
        *,
        prefer_peak: bool,
    ) -> tuple[float, float, float]:
        if (
            g_force_plot_times is None
            or g_force_plot_values is None
            or g_force_plot_raw_values is None
        ):
            return target_time, _g_force_plot_y(fallback_g_force), fallback_g_force
        search_radius = DEFAULT_LOAD_PEAK_SPACING_SECONDS
        candidates = [
            index for index, time in enumerate(g_force_plot_times)
            if abs(time - target_time) <= search_radius
        ]
        if not candidates:
            candidates = [min(
                range(len(g_force_plot_times)),
                key=lambda index: abs(g_force_plot_times[index] - target_time),
            )]
        chooser = max if prefer_peak else min
        anchor_index = chooser(candidates, key=lambda index: g_force_plot_values[index])
        return (
            g_force_plot_times[anchor_index],
            g_force_plot_values[anchor_index],
            g_force_plot_raw_values[anchor_index],
        )

    def _pad_y_axis(plot_ax: Any, fraction: float) -> None:
        bottom, top = plot_ax.get_ylim()
        span = top - bottom
        if span <= 0:
            span = max(abs(top), 1.0)
        padding = span * fraction
        plot_ax.set_ylim(bottom - padding, top + padding)

    event_kinds = [
        kind for kind in EVENT_ORDER
        if any(str(event['kind']) == kind for event in result['events'])
    ]
    extra_kinds = sorted({
        str(event['kind'])
        for event in result['events']
        if str(event['kind']) not in event_kinds
    })
    event_kinds.extend(extra_kinds)
    lane_for_kind = {kind: index for index, kind in enumerate(reversed(event_kinds))}
    key_rows: list[tuple[int, dict[str, object], str]] = []
    event_boxes: list[dict[str, Any]] = []
    for index, event in enumerate(result['events'], start=1):
        kind = str(event['kind'])
        color = EVENT_COLORS.get(kind, '#eeeeee')
        ax.axvspan(
            float(event['start_seconds']),
            float(event['end_seconds']),
            color=color,
            alpha=0.16,
            linewidth=0,
        )
        pry_ax.axvspan(
            float(event['start_seconds']),
            float(event['end_seconds']),
            color=color,
            alpha=0.10,
            linewidth=0,
        )
        event_ax.axvspan(
            float(event['start_seconds']),
            float(event['end_seconds']),
            color=color,
            alpha=0.08,
            linewidth=0,
        )
        y = lane_for_kind.get(kind, 0)
        start = float(event['start_seconds'])
        duration = float(event['duration_seconds'])
        rect = Rectangle(
            (start, y - 0.36),
            duration,
            0.66,
            facecolor=color,
            edgecolor='#111111',
            linewidth=0.6,
            alpha=0.92,
        )
        event_ax.add_patch(rect)
        full_label = f"{index}. {EVENT_LABELS.get(kind, _short_event_label(kind))}"
        text = event_ax.text(
            start + duration / 2,
            y,
            full_label,
            ha='center',
            va='center',
            fontsize=8.0,
            color='white',
            weight='bold',
            clip_on=True,
            visible=False,
        )
        event_boxes.append({
            'rect': rect,
            'text': text,
            'full_label': full_label,
            'number_label': str(index),
            'label_mode': 'hidden',
            'tooltip': _event_bar_label(index, event),
            'center_x': start + duration / 2,
            'center_y': y,
            'font_size': 8.0,
            'small_font_size': 6.8,
            'padding_px': 9.0,
            'number_padding_px': 4.0,
        })

        load_pulses = event.get('load_pulses')
        if isinstance(load_pulses, dict):
            peaks = load_pulses.get('peaks')
            if isinstance(peaks, list):
                for pulse in peaks:
                    if not isinstance(pulse, dict):
                        continue
                    peak_time = float(pulse.get('seconds_raw', pulse.get('seconds', 0.0)))
                    peak_g = float(pulse.get('g_force_raw', pulse.get('g_force', 0.0)))
                    valley_time = float(pulse.get('valley_seconds_raw', pulse.get('valley_seconds', peak_time)))
                    valley_g = float(pulse.get('valley_g_force_raw', pulse.get('valley_g_force', peak_g)))

                    peak_x, peak_y, peak_label_g = _g_force_anchor(
                        peak_time,
                        peak_g,
                        prefer_peak=True,
                    )
                    valley_x, valley_y, valley_label_g = _g_force_anchor(
                        valley_time,
                        valley_g,
                        prefer_peak=False,
                    )

                    ax.annotate(
                        f"{peak_label_g:.2f}g",
                        xy=(peak_x, peak_y),
                        xytext=(0, 14),
                        textcoords='offset points',
                        ha='center',
                        va='bottom',
                        fontsize=7.3,
                        color='#111111',
                        arrowprops={
                            'arrowstyle': '-',
                            'color': '#111111',
                            'linewidth': 0.8,
                            'shrinkA': 1.5,
                            'shrinkB': 0,
                        },
                        bbox={'boxstyle': 'round,pad=0.18', 'fc': '#ffffff', 'ec': 'none', 'alpha': 0.85},
                        clip_on=False,
                    )

                    ax.annotate(
                        f"{valley_label_g:.2f}g",
                        xy=(valley_x, valley_y),
                        xytext=(0, -14),
                        textcoords='offset points',
                        ha='center',
                        va='top',
                        fontsize=7.0,
                        color='#111111',
                        arrowprops={
                            'arrowstyle': '-',
                            'color': '#111111',
                            'linewidth': 0.8,
                            'shrinkA': 1.5,
                            'shrinkB': 0,
                        },
                        bbox={'boxstyle': 'round,pad=0.18', 'fc': '#ffffff', 'ec': 'none', 'alpha': 0.8},
                        clip_on=False,
                    )
        key_rows.append((index, event, color))

    _pad_y_axis(ax, 0.24)
    _pad_y_axis(pry_ax, 0.18)

    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles, labels, loc='upper right', fontsize=8)
    event_ax.set_yticks(list(lane_for_kind.values()))
    event_ax.set_yticklabels([
        EVENT_LABELS.get(kind, kind.replace('_candidate', '').replace('_', ' '))
        for kind in reversed(event_kinds)
    ])
    event_ax.set_ylabel('Candidates')
    event_ax.grid(True, axis='x', alpha=0.25)
    event_ax.set_ylim(-0.75, max(lane_for_kind.values(), default=0) + 0.75)
    event_ax.tick_params(axis='y', labelsize=9)
    if event_boxes:
        hover_annotation = event_ax.annotate(
            '',
            xy=(0.0, 0.0),
            xycoords='data',
            xytext=(0, 12),
            textcoords='offset points',
            ha='center',
            va='bottom',
            fontsize=8,
            color='#111111',
            bbox={'boxstyle': 'round,pad=0.3', 'fc': '#fdfdfd', 'ec': '#333333', 'linewidth': 0.5},
            visible=False,
        )
        hover_state: dict[str, Optional[dict[str, Any]]] = {'active': None}

        def _update_event_box_labels(*_args: object) -> None:
            canvas_any = cast(Any, event_ax.figure.canvas)
            renderer = canvas_any.get_renderer() if hasattr(canvas_any, 'get_renderer') else None
            if renderer is None:
                event_ax.figure.canvas.draw()
                renderer = canvas_any.get_renderer() if hasattr(canvas_any, 'get_renderer') else None
            if renderer is None:
                return
            changed = False
            for box in event_boxes:
                rect = cast(Rectangle, box['rect'])
                text_artist = box['text']
                left = rect.get_x()
                width = rect.get_width()
                x0, _ = event_ax.transData.transform((left, 0.0))
                x1, _ = event_ax.transData.transform((left + width, 0.0))
                pixel_width = abs(x1 - x0)
                previous_visible = text_artist.get_visible()
                previous_text = text_artist.get_text()
                previous_fontsize = text_artist.get_fontsize()
                full_font_size = float(box.get('font_size', 8.0))
                small_font_size = float(box.get('small_font_size', 6.8))
                text_artist.set_visible(True)
                text_artist.set_fontsize(full_font_size)
                text_artist.set_text(str(box['full_label']))
                bbox = text_artist.get_window_extent(renderer=renderer)
                full_text_width = bbox.width + float(box.get('padding_px', 0.0))
                text_artist.set_fontsize(small_font_size)
                bbox = text_artist.get_window_extent(renderer=renderer)
                small_full_text_width = bbox.width + float(box.get('padding_px', 0.0))
                text_artist.set_fontsize(full_font_size)
                text_artist.set_text(str(box['number_label']))
                bbox = text_artist.get_window_extent(renderer=renderer)
                number_text_width = bbox.width + float(box.get('number_padding_px', 0.0))
                if width <= 0:
                    next_text = str(box['full_label'])
                    next_font_size = full_font_size
                    next_visible = False
                    label_mode = 'hidden'
                elif pixel_width >= full_text_width:
                    next_text = str(box['full_label'])
                    next_font_size = full_font_size
                    next_visible = True
                    label_mode = 'full'
                elif pixel_width >= small_full_text_width:
                    next_text = str(box['full_label'])
                    next_font_size = small_font_size
                    next_visible = True
                    label_mode = 'full-small'
                elif pixel_width >= number_text_width:
                    next_text = str(box['number_label'])
                    next_font_size = full_font_size
                    next_visible = True
                    label_mode = 'number'
                else:
                    next_text = str(box['number_label'])
                    next_font_size = full_font_size
                    next_visible = False
                    label_mode = 'hidden'
                text_artist.set_text(next_text)
                text_artist.set_fontsize(next_font_size)
                text_artist.set_visible(next_visible)
                if (
                    next_visible != previous_visible
                    or next_text != previous_text
                    or next_font_size != previous_fontsize
                    or box.get('label_mode') != label_mode
                ):
                    box['label_mode'] = label_mode
                    changed = True
            if changed:
                event_ax.figure.canvas.draw_idle()

        def _maybe_hide_annotation(redraw: bool = True) -> None:
            if hover_state['active'] is not None:
                hover_annotation.set_visible(False)
                hover_state['active'] = None
                if redraw:
                    event_ax.figure.canvas.draw_idle()

        def _on_motion(event: object) -> None:
            if not isinstance(event, MouseEvent):
                return
            if event.inaxes is not event_ax:
                _maybe_hide_annotation()
                return
            for box in event_boxes:
                rect = cast(Rectangle, box['rect'])
                contains, _ = rect.contains(event)
                if contains:
                    if box.get('label_mode') == 'full':
                        _maybe_hide_annotation()
                        return
                    hover_annotation.xy = (box['center_x'], box['center_y'])
                    hover_annotation.set_text(str(box['tooltip']))
                    if not hover_annotation.get_visible() or hover_state['active'] is not box:
                        hover_annotation.set_visible(True)
                        hover_state['active'] = box
                        event_ax.figure.canvas.draw_idle()
                    return
            _maybe_hide_annotation()

        event_ax.callbacks.connect('xlim_changed', lambda _ax: _update_event_box_labels())
        event_ax.figure.canvas.mpl_connect('resize_event', lambda _evt: _update_event_box_labels())
        event_ax.figure.canvas.mpl_connect('motion_notify_event', _on_motion)
        _update_event_box_labels()
    _draw_candidate_key(key_ax, key_rows)

    if output:
        fig.savefig(output, dpi=160)
        # Save to persistent cache if enabled
        if not plot_cache_bypass and 'cache_file' in locals():
            import shutil
            shutil.copyfile(output, cache_file)
    if show:
        plt.show()
    else:
        plt.close(fig)


def _threshold_segments(
    kind: str,
    times: list[float],
    values: list[float],
    threshold: float,
    min_duration_seconds: float,
    merge_gap_seconds: float,
) -> list[Segment]:
    active: list[tuple[int, int]] = []
    start_index: Optional[int] = None
    for index, value in enumerate(values):
        if value >= threshold and start_index is None:
            start_index = index
        elif value < threshold and start_index is not None:
            if times[index] - times[start_index] >= min_duration_seconds:
                active.append((start_index, index))
            start_index = None
    if start_index is not None:
        end_index = len(values) - 1
        if times[end_index] - times[start_index] >= min_duration_seconds:
            active.append((start_index, end_index))
    return _merge_segments(kind, times, active, merge_gap_seconds)


def _combined_segments(
    kind: str,
    times: list[float],
    left: list[float],
    left_threshold: float,
    right: list[float],
    right_threshold: float,
    min_duration_seconds: float,
    merge_gap_seconds: float,
) -> list[Segment]:
    values = [
        1.0 if left_value >= left_threshold and right_value >= right_threshold else 0.0
        for left_value, right_value in zip(left, right)
    ]
    return _threshold_segments(
        kind=kind,
        times=times,
        values=values,
        threshold=1.0,
        min_duration_seconds=min_duration_seconds,
        merge_gap_seconds=merge_gap_seconds,
    )


def _single_axis_spin_segments(
    samples: list[MotionSample],
    times: list[float],
    gyro_smooth: list[float],
    rotation_threshold: float,
    dominance_threshold: float,
    min_duration_seconds: float,
    merge_gap_seconds: float,
    sample_rate_hz: float,
) -> list[Segment]:
    smooth_n = max(1, round(sample_rate_hz * DEFAULT_SMOOTH_SECONDS))
    ax = _rolling_mean([abs(sample.gyro_x) for sample in samples], smooth_n)
    ay = _rolling_mean([abs(sample.gyro_y) for sample in samples], smooth_n)
    az = _rolling_mean([abs(sample.gyro_z) for sample in samples], smooth_n)
    values = []
    for gx, gy, gz, norm in zip(ax, ay, az, gyro_smooth):
        dominant = max(gx, gy, gz)
        dominance = dominant / (gx + gy + gz) if gx + gy + gz > 0 else 0.0
        values.append(1.0 if norm >= rotation_threshold and dominance >= dominance_threshold else 0.0)
    return _threshold_segments(
        kind='single_axis_spin_candidate',
        times=times,
        values=values,
        threshold=1.0,
        min_duration_seconds=min_duration_seconds,
        merge_gap_seconds=merge_gap_seconds,
    )


def _infinite_tumble_segments(
    times: list[float],
    rotation_segments: list[Segment],
    load_segments: list[Segment],
    load_peak_indices: list[int],
    min_load_pulses: int,
    context_seconds: float,
    merge_gap_seconds: float,
) -> list[Segment]:
    """Find rotation regions with repeated bottom-of-rotation load pulses."""
    candidates: list[tuple[int, int]] = []
    for rotation in rotation_segments:
        start_time = rotation.start - context_seconds
        end_time = rotation.end + context_seconds
        related_load_segments = [
            load for load in load_segments
            if load.end >= start_time and load.start <= end_time
        ]
        if not related_load_segments:
            continue

        sample_start = min([rotation.sample_start] + [load.sample_start for load in related_load_segments])
        sample_end = max([rotation.sample_end] + [load.sample_end for load in related_load_segments])
        peak_count = sum(
            1 for index in load_peak_indices
            if sample_start <= index <= sample_end
        )
        if peak_count < min_load_pulses:
            continue
        candidates.append((sample_start, sample_end))

    return _merge_segments(
        kind='infinite_tumble_candidate',
        times=times,
        spans=candidates,
        merge_gap_seconds=merge_gap_seconds,
    )


def _merge_segments(
    kind: str,
    times: list[float],
    spans: Iterable[tuple[int, int]],
    merge_gap_seconds: float,
) -> list[Segment]:
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and times[start] - times[merged[-1][1]] <= merge_gap_seconds:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return [
        Segment(
            kind=kind,
            start=times[start],
            end=times[end],
            sample_start=start,
            sample_end=end,
        )
        for start, end in merged
    ]


def _enrich_segment(
    segment: Segment,
    samples: list[MotionSample],
    load_peak_indices: Optional[list[int]] = None,
) -> dict[str, object]:
    window = samples[segment.sample_start:segment.sample_end + 1]
    axes = _dominant_gyro_axis(window)
    event: dict[str, object] = {
        'kind': segment.kind,
        'start_seconds': _round(segment.start),
        'end_seconds': _round(segment.end),
        'duration_seconds': _round(segment.duration),
        'sample_start': segment.sample_start,
        'sample_end': segment.sample_end,
        'sample_count': len(window),
        'g_force': _stats([sample.g_force for sample in window]),
        'gyro_norm': _stats([sample.gyro_norm for sample in window]),
        'jerk_g_s': _stats([sample.jerk_g_s for sample in window[1:]]),
        'dominant_gyro_axis': axes['axis'],
        'gyro_axis_dominance': axes['dominance'],
        'gyro_axis_mean_abs': axes['mean_abs'],
        'damping': estimate_damping(window),
    }
    pulse_indices = [
        int(index) for index in (load_peak_indices or [])
        if segment.sample_start <= index <= segment.sample_end
    ]
    if pulse_indices:
        pulse_records = _load_pulse_records(
            samples=samples,
            pulse_indices=pulse_indices,
            sample_start=segment.sample_start,
            sample_end=segment.sample_end,
        )
        from typing import cast
        pulse_times = [float(cast(float, record['seconds'])) for record in pulse_records]
        pulse_g = [float(cast(float, record['g_force'])) for record in pulse_records]
        event['load_pulses'] = {
            'count': len(pulse_records),
            'peaks': pulse_records,
            'peak_seconds': [_round(time) for time in pulse_times],
            'peak_g': [_round(value) for value in pulse_g],
            'max_peak_g': _round(max(pulse_g) if pulse_g else 0.0),
            'median_period_seconds': (
                _round(_median_delta(pulse_times)) if len(pulse_times) > 1 and _median_delta(pulse_times) is not None else None
            ),
            'interpretation': (
                'candidate bottom-of-rotation load pulses; in infinite '
                'tumbling these are expected near the pilot passing under '
                'the glider'
            ),
        }
    if segment.kind == 'high_g_load_pulse_candidate':
        event['phase_role'] = 'load_phase_not_maneuver'
        event['phase_note'] = (
            'High G is treated as a force/load phase that can occur inside '
            'infinite tumbling or other maneuvers, not as a maneuver label by itself.'
        )
    elif segment.kind == 'infinite_tumble_candidate':
        event['phase_role'] = 'rotation_with_repeated_load_pulses'
        event['phase_note'] = (
            'Candidate infinite tumbling region: sustained rotation with repeated '
            'high-G load pulses, matching the pilot-under-glider bottom phase.'
        )
    return event


def _window_features(window: list[MotionSample]) -> dict[str, object]:
    row: dict[str, object] = {}
    series = {
        'accel_x': [sample.accel_x for sample in window],
        'accel_y': [sample.accel_y for sample in window],
        'accel_z': [sample.accel_z for sample in window],
        'gyro_x': [sample.gyro_x for sample in window],
        'gyro_y': [sample.gyro_y for sample in window],
        'gyro_z': [sample.gyro_z for sample in window],
        'g_force': [sample.g_force for sample in window],
        'gyro_norm': [sample.gyro_norm for sample in window],
        'jerk_g_s': [sample.jerk_g_s for sample in window[1:]],
    }
    for name, values in series.items():
        stats = _stats(values)
        for stat_name in ('mean', 'std', 'min', 'max', 'median', 'p95'):
            row[f'{name}_{stat_name}'] = stats[stat_name]

    for prefix, axes in (
        ('accel', ('accel_x', 'accel_y', 'accel_z')),
        ('gyro', ('gyro_x', 'gyro_y', 'gyro_z')),
    ):
        means = [abs(float(row[f'{axis}_mean'])) for axis in axes]
        total = sum(means)
        dominant_value = max(means) if means else 0.0
        dominant_index = means.index(dominant_value) if means else 0
        row[f'{prefix}_dominant_axis'] = axes[dominant_index][-1]
        row[f'{prefix}_axis_dominance'] = _round(dominant_value / total) if total else 0.0

    row['gyro_sign_changes_x'] = _sign_changes([sample.gyro_x for sample in window])
    row['gyro_sign_changes_y'] = _sign_changes([sample.gyro_y for sample in window])
    row['gyro_sign_changes_z'] = _sign_changes([sample.gyro_z for sample in window])
    return row


def _dominant_gyro_axis(window: list[MotionSample]) -> dict[str, object]:
    if not window:
        return {'axis': '', 'dominance': 0.0, 'mean_abs': 0.0}
    means: dict[str, float] = {
        'x': statistics.fmean(abs(sample.gyro_x) for sample in window),
        'y': statistics.fmean(abs(sample.gyro_y) for sample in window),
        'z': statistics.fmean(abs(sample.gyro_z) for sample in window),
    }
    total = sum(means.values())
    axis = max(means, key=lambda k: means[k])
    return {
        'axis': axis,
        'dominance': _round(means[axis] / total) if total else 0.0,
        'mean_abs': _round(means[axis]),
    }


def _sample_signal(sample: MotionSample, signal_name: str) -> float:
    try:
        return float(getattr(sample, signal_name))
    except AttributeError as exc:
        raise ValueError(f'unknown signal {signal_name!r}') from exc


def _normalize_for_plot(values: list[float]) -> tuple[list[float], str]:
    if not values:
        return [], 'empty'
    sorted_values = sorted(values)
    low = _percentile_sorted(sorted_values, 1)
    high = _percentile_sorted(sorted_values, 99)
    if high <= low:
        low = sorted_values[0]
        high = sorted_values[-1]
    if high <= low:
        return [0.5 for _ in values], f'constant {low:.3g}'
    padding = 0.05 * (high - low)
    span_min = low - padding
    span_max = high + padding
    if span_max <= span_min:
        return [0.5 for _ in values], f'constant {low:.3g}'
    span = span_max - span_min
    normalized = [
        (value - span_min) / span
        for value in values
    ]
    return normalized, f'raw p1={low:.3g}, p99={high:.3g}, pad={padding:.3g}'


def _short_event_label(kind: str) -> str:
    labels = {
        'high_g_load_pulse_candidate': 'load',
        'fast_rotation_candidate': 'rotation',
        'loaded_rotation_phase_candidate': 'loaded rot',
        'infinite_tumble_candidate': 'infinite',
        'single_axis_spin_candidate': 'spin',
    }
    return labels.get(kind, kind.replace('_candidate', '').replace('_', ' '))


def _event_bar_label(index: int, event: dict[str, object]) -> str:
    kind = str(event['kind'])
    label = EVENT_LABELS.get(kind, _short_event_label(kind))
    start = float(event['start_seconds'])
    end = float(event['end_seconds'])
    pulses = ''
    load_pulses = event.get('load_pulses')
    if isinstance(load_pulses, dict) and load_pulses.get('count'):
        count = int(load_pulses['count'])
        pulses = f" · {count} {'pulse' if count == 1 else 'pulses'}"
    return f'{index}. {label} {start:.1f}-{end:.1f}s{pulses}'


def _draw_candidate_key(key_ax, rows: list[tuple[int, dict[str, object], str]]) -> None:
    from matplotlib.patches import Rectangle

    # Table column widths (fraction of axes width)
    swatch_height = 0.027
    key_font_size = 7.8
    axes_box = key_ax.get_position()
    fig_width, fig_height = key_ax.figure.get_size_inches()
    axes_width = max(axes_box.width * fig_width, 1e-9)
    axes_height = max(axes_box.height * fig_height, 1e-9)
    swatch_width = swatch_height * axes_height / axes_width
    col_x = [0.0, swatch_width + 0.016, 0.64, 0.87]
    key_ax.text(
        0.0,
        1.0,
        'Candidate Key',
        transform=key_ax.transAxes,
        ha='left',
        va='top',
        fontsize=12,
        weight='bold',
    )
    # Table header
    y = 0.97
    row_gap = 0.052
    key_ax.text(col_x[0], y, "#", transform=key_ax.transAxes, ha='left', va='top', fontsize=key_font_size, fontfamily="monospace", weight="bold")
    key_ax.text(col_x[1], y, "Type", transform=key_ax.transAxes, ha='left', va='top', fontsize=key_font_size, fontfamily="monospace", weight="bold")
    key_ax.text(col_x[2], y, "Time (s)", transform=key_ax.transAxes, ha='left', va='top', fontsize=key_font_size, fontfamily="monospace", weight="bold")
    key_ax.text(col_x[3], y, "Pulses", transform=key_ax.transAxes, ha='left', va='top', fontsize=key_font_size, fontfamily="monospace", weight="bold")
    y -= row_gap * 0.9

    for index, event, color in rows:
        if y < 0.02:
            key_ax.text(
                0.0,
                y,
                f'... {len(rows) - index + 1} more',
                transform=key_ax.transAxes,
                ha='left',
                va='top',
                fontsize=8,
                color='#444444',
            )
            break
        key_ax.add_patch(
            Rectangle(
                (col_x[0], y - swatch_height),
                swatch_width,
                swatch_height,
                transform=key_ax.transAxes,
                facecolor=color,
                edgecolor='#111111',
                linewidth=0.5,
            )
        )
        kind = event.get('kind', '')
        weight = 'bold' if kind == 'infinite_tumble_candidate' else 'normal'
        # Table columns: index, type, time, pulses
        label = EVENT_LABELS.get(kind, kind.replace('_candidate', '').replace('_', ' '))
        t_start = event.get('start_seconds', "")
        t_end = event.get('end_seconds', "")
        time_str = f"{t_start:.1f}-{t_end:.1f}" if isinstance(t_start, (float, int)) and isinstance(t_end, (float, int)) else ""
        pulses = ""
        load_pulses = event.get('load_pulses')
        if isinstance(load_pulses, dict) and load_pulses.get('count'):
            pulses = str(load_pulses['count'])
        key_ax.text(col_x[0] + swatch_width / 2, y - swatch_height / 2, str(index), transform=key_ax.transAxes, ha='center', va='center', fontsize=7.2, fontfamily="monospace", weight='bold', color='#ffffff')
        key_ax.text(col_x[1], y, label, transform=key_ax.transAxes, ha='left', va='top', fontsize=key_font_size, fontfamily="monospace", weight=weight, color='#202020')
        key_ax.text(col_x[2], y, time_str, transform=key_ax.transAxes, ha='left', va='top', fontsize=key_font_size, fontfamily="monospace", color='#202020')
        key_ax.text(col_x[3], y, pulses, transform=key_ax.transAxes, ha='left', va='top', fontsize=key_font_size, fontfamily="monospace", color='#202020')
        y -= row_gap


def _format_load_pulse_count(event: dict[str, object]) -> str:
    load_pulses = event.get('load_pulses')
    if not isinstance(load_pulses, dict):
        try:
            load_pulses = cast(dict, load_pulses)
        except Exception:
            return ''
    count = load_pulses.get('count') if isinstance(load_pulses, dict) else None
    if not count:
        return ''
    return f' load_pulses={count}'


def _load_pulse_records(
    samples: list[MotionSample],
    pulse_indices: list[int],
    sample_start: int,
    sample_end: int,
) -> list[dict[str, float]]:
    times = [sample.time for sample in samples]
    sample_rate_hz = _sample_rate_from_times(times)
    half_window = max(1, round(sample_rate_hz * DEFAULT_LOAD_PEAK_SPACING_SECONDS))
    records: list[dict[str, float]] = []
    used_peak_indices: set[int] = set()
    for index in pulse_indices:
        start = max(sample_start, index - half_window)
        end = min(sample_end, index + half_window)
        peak_index = max(range(start, end + 1), key=lambda idx: samples[idx].g_force)
        if peak_index in used_peak_indices:
            continue
        used_peak_indices.add(peak_index)
        valley_index = min(range(start, end + 1), key=lambda idx: samples[idx].g_force)
        records.append({
            'seconds_raw': float(samples[peak_index].time),
            'seconds': _round(samples[peak_index].time),
            'g_force_raw': float(samples[peak_index].g_force),
            'g_force': _round(samples[peak_index].g_force),
            'valley_seconds_raw': float(samples[valley_index].time),
            'valley_seconds': _round(samples[valley_index].time),
            'valley_g_force_raw': float(samples[valley_index].g_force),
            'valley_g_force': _round(samples[valley_index].g_force),
        })
    records.sort(key=lambda record: record['seconds_raw'])
    merged: list[dict[str, float]] = []
    for record in records:
        if merged and record['seconds'] - merged[-1]['seconds'] < DEFAULT_LOAD_PEAK_SPACING_SECONDS:
            if record['g_force_raw'] > merged[-1]['g_force_raw']:
                merged[-1] = record
        else:
            merged.append(record)
    return merged


def _find_peaks(values: list[float], min_distance: int, min_value: float) -> list[int]:
    candidates = [
        index for index in range(1, len(values) - 1)
        if values[index] >= min_value
        and values[index] >= values[index - 1]
        and values[index] > values[index + 1]
    ]
    if not candidates:
        return []

    selected: list[int] = []
    for index in sorted(candidates, key=lambda idx: values[idx], reverse=True):
        if all(abs(index - chosen) >= min_distance for chosen in selected):
            selected.append(index)
    selected.sort()
    return selected


def _fit_exponential_decay(times: list[float], amplitudes: list[float]) -> dict[str, object]:
    base_time = times[0]
    xs = [time - base_time for time in times]
    ys = [math.log(max(amplitude, 1e-12)) for amplitude in amplitudes]
    n = len(xs)
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    slope = 0.0 if denominator == 0 else sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    predicted = [intercept + slope * x for x in xs]
    ss_res = sum((y - y_hat) ** 2 for y, y_hat in zip(ys, predicted))
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r_squared = 1.0 if ss_tot == 0 else 1 - ss_res / ss_tot
    damping_coefficient = -slope
    half_life = math.log(2) / damping_coefficient if damping_coefficient > 0 else None
    period = _median_delta(times)
    angular_frequency = 2 * math.pi / period if period and period > 0 else None
    damping_ratio = None
    if angular_frequency and damping_coefficient > 0:
        damping_ratio = damping_coefficient / math.sqrt(angular_frequency ** 2 + damping_coefficient ** 2)
    return {
        'damping_coefficient_per_s': _round(damping_coefficient),
        'half_life_seconds': _round(half_life) if half_life is not None else None,
        'median_peak_period_seconds': _round(period) if period is not None else None,
        'damping_ratio_estimate': _round(damping_ratio) if damping_ratio is not None else None,
        'r_squared': _round(r_squared),
    }


def _rolling_mean(values: list[float], window_size: int) -> list[float]:
    if window_size <= 1:
        return list(values)
    result: list[float] = []
    total = 0.0
    queue: list[float] = []
    for value in values:
        queue.append(value)
        total += value
        if len(queue) > window_size:
            total -= queue.pop(0)
        result.append(total / len(queue))
    return result


def _centered_rolling_mean(values: list[float], window_size: int) -> list[float]:
    if window_size <= 1 or not values:
        return list(values)
    radius = max(0, window_size // 2)
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
    result: list[float] = []
    for index in range(len(values)):
        start = max(0, index - radius)
        end = min(len(values), index + radius + 1)
        result.append((prefix[end] - prefix[start]) / (end - start))
    return result


def _stats(values: Iterable[float]) -> dict[str, float]:
    vals = [float(value) for value in values]
    if not vals:
        return {
            'count': 0,
            'mean': 0.0,
            'std': 0.0,
            'min': 0.0,
            'max': 0.0,
            'median': 0.0,
            'p95': 0.0,
            'p99': 0.0,
        }
    vals_sorted = sorted(vals)
    mean = statistics.fmean(vals)
    std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    return {
        'count': len(vals),
        'mean': _round(mean),
        'std': _round(std),
        'min': _round(vals_sorted[0]),
        'max': _round(vals_sorted[-1]),
        'median': _round(_percentile_sorted(vals_sorted, 50)),
        'p95': _round(_percentile_sorted(vals_sorted, 95)),
        'p99': _round(_percentile_sorted(vals_sorted, 99)),
    }


def _percentile_sorted(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * percentile / 100
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return values[int(position)]
    fraction = position - low
    return values[low] * (1 - fraction) + values[high] * fraction


def _sign_changes(values: list[float]) -> int:
    changes = 0
    previous = 0
    for value in values:
        sign = 1 if value > 0 else -1 if value < 0 else 0
        if sign and previous and sign != previous:
            changes += 1
        if sign:
            previous = sign
    return changes


def _median_delta(values: list[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    return statistics.median(b - a for a, b in zip(values, values[1:]))


def _sample_rate_from_times(times: list[float]) -> float:
    duration = times[-1] - times[0] if len(times) > 1 else 0
    return (len(times) - 1) / duration if duration > 0 else 0.0


def _norm3(x: float, y: float, z: float) -> float:
    return math.sqrt(x * x + y * y + z * z)


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Analyze INSV accelerometer/gyro data for acro maneuver candidates.',
    )
    parser.add_argument(
        'inputs',
        nargs='*',
        default=[DEFAULT_INPUT_PATH],
        help=f'Input .insv file(s), default: {DEFAULT_INPUT_PATH}',
    )
    parser.add_argument('--gui', action='store_true', help='Open a simple file/plot launcher GUI')
    parser.add_argument('--events', help='Write event summary JSON to this path')
    parser.add_argument('--features', help='Write sliding-window feature CSV to this path')
    parser.add_argument('--summary', action='store_true', help='Print a human summary')
    parser.add_argument(
        '--summary-output',
        default=DEFAULT_SUMMARY_OUTPUT,
        help=f'Write the human summary to this text file, default: {DEFAULT_SUMMARY_OUTPUT}',
    )
    parser.add_argument('--plot', action='store_true', default=True, help='Open an interactive matplotlib timeline plot')
    parser.add_argument('--no-plot', action='store_false', dest='plot', help='Do not open an interactive plot')
    parser.add_argument('--plot-output', help='Save the timeline plot image to this path')
    parser.add_argument(
        '--plot-metrics',
        nargs='+',
        choices=PLOT_METRIC_CHOICES,
        default=list(DEFAULT_PLOT_METRICS),
        help='Metrics to plot on the shared timeline',
    )
    parser.add_argument(
        '--plot-raw',
        action='store_true',
        default=True,
        help='Plot raw metric values instead of robust normalized values',
    )
    parser.add_argument(
        '--plot-normalized',
        action='store_false',
        dest='plot_raw',
        help='Plot robust-normalized metric values instead of raw values',
    )
    parser.add_argument(
        '--plot-max-points',
        type=int,
        default=DEFAULT_PLOT_MAX_POINTS,
        help='Maximum samples per plotted line before downsampling',
    )
    parser.add_argument(
        '--plot-smooth-seconds',
        type=float,
        default=0.10,
        help='Rolling mean smoothing window used only for plotted metric lines',
    )
    parser.add_argument('--smooth-seconds', type=float, default=DEFAULT_SMOOTH_SECONDS)
    parser.add_argument('--min-duration-seconds', type=float, default=DEFAULT_MIN_DURATION_SECONDS)
    parser.add_argument('--merge-gap-seconds', type=float, default=DEFAULT_MERGE_GAP_SECONDS)
    parser.add_argument('--high-g-threshold', type=float, default=DEFAULT_HIGH_G_THRESHOLD)
    parser.add_argument('--rotation-threshold', type=float, default=DEFAULT_ROTATION_THRESHOLD)
    parser.add_argument('--single-axis-dominance', type=float, default=DEFAULT_SINGLE_AXIS_DOMINANCE)
    parser.add_argument(
        '--load-peak-spacing',
        type=float,
        default=DEFAULT_LOAD_PEAK_SPACING_SECONDS,
        help='Minimum spacing between high-G load peaks used as bottom/load pulse candidates',
    )
    parser.add_argument(
        '--infinite-tumble-min-load-pulses',
        type=int,
        default=DEFAULT_INFINITE_TUMBLE_MIN_LOAD_PULSES,
        help='Minimum load pulses inside a rotation region before flagging infinite tumbling',
    )
    parser.add_argument(
        '--infinite-tumble-context-seconds',
        type=float,
        default=DEFAULT_INFINITE_TUMBLE_CONTEXT_SECONDS,
        help='Seconds around rotation intervals used to associate high-G load pulses',
    )
    parser.add_argument('--window-seconds', type=float, default=DEFAULT_WINDOW_SECONDS)
    parser.add_argument('--step-seconds', type=float, default=DEFAULT_STEP_SECONDS)
    parser.add_argument(
        '--damping-signal',
        default='g_force',
        choices=['g_force', 'gyro_norm', 'accel_x', 'accel_y', 'accel_z', 'gyro_x', 'gyro_y', 'gyro_z'],
    )
    parser.add_argument('--damping-peak-spacing', type=float, default=0.35)
    parser.add_argument('--damping-min-amplitude', type=float, default=0.08)
    parser.add_argument(
        '--gyro-stabilize',
        action='store_true',
        help='Apply optional gyro stabilization before summaries, detection, features, and plots',
    )
    parser.add_argument(
        '--gyro-stabilizer-method',
        choices=['rolling-mean', 'mean'],
        default='rolling-mean',
        help='Gyro stabilization baseline method',
    )
    parser.add_argument(
        '--gyro-stabilizer-window-seconds',
        type=float,
        default=2.0,
        help='Rolling baseline window for --gyro-stabilize',
    )
    parser.add_argument(
        '--gyro-stabilizer-strength',
        type=float,
        default=1.0,
        help='Fraction of estimated gyro baseline to remove, from 0 to 1',
    )
    parser.add_argument('--start-seconds', type=float, default=None, help='Start time (seconds) for analysis window')
    parser.add_argument('--end-seconds', type=float, default=None, help='End time (seconds) for analysis window')
    return parser.parse_args(argv)


def _format_launcher_stats(result: dict[str, object], samples: list[MotionSample]) -> str:
    """Return a compact stats report for the launcher GUI."""
    summary = cast(dict[str, object], result['summary'])
    source_path = str(summary['source_path'])
    g_force = cast(dict[str, float], summary['g_force'])
    gyro_norm = cast(dict[str, float], summary['gyro_norm'])
    jerk = cast(dict[str, float], summary['jerk_g_s'])
    accel_stats = {
        axis: _stats(_sample_signal(sample, axis) for sample in samples)
        for axis in ('accel_x', 'accel_y', 'accel_z')
    }
    gyro_stats = {
        axis: _stats(_sample_signal(sample, axis) for sample in samples)
        for axis in ('gyro_x', 'gyro_y', 'gyro_z')
    }

    def _peak_abs(axis_names: tuple[str, ...]) -> tuple[str, float, float]:
        peak_axis = axis_names[0]
        peak_value = 0.0
        peak_time = samples[0].time if samples else 0.0
        for sample in samples:
            for axis in axis_names:
                value = _sample_signal(sample, axis)
                if abs(value) > abs(peak_value):
                    peak_axis = axis
                    peak_value = value
                    peak_time = sample.time
        return peak_axis, peak_value, peak_time

    peak_accel_axis, peak_accel_value, peak_accel_time = _peak_abs(
        ('accel_x', 'accel_y', 'accel_z')
    )
    peak_gyro_axis, peak_gyro_value, peak_gyro_time = _peak_abs(
        ('gyro_x', 'gyro_y', 'gyro_z')
    )
    max_accel_m_s2 = g_force['max'] * STANDARD_GRAVITY_M_S2
    mean_accel_m_s2 = g_force['mean'] * STANDARD_GRAVITY_M_S2
    damping = cast(dict[str, object], summary.get('damping', {}))
    damping_fit = damping.get('fit')

    lines = [
        os.path.basename(source_path),
        f"Samples: {int(summary['sample_count']):,}",
        f"Window duration: {float(summary['duration_seconds']):.3f}s",
        f"Sample rate: {float(summary['sample_rate_hz']):.3f} Hz",
        "",
        "Acceleration magnitude",
        f"  max g-force: {g_force['max']:.3f} g ({max_accel_m_s2:.2f} m/s^2)",
        f"  mean g-force: {g_force['mean']:.3f} g ({mean_accel_m_s2:.2f} m/s^2)",
        f"  min/median/p95: {g_force['min']:.3f} / {g_force['median']:.3f} / {g_force['p95']:.3f} g",
        f"  max axis sample: {peak_accel_axis}={peak_accel_value:.3f} g at {peak_accel_time:.3f}s",
        "",
        "Rotation",
        f"  max gyro norm: {gyro_norm['max']:.3f}",
        f"  mean gyro norm: {gyro_norm['mean']:.3f}",
        f"  min/median/p95: {gyro_norm['min']:.3f} / {gyro_norm['median']:.3f} / {gyro_norm['p95']:.3f}",
        f"  max axis sample: {peak_gyro_axis}={peak_gyro_value:.3f} at {peak_gyro_time:.3f}s",
        "",
        "Jerk",
        f"  max jerk: {jerk['max']:.3f} g/s",
        f"  mean jerk: {jerk['mean']:.3f} g/s",
        "",
        "Axis averages and ranges",
    ]
    for axis in ('accel_x', 'accel_y', 'accel_z'):
        stats = accel_stats[axis]
        lines.append(
            f"  {axis}: mean={stats['mean']:.3f} min={stats['min']:.3f} max={stats['max']:.3f} g"
        )
    for axis in ('gyro_x', 'gyro_y', 'gyro_z'):
        stats = gyro_stats[axis]
        lines.append(
            f"  {axis}: mean={stats['mean']:.3f} min={stats['min']:.3f} max={stats['max']:.3f}"
        )

    lines.extend([
        "",
        f"Candidate events: {int(summary['candidate_event_count'])}",
        "Speed: unavailable; no GPS or velocity stream is currently parsed.",
    ])
    if isinstance(damping_fit, dict):
        half_life = damping_fit.get('half_life_seconds')
        damping_ratio = damping_fit.get('damping_ratio_estimate')
        damping_bits = [f"r2={float(damping_fit.get('r_squared', 0.0)):.3f}"]
        if half_life is not None:
            damping_bits.append(f"half_life={float(half_life):.3f}s")
        if damping_ratio is not None:
            damping_bits.append(f"ratio={float(damping_ratio):.3f}")
        lines.append("Damping fit: " + " ".join(damping_bits))
    return '\n'.join(lines)


def _run_launcher_gui() -> int:
    """Open a small Tk launcher that runs this script in a plotting subprocess."""
    try:
        import subprocess
        import tkinter as tk
        from tkinter import filedialog, messagebox, scrolledtext
    except ImportError as exc:
        print(f'error: launcher GUI requires tkinter: {exc}', file=sys.stderr)
        return 1

    root = tk.Tk()
    root.title('INSV Maneuver Analysis')
    root.resizable(False, False)

    file_var = tk.StringVar(value=DEFAULT_INPUT_PATH)
    summary_var = tk.StringVar(value=DEFAULT_SUMMARY_OUTPUT)
    start_var = tk.StringVar(value='')
    end_var = tk.StringVar(value='')
    max_points_var = tk.StringVar(value=str(DEFAULT_PLOT_MAX_POINTS))
    raw_var = tk.BooleanVar(value=True)
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    launcher_path = os.path.join(repo_root, 'insv_maneuver_analysis.py')
    script_path = launcher_path if os.path.exists(launcher_path) else os.path.abspath(__file__)

    def set_stats_text(text: str) -> None:
        stats_text.configure(state='normal')
        stats_text.delete('1.0', tk.END)
        stats_text.insert(tk.END, text)
        stats_text.configure(state='disabled')

    def choose_file() -> None:
        path = filedialog.askopenfilename(
            title='Open INSV file',
            filetypes=[('INSV files', '*.insv'), ('All files', '*')],
        )
        if path:
            file_var.set(path)

    def choose_summary() -> None:
        path = filedialog.asksaveasfilename(
            title='Save summary as',
            defaultextension='.txt',
            filetypes=[('Text files', '*.txt'), ('All files', '*')],
        )
        if path:
            summary_var.set(path)

    def parse_optional_float(value: str, label: str) -> Optional[float]:
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError as exc:
            raise ValueError(f'{label} must be a number') from exc

    def read_settings() -> tuple[str, int, Optional[float], Optional[float]]:
        input_path = file_var.get().strip()
        if not input_path:
            raise ValueError('choose an INSV file')
        max_points = int(max_points_var.get().strip())
        if max_points <= 0:
            raise ValueError('max points must be greater than zero')
        start_seconds = parse_optional_float(start_var.get(), 'start seconds')
        end_seconds = parse_optional_float(end_var.get(), 'end seconds')
        if (
            start_seconds is not None
            and end_seconds is not None
            and end_seconds <= start_seconds
        ):
            raise ValueError('end seconds must be greater than start seconds')
        return input_path, max_points, start_seconds, end_seconds

    def preview_stats() -> None:
        try:
            input_path, _max_points, start_seconds, end_seconds = read_settings()
        except ValueError as exc:
            messagebox.showerror('Invalid settings', str(exc))
            return

        args = _parse_args([input_path, '--no-plot'])
        args.start_seconds = start_seconds
        args.end_seconds = end_seconds
        root.configure(cursor='watch')
        root.update_idletasks()
        try:
            result, _rows, samples = analyze_file(input_path, args)
            set_stats_text(_format_launcher_stats(result, samples))
        except Exception as exc:
            messagebox.showerror('Could not load stats', str(exc))
        finally:
            root.configure(cursor='')

    def run_plot() -> None:
        try:
            input_path, max_points, start_seconds, end_seconds = read_settings()
        except ValueError as exc:
            messagebox.showerror('Invalid settings', str(exc))
            return

        cmd = [
            sys.executable,
            script_path,
            input_path,
            '--summary-output',
            summary_var.get().strip() or DEFAULT_SUMMARY_OUTPUT,
            '--plot',
            '--plot-max-points',
            str(max_points),
        ]
        if raw_var.get():
            cmd.append('--plot-raw')
        else:
            cmd.append('--plot-normalized')
        if start_seconds is not None:
            cmd.extend(['--start-seconds', str(start_seconds)])
        if end_seconds is not None:
            cmd.extend(['--end-seconds', str(end_seconds)])

        try:
            subprocess.Popen(cmd)
        except OSError as exc:
            messagebox.showerror('Could not run analysis', str(exc))

    padding = {'padx': 8, 'pady': 5}

    tk.Label(root, text='INSV file').grid(row=0, column=0, sticky='w', **padding)
    tk.Entry(root, textvariable=file_var, width=58).grid(row=0, column=1, **padding)
    tk.Button(root, text='Open File', command=choose_file).grid(row=0, column=2, **padding)

    tk.Label(root, text='Summary output').grid(row=1, column=0, sticky='w', **padding)
    tk.Entry(root, textvariable=summary_var, width=58).grid(row=1, column=1, **padding)
    tk.Button(root, text='Choose', command=choose_summary).grid(row=1, column=2, **padding)

    tk.Label(root, text='Start seconds').grid(row=2, column=0, sticky='w', **padding)
    tk.Entry(root, textvariable=start_var, width=14).grid(row=2, column=1, sticky='w', **padding)

    tk.Label(root, text='End seconds').grid(row=3, column=0, sticky='w', **padding)
    tk.Entry(root, textvariable=end_var, width=14).grid(row=3, column=1, sticky='w', **padding)

    tk.Label(root, text='Max points').grid(row=4, column=0, sticky='w', **padding)
    tk.Entry(root, textvariable=max_points_var, width=14).grid(row=4, column=1, sticky='w', **padding)

    tk.Checkbutton(root, text='Raw plot values', variable=raw_var).grid(row=5, column=1, sticky='w', **padding)
    tk.Button(root, text='Preview Stats', command=preview_stats, width=18).grid(row=6, column=1, sticky='w', **padding)
    tk.Button(root, text='Run Plot', command=run_plot, width=18).grid(row=6, column=1, sticky='e', **padding)

    stats_text = scrolledtext.ScrolledText(root, width=82, height=24, wrap='word')
    stats_text.grid(row=7, column=0, columnspan=3, sticky='nsew', **padding)
    stats_text.configure(state='disabled')
    set_stats_text('Choose an INSV file or time window, then click Preview Stats.')

    root.mainloop()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    if args.gui:
        return _run_launcher_gui()

    results: list[dict[str, object]] = []
    feature_rows: list[dict[str, object]] = []
    plot_jobs: list[tuple[dict[str, object], list[MotionSample]]] = []

    if args.plot_output and len(args.inputs) > 1:
        print('error: --plot-output can only be used with one input file', file=sys.stderr)
        return 1

    try:
        for path in args.inputs:
            result, rows, samples = analyze_file(path, args)
            results.append(result)
            feature_rows.extend(rows)
            if args.plot or args.plot_output:
                plot_jobs.append((result, samples))
    except (OSError, ValueError, INSVAccelerometerError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1

    if args.events:
        payload = {
            'generated_by': os.path.basename(__file__),
            'files': results,
        }
        with open(args.events, 'w', encoding='utf-8') as out:
            json.dump(payload, out, indent=2)
            out.write('\n')

    if args.features:
        with open(args.features, 'w', newline='', encoding='utf-8') as out:
            write_features_csv(feature_rows, out)

    if args.summary_output:
        with open(args.summary_output, 'w', encoding='utf-8') as out:
            out.write(format_human_summary(results))

    if args.summary or not args.events and not args.features and not args.summary_output:
        print_human_summary(results)

    try:
        for result, samples in plot_jobs:
            plot_timeline(
                result=result,
                samples=samples,
                metrics=args.plot_metrics,
                normalize=not args.plot_raw,
                max_points=args.plot_max_points,
                smooth_seconds=args.plot_smooth_seconds,
                output=args.plot_output,
                show=args.plot,
            )
    except (RuntimeError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
