#!/usr/bin/env python3
"""Analyze Insta360 INSV IMU data for acro paragliding maneuvers.

This module intentionally keeps the first analysis layer dependency-free.  It
extracts accelerometer/gyro samples with ``insv_accelerometer.py``, computes
derived load and rotation signals, finds coarse candidate maneuver intervals,
and exports machine-learning feature windows.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Optional, TextIO, cast

from insv_accelerometer import (
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


def derive_motion_samples(data: AccelerometerData) -> list[MotionSample]:
    """Return samples with vector magnitudes and jerk estimates."""
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
    return derived


def summarize_motion(data: AccelerometerData, samples: list[MotionSample]) -> dict[str, object]:
    """Build recording-level summary statistics."""
    g_force = [sample.g_force for sample in samples]
    gyro = [sample.gyro_norm for sample in samples]
    jerk = [sample.jerk_g_s for sample in samples[1:]]
    return {
        'source_path': data.source_path,
        'sample_count': data.sample_count,
        'duration_seconds': data.duration_seconds,
        'sample_rate_hz': data.sample_rate_hz,
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
    """Detect coarse acro maneuver candidates from load and rotation signals.

    These are deliberately conservative *candidate* labels.  They are useful for
    triage and as a labeling aid, not as final maneuver classifications.
    """
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
    enriched.sort(key=lambda event: (event['start_seconds'], event['kind']))
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
    summary = summarize_motion(data, samples)
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
        summary = result['summary']
        lines.append(os.path.basename(str(summary['source_path'])))
        lines.append(
            f"  samples={summary['sample_count']:,} "
            f"duration={summary['duration_seconds']:.3f}s "
            f"rate={summary['sample_rate_hz']:.3f}Hz"
        )
        g_force = summary['g_force']
        gyro = summary['gyro_norm']
        lines.append(
            f"  g-force mean={g_force['mean']:.3f} "
            f"p95={g_force['p95']:.3f} max={g_force['max']:.3f}"
        )
        lines.append(
            f"  gyro norm mean={gyro['mean']:.3f} "
            f"p95={gyro['p95']:.3f} max={gyro['max']:.3f}"
        )
        lines.append(f"  candidate events={summary['candidate_event_count']}")
        for event in result['events'][:12]:
            lines.append(
                f"    {event['kind']} "
                f"{event['start_seconds']:.3f}-{event['end_seconds']:.3f}s "
                f"dur={event['duration_seconds']:.3f}s "
                f"max_g={event['g_force']['max']:.3f} "
                f"max_gyro={event['gyro_norm']['max']:.3f} "
                f"axis={event['dominant_gyro_axis']}"
                f"{_format_load_pulse_count(event)}"
            )
        if len(result['events']) > 12:
            lines.append(f"    ... {len(result['events']) - 12} more")
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
) -> None:
    """Open or save a matplotlib timeline plot for one analyzed file."""
    os.environ.setdefault(
        'MPLCONFIGDIR',
        os.path.join(os.getcwd(), '.cache', 'matplotlib'),
    )
    os.environ.setdefault(
        'XDG_CACHE_HOME',
        os.path.join(os.getcwd(), '.cache'),
    )
    try:
        import matplotlib
        if output and not show:
            matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.backend_bases import MouseEvent
        from matplotlib.patches import Patch, Rectangle
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

    stride = max(1, math.ceil(len(samples) / max_points))
    times_all = [sample.time for sample in samples]
    times = times_all[::stride]
    sample_rate_hz = _sample_rate_from_times(times_all)
    smooth_n = max(1, round(sample_rate_hz * smooth_seconds))

    fig = plt.figure(figsize=(22, 8.5), constrained_layout=True)
    grid = fig.add_gridspec(
        2,
        2,
        width_ratios=[5.8, 1.45],
        height_ratios=[4.6, 1.55],
        hspace=0.08,
        wspace=0.04,
    )
    ax = fig.add_subplot(grid[0, 0])
    event_ax = fig.add_subplot(grid[1, 0], sharex=ax)
    key_ax = fig.add_subplot(grid[:, 1])
    key_ax.axis('off')
    ax.set_title(f"{os.path.basename(str(result['summary']['source_path']))} IMU timeline")
    ax.set_ylabel('Normalized metric value' if normalize else 'Raw metric value')
    ax.grid(True, alpha=0.25)

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
            0.72,
            facecolor=color,
            edgecolor='#111111',
            linewidth=0.6,
            alpha=0.92,
        )
        event_ax.add_patch(rect)
        short_label = f"{index}. {EVENT_LABELS.get(kind, _short_event_label(kind))}"
        text = event_ax.text(
            start + duration / 2,
            y,
            short_label,
            ha='center',
            va='center',
            fontsize=8.5,
            color='white',
            weight='bold',
            clip_on=True,
            visible=False,
        )
        event_boxes.append({
            'rect': rect,
            'text': text,
            'tooltip': _event_bar_label(index, event),
            'center_x': start + duration / 2,
            'center_y': y,
            'padding_px': 12.0,
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
                    ax.scatter(
                        [peak_time],
                        [1.02],
                        marker='v',
                        color='#111111',
                        alpha=0.9,
                        clip_on=False,
                        transform=ax.get_xaxis_transform(),
                        zorder=5,
                    )
                    ax.text(
                        peak_time,
                        1.02,
                        f"{peak_g:.2f}g",
                        transform=ax.get_xaxis_transform(),
                        ha='center',
                        va='bottom',
                        fontsize=7.3,
                        color='#111111',
                        bbox={'boxstyle': 'round,pad=0.18', 'fc': '#ffffff', 'ec': 'none', 'alpha': 0.85},
                        clip_on=False,
                    )

                    valley_time = float(pulse.get('valley_seconds_raw', pulse.get('valley_seconds', peak_time)))
                    valley_g = float(pulse.get('valley_g_force_raw', pulse.get('valley_g_force', peak_g)))
                    ax.scatter(
                        [valley_time],
                        [0.02],
                        marker='^',
                        color='#111111',
                        alpha=0.75,
                        clip_on=False,
                        transform=ax.get_xaxis_transform(),
                        zorder=5,
                    )
                    ax.text(
                        valley_time,
                        0.02,
                        f"{valley_g:.2f}g",
                        transform=ax.get_xaxis_transform(),
                        ha='center',
                        va='top',
                        fontsize=7.0,
                        color='#111111',
                        bbox={'boxstyle': 'round,pad=0.18', 'fc': '#ffffff', 'ec': 'none', 'alpha': 0.8},
                        clip_on=False,
                    )
        key_rows.append((index, event, color))

    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles, labels, loc='upper right', fontsize=8)
    event_ax.set_yticks(list(lane_for_kind.values()))
    event_ax.set_yticklabels([
        EVENT_LABELS.get(kind, kind.replace('_candidate', '').replace('_', ' '))
        for kind in reversed(event_kinds)
    ])
    event_ax.set_xlabel('Time (seconds)')
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
                previous = text_artist.get_visible()
                text_artist.set_visible(True)
                bbox = text_artist.get_window_extent(renderer=renderer)
                text_width = bbox.width + float(box.get('padding_px', 0.0))
                text_artist.set_visible(previous)
                visible = pixel_width >= text_width and width > 0
                if visible != previous:
                    text_artist.set_visible(visible)
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
                    if box['text'].get_visible():
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
    event = {
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
        index for index in (load_peak_indices or [])
        if segment.sample_start <= index <= segment.sample_end
    ]
    if pulse_indices:
        pulse_records = _load_pulse_records(
            samples=samples,
            pulse_indices=pulse_indices,
            sample_start=segment.sample_start,
            sample_end=segment.sample_end,
        )
        pulse_times = [record['seconds'] for record in pulse_records]
        pulse_g = [record['g_force'] for record in pulse_records]
        event['load_pulses'] = {
            'count': len(pulse_records),
            'peaks': pulse_records,
            'peak_seconds': [_round(time) for time in pulse_times],
            'peak_g': [_round(value) for value in pulse_g],
            'max_peak_g': _round(max(pulse_g)),
            'median_period_seconds': (
                _round(_median_delta(pulse_times))
                if len(pulse_times) > 1 else None
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
        dominant_index = means.index(max(means)) if means else 0
        row[f'{prefix}_dominant_axis'] = axes[dominant_index][-1]
        row[f'{prefix}_axis_dominance'] = _round(max(means) / total) if total else 0.0

    row['gyro_sign_changes_x'] = _sign_changes([sample.gyro_x for sample in window])
    row['gyro_sign_changes_y'] = _sign_changes([sample.gyro_y for sample in window])
    row['gyro_sign_changes_z'] = _sign_changes([sample.gyro_z for sample in window])
    return row


def _dominant_gyro_axis(window: list[MotionSample]) -> dict[str, object]:
    if not window:
        return {'axis': '', 'dominance': 0.0, 'mean_abs': 0.0}
    means = {
        'x': statistics.fmean(abs(sample.gyro_x) for sample in window),
        'y': statistics.fmean(abs(sample.gyro_y) for sample in window),
        'z': statistics.fmean(abs(sample.gyro_z) for sample in window),
    }
    total = sum(means.values())
    axis = max(means, key=means.get)
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


def _draw_candidate_key(key_ax: object, rows: list[tuple[int, dict[str, object], str]]) -> None:
    from matplotlib.patches import Rectangle

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
    y = 0.955
    row_gap = 0.067
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
                (0.0, y - 0.026),
                0.035,
                0.028,
                transform=key_ax.transAxes,
                facecolor=color,
                edgecolor='#111111',
                linewidth=0.5,
            )
        )
        key_ax.text(
            0.045,
            y,
            _event_bar_label(index, event),
            transform=key_ax.transAxes,
            ha='left',
            va='top',
            fontsize=8.5,
            weight='bold' if event['kind'] == 'infinite_tumble_candidate' else 'normal',
            color='#202020',
            wrap=True,
        )
        y -= row_gap


def _format_load_pulse_count(event: dict[str, object]) -> str:
    load_pulses = event.get('load_pulses')
    if not isinstance(load_pulses, dict):
        return ''
    count = load_pulses.get('count')
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
    parser.add_argument('inputs', nargs='+', help='Input .insv file(s)')
    parser.add_argument('--events', help='Write event summary JSON to this path')
    parser.add_argument('--features', help='Write sliding-window feature CSV to this path')
    parser.add_argument('--summary', action='store_true', help='Print a human summary')
    parser.add_argument('--summary-output', help='Write the human summary to this text file')
    parser.add_argument('--plot', action='store_true', help='Open an interactive matplotlib timeline plot')
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
        help='Plot raw metric values instead of robust normalized values',
    )
    parser.add_argument(
        '--plot-max-points',
        type=int,
        default=60000,
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
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
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
