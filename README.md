# Insta360 X4 .insv File Repair Tool

Repairs broken or corrupted `.insv` video files from the Insta360 X4 camera.
The current implementation is tuned for X4 files that stopped recording badly:
the `mdat` box is left with a size of `0`, so it incorrectly swallows data that
should have been the final metadata/trailer.

## Common Corruption Scenarios

- **Missing `moov` atom**: recording interrupted by power loss, crash, or a bad stop.
- **Size-0 `mdat`**: the media data box extends to EOF and incorrectly contains the Insta360 trailer.
- **Missing or swallowed trailer**: proprietary Insta360 sensor/stabilization data is appended after `moov`.
- **Incomplete recording**: only bytes that were actually written to disk can be recovered.

## Requirements

- Python 3.10+
- No external dependencies (stdlib only)
- Optional: [FFmpeg](https://ffmpeg.org/) (`ffprobe`) for output verification
- Optional: [ExifTool](https://exiftool.org/) for inspecting QuickTime boxes and trailer warnings

## Project Layout

- `src/insv_tools/`: implementation modules for accelerometer extraction, maneuver analysis, and repair.
- `insv_accelerometer.py`, `insv_maneuver_analysis.py`, `insv_repair.py`: root compatibility launchers.
- `docs/`: user guides, internal references, and format documentation.
- `docs/research/`: investigation reports and sample-set comparisons.
- `patterns/`: hex/editor patterns for inspecting INSV files.
- `pg-reference-docs/`: external paragliding/aero reference documents.
- `scripts/`: small developer/inspection helpers.
- `../sample_insvs_x3/`, `../sample_insvs_x4/`: local sample data.
- `analysis_outputs/`: generated summaries, plots, and feature files.

## Usage

### Extract accelerometer / IMU data

Programmatic API:

```python
from insv_tools.accelerometer import read_accelerometer_timeseries

imu = read_accelerometer_timeseries("video.insv")

print(imu.sample_count)
print(imu.sample_rate_hz)
print(imu.relative_seconds[0])
print(imu.acceleration[0])       # (accel_x, accel_y, accel_z)
print(imu.angular_velocity[0])   # (gyro_x, gyro_y, gyro_z)
```

CSV export:

```bash
python3 insv_accelerometer.py video.insv -o accelerometer.csv --summary
```

The extractor supports both observed X3-style sequential trailers and
X4-style trailers with a `0x000` directory table.

### Analyze acro maneuver candidates

Generate derived force/rotation features, conservative candidate maneuver
intervals, and machine-learning-ready sliding-window features:

```bash
python3 insv_maneuver_analysis.py \
  ../sample_insvs_x4/infinite_and_helis.insv \
  --summary \
  --summary-output analysis_outputs/infinite_and_helis_summary.txt \
  --events analysis_outputs/infinite_and_helis_events.json \
  --features analysis_outputs/infinite_and_helis_features.csv
```

For an interactive matplotlib timeline with shaded/numbered candidate
intervals, run from a Python environment with `matplotlib` installed:

```bash
python3 -m pip install -r requirements.txt
python3 insv_maneuver_analysis.py \
  ../sample_insvs_x4/infinite_and_helis.insv \
  --summary-output analysis_outputs/infinite_and_helis_summary.txt \
  --plot
```

The analyzer is a first-pass labeling aid, not a final classifier.  See
[docs/MANEUVER_ANALYSIS_USAGE.md](docs/MANEUVER_ANALYSIS_USAGE.md) for commands
and labeling workflow, and [docs/MANEUVER_ANALYSIS.md](docs/MANEUVER_ANALYSIS.md)
for functionality, variables, and algorithms.

### Diagnose a file (no repair)

```bash
python3 insv_repair.py broken.insv --diagnose
```

When `--diagnose` is combined with a repair mode, the repair runs first and the
console then prints diagnostics for the repaired output file.

```bash
python3 insv_repair.py broken.insv --reference good_file.insv --diagnose
```

### Repair using a reference file (recommended)

Uses a known-good `.insv` file recorded with the same camera and settings to
reconstruct the `moov` atom with correct codec parameters and sample table
shape. This is the best path for X4 recovery.

```bash
python3 insv_repair.py broken.insv --reference good_file.insv
```

Example from this repo:

```bash
python3 insv_repair.py \
  broken/VID_20260509_161202_00_660.insv \
  --reference reference/VID_20260509_150725_00_656.insv \
  -o broken/VID_20260509_161202_00_660_repaired.insv
```

### Repair without a reference file (scan mode)

Scans raw `mdat` data and rebuilds `moov` from scratch. This is less reliable
than reference mode because X4 AAC is stored without ADTS headers, so audio
frame boundaries must be estimated.

```bash
python3 insv_repair.py broken.insv --scan
```

### Specify output filename

```bash
python3 insv_repair.py broken.insv --scan -o repaired.insv
```

By default, the output is written to `<original_name>_repaired.insv`.

## How It Works

1. **Diagnosis**: parses the MP4/QuickTime box structure and detects `ftyp`,
   `mdat`, `moov`, and the Insta360 trailer footer.
2. **Trailer boundary detection**: finds the Insta360 footer magic
   `8db42d694ccc418790edff439fe026bf`, reads the trailer size, and stops media
   scanning before the trailer starts.
3. **Reference analysis**: copies track metadata, codec sample descriptions, and
   timing rules from a healthy X4 `.insv`.
4. **`mdat` scanning**: detects X4 HEVC access units using the reference-learned
   five-NAL pattern. Non-video gaps between valid video samples are treated as
   raw AAC and split into estimated AAC samples.
5. **`moov` reconstruction**: builds coherent `mvhd`, `trak`, and sample tables
   (`stts`, `stss`, `stsc`, `stsz`, `co64`) with `moov` children ordered like
   the camera files: `mvhd`, `udta`, `trak`, `trak`, `trak`.
6. **Output**: writes a new file as `ftyp + mdat + moov + Insta360 trailer`.
   The repaired `mdat` gets a real size instead of `size=0`.

## Insta360 X4 Structure Notes

Observed good X4 files in this repo use:

- Track 1: HEVC/hvc1 lens video, 1920x1920, timescale 30000, 29.97 fps.
- Track 2: HEVC/hvc1 lens video, 1920x1920, timescale 30000, 29.97 fps.
- Track 3: AAC-LC audio, stereo, 48000 Hz.
- Video sample delta: 1001 ticks.
- Audio sample delta: 1024 ticks.
- `samples_per_chunk = 1` for all tracks.
- `mdat` is interleaved as lens video samples plus audio gaps.
- The proprietary Insta360 trailer is appended after `moov`, not stored as a
  normal MP4 box.

More detailed implementation notes are in
[docs/INSV_REPAIR_NOTES.md](docs/INSV_REPAIR_NOTES.md). A standalone
description of the observed X4 container is in
[docs/INSV_FORMAT.md](docs/INSV_FORMAT.md).

## Validation

After repair, check duration and bitrate:

```bash
ffprobe -v error \
  -show_entries format=duration,size,bit_rate \
  -show_entries stream=index,codec_name,codec_type,width,height,duration,nb_frames \
  -of compact repaired.insv
```

Optional ExifTool check:

```bash
exiftool -api LargeFileSupport=1 -G1 -a -s \
  -Duration -AvgBitrate -MediaDataSize -MediaDataOffset -Warning repaired.insv
```

For the repaired sample tested during development, the result was about
707.44 seconds with a bitrate around 37 Mbps, matching the good reference files
instead of the multi-gigabit bitrate caused by the broken sample tables.

## Limitations

- Designed specifically for the Insta360 X4 files observed in this project.
  Other Insta360 models or other X4 modes may use different dimensions,
  interleaving, timing, or trailer details.
- Reference mode is strongly recommended. Standalone scan mode is best-effort.
- Raw AAC frame boundaries are estimated because the camera stores AAC without
  ADTS sync headers.
- The trailer is preserved when the footer magic and size are present. If the
  trailer itself was never written or is corrupt, advanced Insta360 features may
  still be degraded.
- The tool cannot recover media bytes that were never written to disk.

## License

MIT
