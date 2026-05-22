# Insta360 INSV File Format

This document describes the `.insv` files observed from the reference
recordings in this repository. `.insv` is not a wholly separate container
format; it is an ISO Base Media File Format / QuickTime-style file with an
Insta360 extension and a proprietary trailer.

The files in `../sample_insvs_x3/` identify themselves in the Insta360 maker-notes
trailer as `Insta360 X3`, firmware `v1.1.6_build1`, serial `IAQEB2309KKK7Y`.
The files in `../sample_insvs_x4/` identify themselves as `Insta360 X4`, firmware
`v1.9.21_build5`, serial `IBMEA2405YQG84`. See
[research/X3_X4_FORMAT_COMPARISON.md](research/X3_X4_FORMAT_COMPARISON.md) for
a focused comparison of those two sample sets.

## High-Level Layout

Healthy Insta360 files observed so far use this top-level structure:

```text
ftyp
mdat
moov
Insta360 trailer
```

The first three entries are normal MP4/QuickTime boxes. The final Insta360
trailer is proprietary data appended after `moov`; it is not a normal MP4 box
and should not be parsed as one.

Some damaged recordings use this structure:

```text
ftyp
wide
mdat size=0 / extends to EOF
```

In that case, the `mdat` header incorrectly claims the media data extends to the
end of the file. Bytes near the end may actually be metadata, trailer data, or
padding that should not be indexed as media samples.

## Top-Level Boxes

### `ftyp`

The `ftyp` box identifies the file as MP4/QuickTime-compatible media. The
sample files observed here use:

- major brand: `avc1`
- compatible brands: `avc1`, `isom`

The X3-labeled files in `../sample_insvs_x3/` contain a single `avc1`/H.264 video
track at 3840x1920 plus AAC audio. Earlier X4 repair notes in this repository
describe dual-lens HEVC samples with `hvc1` sample entries. Do not assume the
video track layout from the trailer IMU layout alone.

### `mdat`

The `mdat` box stores interleaved raw media samples for the two video lenses and
the audio track.

Observed properties:

- video samples are MP4-style length-prefixed HEVC NAL units
- audio samples are raw AAC frames without ADTS headers
- chunks are contiguous
- each chunk contains one sample

For normal files, `mdat` has a finite box size. For broken files caused by a bad
recording stop, `mdat` may have size `0`, meaning "extends to EOF". That can
swallow the `moov` or trailer bytes.

### `moov`

The `moov` box contains movie-level metadata, track metadata, and sample tables.
In observed repair references, the `moov` child order is:

```text
mvhd
udta
trak
trak
trak
```

That ordering appears to matter for maximum compatibility with Insta360 tools.

### Insta360 Trailer

The trailer is appended after `moov` and contains proprietary Insta360 metadata,
likely including stabilization, IMU, GPS, exposure, and camera/application data.
It is not required for basic video decoding, but preserving it improves the
chance that Insta360 Studio and related tools will recognize the repaired file
as a full camera recording.

The observed trailer ends with this footer:

```text
uint32 little-endian trailer_size
uint32 little-endian value 3
ASCII "8db42d694ccc418790edff439fe026bf"
```

The trailer start can be calculated as:

```text
file_size - trailer_size
```

When repairing a file, media scanning should stop before this offset. The
trailer bytes should be appended unchanged after the rebuilt `moov`.

### Trailer Records

The trailer is made of records stored as:

```text
record data bytes
uint16 little-endian record_id
uint32 little-endian record_data_size
```

The trailer should normally be walked from the end toward the beginning. Start
78 bytes before EOF, read the first record footer there, seek backward by
`record_data_size`, read the data, then step back another 6 bytes to the
previous record footer. ExifTool uses the same backward scan in
`QuickTimeStream.pl`.

The `../sample_insvs_x3/` files all contain the same record IDs, in this backward
scan order:

| Record ID | Observed meaning |
| --- | --- |
| `0x101` | Insta360 maker notes: serial, model, firmware, lens parameters |
| `0x200` | Preview image / preview data |
| `0x300` | Accelerometer and angular-velocity time series |
| `0x400` | Exposure time series |
| `0x900` | Unknown timed or camera metadata |
| `0xa00` | Unknown short metadata block |

ExifTool also documents optional record IDs not present in these samples, such
as `0x000` directory tables, `0x600` timestamps, and `0x700` GPS records. Newer
Insta360 files may include a `0x000` directory table; if present, that table can
be used instead of purely sequential backward scanning.

## Accelerometer And IMU Data

The accelerometer stream is trailer record `0x300`. ExifTool names the first
three channels `Accelerometer` and the next three channels `AngularVelocity`.
For the sample files here, each `0x300` item is 20 bytes:

```text
uint64 little-endian timecode
uint16 little-endian accel_x_raw
uint16 little-endian accel_y_raw
uint16 little-endian accel_z_raw
uint16 little-endian gyro_x_raw
uint16 little-endian gyro_y_raw
uint16 little-endian gyro_z_raw
```

Convert each raw 16-bit channel value with the ExifTool formula:

```text
value = (raw_uint16 - 0x8000) / 1000
```

The resulting accelerometer units are normally interpreted as `g`. ExifTool
marks the exact axis convention as not confirmed, so code should keep the
source axis labels explicit until validated against known camera motion.

The 64-bit `timecode` increases by about `1000` per sample. Interpreting this
counter as microseconds gives the expected IMU cadence of about 996 Hz and
durations close to the video duration plus a few seconds of leading/trailing
sensor data. ExifTool reports `TimeCode` as `timecode / 1000`, which is a
millisecond-style counter rather than a zero-based video timestamp. For CSV
export, a practical normalized timestamp is:

```text
relative_seconds = (timecode - first_timecode) / 1_000_000
```

ExifTool's current parser also supports a second `0x300` item shape used by
some Insta360 files:

```text
uint64 little-endian timecode
double little-endian accel_x
double little-endian accel_y
double little-endian accel_z
double little-endian gyro_x
double little-endian gyro_y
double little-endian gyro_z
```

That form is 56 bytes per sample. ExifTool chooses between the 20-byte and
56-byte forms by record length and, when ambiguous, by checking whether bytes
16-18 of the first sample are all zero. The `../sample_insvs_x3/` files are all
20-byte records.

Observed `0x300` statistics:

| File | Samples | Time span | Rate | First accelerometer | First angular velocity |
| --- | ---: | ---: | ---: | --- | --- |
| `VID_20251011_164332_00_013.insv` | 45,216 | 45.384 s | 996.27 Hz | `-0.858 -0.840 -0.045` | `0.136 0.313 -0.729` |
| `VID_20251011_180010_00_015.insv` | 343,456 | 344.696 s | 996.40 Hz | `-0.820 -0.543 0.204` | `0.004 0.093 -0.112` |
| `VID_20251013_121252_00_018.insv` | 358,624 | 359.909 s | 996.43 Hz | `-0.766 -0.632 0.356` | `0.744 1.227 -0.085` |
| `VID_20251013_130438_00_019.insv` | 264,048 | 265.020 s | 996.33 Hz | `-0.738 -0.561 -0.122` | `-0.023 -0.104 0.092` |
| `VID_20251013_173534_00_022.insv` | 122,288 | 122.735 s | 996.35 Hz | `-0.909 -0.559 -0.118` | `-0.456 0.365 -0.234` |
| `VID_20251013_173750_00_023.insv` | 29,408 | 29.514 s | 996.36 Hz | `-0.892 -0.765 0.856` | `-1.030 0.833 0.806` |
| `VID_20251013_174204_00_024.insv` | 336,192 | 337.414 s | 996.37 Hz | `-0.837 -0.645 0.299` | `0.720 0.579 -0.115` |

Useful references for this section:

- ExifTool `QuickTimeStream.pl` handles the Insta360 trailer, record `0x300`,
  and the 20-byte/56-byte sample distinction.
- `nivim/Insta360toBlackBoxCSV` also walks the trailer backward and treats
  record `0x300` as IMU data, though its older script assumes 56-byte records
  and labels the channels for gyro-oriented Blackbox export.
- The ExifTool forum thread around Insta360 trailer parsing documents
  maker-note parameters and confirms that ExifTool requires
  `ExtractEmbedded`/`-ee` to emit trailer time-series metadata.

## Tracks

The X4 repair notes in this repository describe three tracks.

### Track 1: Video, Lens 1

- handler type: `vide`
- codec: `hvc1`
- dimensions: 1920x1920
- timescale: 30000
- frame rate: 29.97 fps
- sample delta: 1001 ticks
- samples per chunk: 1

### Track 2: Video, Lens 2

- handler type: `vide`
- codec: `hvc1`
- dimensions: 1920x1920
- timescale: 30000
- frame rate: 29.97 fps
- sample delta: 1001 ticks
- samples per chunk: 1

### Track 3: Audio

- handler type: `soun`
- codec: `mp4a`
- profile: AAC-LC
- channels: 2
- sample rate: 48000 Hz
- timescale: 48000
- sample delta: 1024 ticks
- samples per chunk: 1

## Media Data Layout

The `mdat` payload is interleaved. Conceptually, it looks like:

```text
video lens 1 sample
video lens 2 sample
audio sample(s)
video lens 1 sample
video lens 2 sample
audio sample(s)
...
```

The exact number of audio samples between video samples can vary because audio
uses a 48000 Hz timescale and 1024-sample AAC frames, while video uses a 30000
timescale and 1001-tick frame duration.

## HEVC Video Samples

Video data is stored as MP4-style HEVC, not Annex B. That means each NAL unit is
prefixed by a 4-byte big-endian length:

```text
uint32 big-endian nal_length
nal bytes
uint32 big-endian nal_length
nal bytes
...
```

No Annex B start code (`00 00 01` or `00 00 00 01`) is used inside normal
samples.

For the HEVC X4 repair references, each video sample has this strong pattern:

- exactly five length-prefixed HEVC VCL NAL units
- all five NAL units have the same HEVC NAL type
- the first NAL has `first_slice_segment_in_pic_flag = 1`
- the next four NALs have `first_slice_segment_in_pic_flag = 0`
- the HEVC forbidden-zero bit is clear
- `temporal_id_plus1` is nonzero

This pattern is useful for recovery because raw AAC and trailer bytes can
contain accidental values that look weakly HEVC-like. A strict access-unit
detector avoids treating those false positives as video.

## AAC Audio Samples

Audio is raw AAC-LC in MP4 format. It does not include ADTS headers. In
practice, this means there is no `0xfff` syncword to scan for.

Observed audio samples average close to 505 bytes, but frame sizes vary. During
repair, audio regions can be inferred from the gaps between detected video
samples and split into estimated AAC samples. The final audio duration should be
kept aligned with the recovered video duration.

## `moov` Sample Tables

Each track's `trak` contains the usual MP4/QuickTime metadata hierarchy:

```text
trak
  tkhd
  mdia
    mdhd
    hdlr
    minf
      vmhd or smhd
      dinf
      stbl
        stsd
        stts
        stss  (video sync samples)
        stsc
        stsz
        co64
```

Important tables:

- `stsd`: codec sample description (`hvc1` for video, `mp4a` for audio)
- `stts`: sample count and duration
- `stss`: sync/keyframe sample numbers for video
- `stsc`: sample-to-chunk mapping
- `stsz`: individual sample sizes
- `co64`: 64-bit chunk offsets

Large Insta360 files commonly use `co64`, not 32-bit `stco`, because files can
be large enough that 64-bit offsets are safer.

## `udta`

The `udta` box inside `moov` contains Insta360-specific metadata. Observed
children include proprietary data such as:

- `AMBA`
- `nail`
- `free`

For repair, copying `udta` from a healthy reference file gives the rebuilt
`moov` a structure closer to a camera-authored file. Some values inside that
metadata may still be recording-specific.

## Corruption Patterns

### Missing `moov`

If recording stops incorrectly, the camera may write media data but fail to
write the final `moov`. Without `moov`, ordinary players do not know sample
offsets, sizes, durations, or codec parameters.

Repair approach:

- scan the `mdat` payload
- reconstruct sample tables
- write a new `moov`
- append the preserved Insta360 trailer if present

### Size-0 `mdat`

A broken file may contain an `mdat` with size `0`. In MP4, that means the box
extends to the end of the file. For damaged Insta360 files, that can be wrong:
the trailer footer proves that trailing bytes are not media.

Repair approach:

- calculate the trailer start from the footer
- treat only bytes before the trailer as `mdat` media data
- rewrite `mdat` with an explicit finite size
- write `moov`
- append the trailer after `moov`

### Bogus Duration Or Bitrate

If repair indexes only a few seconds of samples while copying gigabytes of
media payload, tools may report an absurdly high bitrate. This usually means
the sample table duration is too short for the copied `mdat` data.

Repair checks:

- both video tracks should have similar sample counts
- audio duration should closely match video duration
- total bitrate should be in the same range as reference files
- ExifTool should report the trailer after `moov`

## Practical Validation Commands

Inspect stream durations and frame counts:

```bash
ffprobe -v error \
  -show_entries format=duration,size,bit_rate \
  -show_entries stream=index,codec_name,codec_type,width,height,duration,nb_frames \
  -of compact repaired.insv
```

Inspect QuickTime metadata and trailer detection:

```bash
exiftool -api LargeFileSupport=1 -G1 -a -s \
  -Duration -AvgBitrate -MediaDataSize -MediaDataOffset -Warning repaired.insv
```

Expected signs of a good X4 repair:

- two `hevc` video streams at 1920x1920
- one `aac` audio stream
- video and audio durations are nearly equal
- bitrate is similar to known-good `.insv` files
- `moov` follows `mdat`
- Insta360 trailer magic remains present at EOF
