#!/usr/bin/env python3
"""
Extract Insta360 INSV accelerometer and angular-velocity data.

The observed X3 and X4 files store IMU data in the proprietary Insta360 trailer
record 0x300.  X3 files in this repository can be walked sequentially backward
through the trailer.  X4 files use a final 0x000 directory table, which points
to the actual records.
"""

import argparse
import csv
import os
import statistics
import struct
import sys
from dataclasses import dataclass
from typing import BinaryIO, Iterable, Optional, TextIO


INSTA360_TRAILER_MAGIC = b'8db42d694ccc418790edff439fe026bf'
INSTA360_TRAILER_FOOTER_LEN = len(INSTA360_TRAILER_MAGIC) + 8
INSTA360_LAST_RECORD_FOOTER_FROM_EOF = 78
ACCELEROMETER_RECORD_ID = 0x300
DIRECTORY_RECORD_ID = 0x000


class INSVAccelerometerError(Exception):
    """Raised when accelerometer data can not be extracted."""


@dataclass(frozen=True)
class TrailerRecord:
    """One Insta360 trailer record."""

    record_id: int
    size: int
    data_offset: int
    footer_offset: int
    table_id: Optional[int] = None


@dataclass(frozen=True)
class TrailerInfo:
    """Insta360 trailer location and decoded record directory."""

    offset: int
    size: int
    records: tuple[TrailerRecord, ...]
    used_directory_table: bool

    def first_record(self, record_id: int) -> Optional[TrailerRecord]:
        """Return the first record with the given actual footer ID."""
        for record in self.records:
            if record.record_id == record_id:
                return record
        return None


@dataclass(frozen=True)
class AccelerometerSample:
    """One decoded IMU sample from record 0x300."""

    index: int
    timecode: int
    relative_seconds: float
    accel_x: float
    accel_y: float
    accel_z: float
    gyro_x: float
    gyro_y: float
    gyro_z: float
    raw_values: Optional[tuple[int, int, int, int, int, int]] = None


@dataclass(frozen=True)
class AccelerometerData:
    """Decoded accelerometer stream and source metadata."""

    source_path: str
    trailer: TrailerInfo
    record: TrailerRecord
    sample_stride: int
    samples: tuple[AccelerometerSample, ...]

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    @property
    def duration_seconds(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        return self.samples[-1].relative_seconds

    @property
    def sample_rate_hz(self) -> float:
        duration = self.duration_seconds
        if len(self.samples) < 2 or duration <= 0:
            return 0.0
        return (len(self.samples) - 1) / duration

    @property
    def timecode_deltas(self) -> tuple[int, ...]:
        return tuple(
            b.timecode - a.timecode
            for a, b in zip(self.samples, self.samples[1:])
        )

    @property
    def timecodes(self) -> tuple[int, ...]:
        return tuple(sample.timecode for sample in self.samples)

    @property
    def relative_seconds(self) -> tuple[float, ...]:
        return tuple(sample.relative_seconds for sample in self.samples)

    @property
    def acceleration(self) -> tuple[tuple[float, float, float], ...]:
        return tuple(
            (sample.accel_x, sample.accel_y, sample.accel_z)
            for sample in self.samples
        )

    @property
    def angular_velocity(self) -> tuple[tuple[float, float, float], ...]:
        return tuple(
            (sample.gyro_x, sample.gyro_y, sample.gyro_z)
            for sample in self.samples
        )


def find_insta360_trailer(f: BinaryIO, file_size: int) -> tuple[int, int]:
    """Return (offset, size) for the Insta360 trailer."""
    if file_size < INSTA360_TRAILER_FOOTER_LEN:
        raise INSVAccelerometerError('file is too small to contain an Insta360 trailer')

    f.seek(file_size - len(INSTA360_TRAILER_MAGIC))
    if f.read(len(INSTA360_TRAILER_MAGIC)) != INSTA360_TRAILER_MAGIC:
        raise INSVAccelerometerError('Insta360 trailer magic not found at EOF')

    f.seek(file_size - INSTA360_TRAILER_FOOTER_LEN)
    footer = f.read(8)
    if len(footer) != 8:
        raise INSVAccelerometerError('could not read Insta360 trailer footer')

    trailer_size, version = struct.unpack('<II', footer)
    if version != 3:
        raise INSVAccelerometerError(f'unexpected Insta360 trailer version {version}')
    if trailer_size < INSTA360_TRAILER_FOOTER_LEN or trailer_size > file_size:
        raise INSVAccelerometerError(f'invalid Insta360 trailer size {trailer_size}')

    return file_size - trailer_size, trailer_size


def read_trailer_info(f: BinaryIO, file_size: int) -> TrailerInfo:
    """Read Insta360 trailer records, including X4 directory-table trailers."""
    trailer_offset, trailer_size = find_insta360_trailer(f, file_size)
    first_footer_offset = file_size - INSTA360_LAST_RECORD_FOOTER_FROM_EOF
    if first_footer_offset < trailer_offset:
        raise INSVAccelerometerError('last trailer record footer is outside trailer')

    record_id, record_size = _read_record_footer(f, first_footer_offset)
    data_offset = first_footer_offset - record_size
    if data_offset < trailer_offset:
        raise INSVAccelerometerError('last trailer record extends before trailer start')

    if record_id == DIRECTORY_RECORD_ID and record_size:
        records = _read_directory_records(
            f,
            trailer_offset=trailer_offset,
            trailer_size=trailer_size,
            directory_offset=data_offset,
            directory_size=record_size,
        )
        used_directory_table = True
    else:
        records = _walk_records_backward(
            f,
            file_size=file_size,
            trailer_offset=trailer_offset,
            trailer_size=trailer_size,
        )
        used_directory_table = False

    return TrailerInfo(
        offset=trailer_offset,
        size=trailer_size,
        records=tuple(records),
        used_directory_table=used_directory_table,
    )


def extract_accelerometer(path: str) -> AccelerometerData:
    """Extract and decode record 0x300 from an INSV file."""
    with open(path, 'rb') as f:
        file_size = os.fstat(f.fileno()).st_size
        trailer = read_trailer_info(f, file_size)
        record = trailer.first_record(ACCELEROMETER_RECORD_ID)
        if record is None:
            record_ids = ', '.join(f'0x{r.record_id:x}' for r in trailer.records)
            raise INSVAccelerometerError(
                f'accelerometer record 0x300 not found; trailer records: {record_ids}'
            )

        f.seek(record.data_offset)
        payload = f.read(record.size)
        if len(payload) != record.size:
            raise INSVAccelerometerError('could not read full accelerometer record')

    stride = _detect_accelerometer_stride(payload)
    samples = _decode_accelerometer_payload(payload, stride)
    return AccelerometerData(
        source_path=path,
        trailer=trailer,
        record=record,
        sample_stride=stride,
        samples=tuple(samples),
    )


def read_accelerometer_timeseries(path: str) -> AccelerometerData:
    """Return decoded time-series accelerometer data from an INSV file.

    This is the primary programmatic API.  The returned object exposes rows via
    ``samples`` and column-style properties such as ``relative_seconds``,
    ``acceleration``, and ``angular_velocity``.
    """
    return extract_accelerometer(path)


def write_csv(
    data: AccelerometerData,
    out: TextIO,
    include_raw: bool = False,
) -> None:
    """Write accelerometer samples as CSV."""
    fields = [
        'sample_index',
        'timecode',
        'relative_seconds',
        'accel_x',
        'accel_y',
        'accel_z',
        'gyro_x',
        'gyro_y',
        'gyro_z',
    ]
    if include_raw:
        fields.extend([
            'raw_accel_x',
            'raw_accel_y',
            'raw_accel_z',
            'raw_gyro_x',
            'raw_gyro_y',
            'raw_gyro_z',
        ])

    writer = csv.DictWriter(out, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    for sample in data.samples:
        row = {
            'sample_index': sample.index,
            'timecode': sample.timecode,
            'relative_seconds': f'{sample.relative_seconds:.6f}',
            'accel_x': _format_float(sample.accel_x),
            'accel_y': _format_float(sample.accel_y),
            'accel_z': _format_float(sample.accel_z),
            'gyro_x': _format_float(sample.gyro_x),
            'gyro_y': _format_float(sample.gyro_y),
            'gyro_z': _format_float(sample.gyro_z),
        }
        if include_raw:
            raw_values = sample.raw_values or ('', '', '', '', '', '')
            row.update({
                'raw_accel_x': raw_values[0],
                'raw_accel_y': raw_values[1],
                'raw_accel_z': raw_values[2],
                'raw_gyro_x': raw_values[3],
                'raw_gyro_y': raw_values[4],
                'raw_gyro_z': raw_values[5],
            })
        writer.writerow(row)


def summarize(data: AccelerometerData) -> dict[str, object]:
    """Return useful summary statistics for decoded accelerometer data."""
    deltas = data.timecode_deltas
    nonpositive_deltas = sum(1 for delta in deltas if delta <= 0)

    summary: dict[str, object] = {
        'trailer_offset': data.trailer.offset,
        'trailer_size': data.trailer.size,
        'used_directory_table': data.trailer.used_directory_table,
        'record_offset': data.record.data_offset,
        'record_size': data.record.size,
        'sample_stride': data.sample_stride,
        'sample_count': data.sample_count,
        'duration_seconds': data.duration_seconds,
        'sample_rate_hz': data.sample_rate_hz,
        'nonpositive_deltas': nonpositive_deltas,
    }
    if deltas:
        summary.update({
            'delta_min': min(deltas),
            'delta_median': statistics.median(deltas),
            'delta_max': max(deltas),
        })
    return summary


def print_summary(data: AccelerometerData, out: TextIO = sys.stderr) -> None:
    """Print a human-readable summary."""
    stats = summarize(data)
    print(f"Trailer offset: 0x{stats['trailer_offset']:x}", file=out)
    print(f"Trailer size: {stats['trailer_size']:,} bytes", file=out)
    print(f"Used directory table: {stats['used_directory_table']}", file=out)
    print(f"Record 0x300 offset: 0x{stats['record_offset']:x}", file=out)
    print(f"Record 0x300 size: {stats['record_size']:,} bytes", file=out)
    print(f"Sample stride: {stats['sample_stride']} bytes", file=out)
    print(f"Samples: {stats['sample_count']:,}", file=out)
    print(f"IMU duration: {stats['duration_seconds']:.6f} s", file=out)
    print(f"IMU sample rate: {stats['sample_rate_hz']:.6f} Hz", file=out)
    if 'delta_min' in stats:
        print(
            'Timecode deltas: '
            f"min={stats['delta_min']} "
            f"median={stats['delta_median']} "
            f"max={stats['delta_max']} "
            f"nonpositive={stats['nonpositive_deltas']}",
            file=out,
        )


def _read_record_footer(f: BinaryIO, offset: int) -> tuple[int, int]:
    f.seek(offset)
    footer = f.read(6)
    if len(footer) != 6:
        raise INSVAccelerometerError(f'could not read record footer at 0x{offset:x}')
    return struct.unpack('<HI', footer)


def _read_directory_records(
    f: BinaryIO,
    trailer_offset: int,
    trailer_size: int,
    directory_offset: int,
    directory_size: int,
) -> list[TrailerRecord]:
    f.seek(directory_offset)
    directory = f.read(directory_size)
    if len(directory) != directory_size:
        raise INSVAccelerometerError('could not read full trailer directory table')

    records: list[TrailerRecord] = []
    for pos in range(0, len(directory) - 9, 10):
        table_id, size, offset = struct.unpack_from('<HII', directory, pos)
        if not table_id or not size:
            continue
        if offset + size >= trailer_size:
            continue

        data_offset = trailer_offset + offset
        footer_offset = data_offset + size
        actual_id, actual_size = _read_record_footer(f, footer_offset)
        if actual_size != size:
            continue

        records.append(TrailerRecord(
            record_id=actual_id,
            size=size,
            data_offset=data_offset,
            footer_offset=footer_offset,
            table_id=table_id,
        ))

    directory_footer_offset = directory_offset + directory_size
    records.append(TrailerRecord(
        record_id=DIRECTORY_RECORD_ID,
        size=directory_size,
        data_offset=directory_offset,
        footer_offset=directory_footer_offset,
        table_id=DIRECTORY_RECORD_ID,
    ))
    records.sort(key=lambda record: record.data_offset)
    return records


def _walk_records_backward(
    f: BinaryIO,
    file_size: int,
    trailer_offset: int,
    trailer_size: int,
) -> list[TrailerRecord]:
    records: list[TrailerRecord] = []
    footer_offset = file_size - INSTA360_LAST_RECORD_FOOTER_FROM_EOF
    trailer_end = trailer_offset + trailer_size

    while trailer_offset <= footer_offset < trailer_end:
        record_id, record_size = _read_record_footer(f, footer_offset)
        data_offset = footer_offset - record_size
        if record_size == 0 or data_offset < trailer_offset:
            break

        records.append(TrailerRecord(
            record_id=record_id,
            size=record_size,
            data_offset=data_offset,
            footer_offset=footer_offset,
            table_id=record_id,
        ))
        footer_offset = data_offset - 6

    records.sort(key=lambda record: record.data_offset)
    return records


def _detect_accelerometer_stride(payload: bytes) -> int:
    if not payload:
        raise INSVAccelerometerError('empty accelerometer record')

    size = len(payload)
    if size % 20 and not size % 56:
        return 56
    if size % 56 and not size % 20:
        return 20
    if not size % 20 and not size % 56:
        return 56 if payload[16:19] == b'\x00\x00\x00' else 20

    raise INSVAccelerometerError(
        f'accelerometer record size {size} is not divisible by 20 or 56'
    )


def _decode_accelerometer_payload(
    payload: bytes,
    stride: int,
) -> Iterable[AccelerometerSample]:
    if len(payload) % stride:
        raise INSVAccelerometerError(
            f'accelerometer payload is not a multiple of {stride} bytes'
        )

    first_timecode: Optional[int] = None
    for index, offset in enumerate(range(0, len(payload), stride)):
        if stride == 20:
            timecode, *raw_values = struct.unpack_from('<Q6H', payload, offset)
            converted = tuple((value - 0x8000) / 1000 for value in raw_values)
            raw_tuple = (
                raw_values[0],
                raw_values[1],
                raw_values[2],
                raw_values[3],
                raw_values[4],
                raw_values[5],
            )
        elif stride == 56:
            values = struct.unpack_from('<Q6d', payload, offset)
            timecode = values[0]
            converted = values[1:]
            raw_tuple = None
        else:
            raise INSVAccelerometerError(f'unsupported accelerometer stride {stride}')

        if first_timecode is None:
            first_timecode = timecode
        relative_seconds = (timecode - first_timecode) / 1_000_000

        yield AccelerometerSample(
            index=index,
            timecode=timecode,
            relative_seconds=relative_seconds,
            accel_x=converted[0],
            accel_y=converted[1],
            accel_z=converted[2],
            gyro_x=converted[3],
            gyro_y=converted[4],
            gyro_z=converted[5],
            raw_values=raw_tuple,
        )


def _format_float(value: float) -> str:
    return f'{value:.6f}'.rstrip('0').rstrip('.')


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Extract accelerometer and angular-velocity data from an INSV file.',
    )
    parser.add_argument('input', help='Input .insv file')
    parser.add_argument(
        '-o', '--output',
        help='CSV output path. Defaults to stdout unless --summary-only is used.',
    )
    parser.add_argument(
        '--raw',
        action='store_true',
        help='Include raw 16-bit channel values when the 20-byte record format is used.',
    )
    parser.add_argument(
        '--summary',
        action='store_true',
        help='Print extraction summary to stderr.',
    )
    parser.add_argument(
        '--summary-only',
        action='store_true',
        help='Print summary without writing CSV.',
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        data = extract_accelerometer(args.input)
        if args.summary or args.summary_only:
            print_summary(data)

        if not args.summary_only:
            if args.output:
                with open(args.output, 'w', newline='') as out:
                    write_csv(data, out, include_raw=args.raw)
            else:
                write_csv(data, sys.stdout, include_raw=args.raw)
    except (OSError, INSVAccelerometerError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
