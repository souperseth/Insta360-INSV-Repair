# Insta360 X3 vs X4 INSV Format Comparison

This report compares the X3 `.insv` files currently in `../sample_insvs_x3/`
against the X4 `.insv` files currently in `../sample_insvs_x4/`.

Both sides were re-examined directly from the local sample files with ExifTool
13.55, `ffprobe`, and a binary MP4/trailer walk.

For the deeper accelerometer-only pass, see
[X4_ACCELEROMETER_INVESTIGATION.md](X4_ACCELEROMETER_INVESTIGATION.md).

## Executive Summary

The X3 and X4 files share the broad container family and the Insta360 trailer
footer scheme, but their media layouts are different enough that the current
X4 repair scanner should not be reused for X3 without a separate AVC/H.264
path.

The strongest observed differences:

- X3 samples contain one `avc1`/H.264 video track at 3840x1920 plus one AAC
  audio track.
- X4 samples contain two `hvc1`/HEVC lens video tracks at 1920x1920 plus one
  AAC audio track.
- X3 samples include a top-level `free` box between `moov` and the Insta360
  trailer; it pads the file exactly to the trailer start.
- X4 samples include a top-level `wide` box after `ftyp`; `moov` is immediately
  followed by the Insta360 trailer.
- X3 sample tables use `co64`; the observed X4 samples use `stco`.
- X4 video sample tables contain `ctts`; X3 video sample tables do not.
- Both observed X3 and X4 sets store trailer record `0x300` as 20-byte IMU
  records, but the X4 trailer uses a `0x000` directory table while the X3
  trailer can be walked sequentially backward.

## High-Level Container Layout

All seven X3 samples use:

```text
ftyp
mdat
moov
free
Insta360 trailer
```

The `free` box is not inside the trailer. It is a normal top-level MP4 box that
fills the gap from the end of `moov` to the footer-derived trailer start. In
these X3 files, the trailer starts at a clean boundary such as `0xa000000`,
`0x2d000000`, `0x61800000`, `0x7d000000`, `0x7f800000`, `0x85c00000`, or
`0x10400000`.

All sixteen X4 samples use:

```text
ftyp
wide
mdat
moov
Insta360 trailer
```

That means repair code should not assume one fixed post-`mdat` layout. X3 has
`free` before the trailer; X4 has `wide` before `mdat` and no observed `free`
between `moov` and the trailer. The reliable rule remains: find the trailer
using the footer magic and little-endian trailer size, then treat
`file_size - trailer_size` as the true trailer start.

## File Type And Brands

The X3 samples use:

```text
major_brand: avc1
minor_version: 0
compatible_brands: avc1, isom, 0, 0
```

The X4 samples use:

```text
major_brand: avc1
minor_version: 0x20140200
compatible_brands: avc1, isom, 0
```

The shared `avc1` major brand is not enough to identify the video codec. The
track sample entries are the meaningful signal: X3 uses `avc1`; X4 uses
`hvc1`.

## Track Layout

### X3 Samples

Each X3 sample has two tracks:

| Track | Handler | Codec | Shape |
| --- | --- | --- | --- |
| 1 | `vide` | `avc1` / H.264 | 3840x1920, 29.97 fps, timescale 30000 |
| 2 | `soun` | `mp4a` / AAC-LC | stereo AAC, 48000 Hz, timescale 48000 |

Both tracks use one sample per chunk and `co64` chunk offsets. Video `stts`
has one entry of `(sample_count, 1001)`. Audio `stts` has one entry of
`(sample_count, 1024)`.

### X4 Samples

Each X4 sample has three tracks:

| Track | Handler | Codec | Shape |
| --- | --- | --- | --- |
| 1 | `vide` | `hvc1` / HEVC | lens video, 1920x1920, 29.97 fps, timescale 30000 |
| 2 | `vide` | `hvc1` / HEVC | lens video, 1920x1920, 29.97 fps, timescale 30000 |
| 3 | `soun` | `mp4a` / AAC-LC | stereo AAC, 48000 Hz, timescale 48000 |

This matches the existing X4 repair profile: two synchronized HEVC lens-video
tracks and one AAC track. The current X4 repair scanner is built around a
strict HEVC access-unit signature: five length-prefixed HEVC VCL NAL units per
sample, with a first-slice flag on the first NAL and no first-slice flag on the
next four. That rule is X4/HEVC specific and does not apply to the X3 H.264
samples.

## `moov` And Sample Table Differences

The X3 samples have this `moov` child order:

```text
mvhd
udta
trak
trak
```

The X4 samples have this `moov` child order:

```text
mvhd
udta
trak
trak
trak
```

The X3 video sample table order is:

```text
stsd
stts
stsc
stsz
co64
stss
```

The X3 audio sample table order is:

```text
stsd
stts
stsc
stsz
co64
```

The existing X4 format notes list video `stss` before `stsc`. This is a
compatibility detail worth correcting: the observed X4 video sample table order
is:

```text
stsd
stts
ctts
stsc
stsz
stco
stss
```

The observed X4 audio sample table order is:

```text
stsd
stts
stsc
stsz
stco
```

The X3 samples use `co64`; the observed X4 samples use `stco`. This is probably
because the X4 samples are short enough for 32-bit chunk offsets. Large X4 files
may still require `co64`, so a repair writer should choose the table type from
actual offsets rather than hard-code it per model.

## Media Data Implications

For X3 recovery, the media scanner needs an AVC/H.264 parser:

- read `avcC` from the `avc1` sample description to learn the NAL length size
- detect MP4-style length-prefixed H.264 access units, not Annex B start codes
- build one video sample table, not two synchronized lens video tables
- keep the AAC path similar to X4 because both profiles use raw MP4 AAC without
  ADTS headers

For X4 recovery, the current HEVC scanner and dual-video-track reconstruction
remain model/profile-specific.

## Insta360 Trailer Comparison

The trailer footer is shared:

```text
uint32 little-endian trailer_size
uint32 little-endian value 3
ASCII "8db42d694ccc418790edff439fe026bf"
```

The X3 samples all contain these trailer records in backward scan order:

| Record ID | X3 observed meaning |
| --- | --- |
| `0x101` | maker notes: serial, model, firmware, lens parameters |
| `0x200` | preview image / preview data |
| `0x300` | accelerometer and angular velocity |
| `0x400` | exposure time series |
| `0x900` | unknown X3 metadata |
| `0xa00` | unknown short metadata block |

The X4 samples contain a newer-style trailer with a final `0x000` directory
table. The directory table stores shortened IDs, but the actual record footers
use the full IDs:

| Actual record ID | X4 observed meaning |
| --- | --- |
| `0x000` | directory table, 250 bytes |
| `0x101` | maker notes: serial, model, firmware, lens parameters |
| `0x200` | preview image / preview data |
| `0x300` | accelerometer and angular velocity |
| `0x400` | exposure time series |
| `0x900` | unknown timed/camera metadata |
| `0xa00` | unknown short metadata block |
| `0xb00` | unknown variable-size X4 metadata |
| `0x1600` | unknown large X4 metadata, 8,306,836 bytes in all samples |

The X3 maker notes identify all X3 samples as:

```text
Model: Insta360 X3
Firmware: v1.1.6_build1
SerialNumber: IAQEB2309KKK7Y
```

The X4 maker notes identify all X4 samples as:

```text
Model: Insta360 X4
Firmware: v1.9.21_build5
SerialNumber: IBMEA2405YQG84
```

On both observed model sets, record `0x300` uses 20-byte samples:

```text
uint64 little-endian timecode
uint16 little-endian accel_x_raw
uint16 little-endian accel_y_raw
uint16 little-endian accel_z_raw
uint16 little-endian gyro_x_raw
uint16 little-endian gyro_y_raw
uint16 little-endian gyro_z_raw
```

Channel conversion:

```text
value = (raw_uint16 - 0x8000) / 1000
```

Time normalization:

```text
relative_seconds = (timecode - first_timecode) / 1_000_000
```

Observed X3 IMU rate is consistently about 996 Hz. Observed X4 IMU rate is
consistently about 1003 Hz. ExifTool's parser also supports a 56-byte `0x300`
record with six little-endian doubles after the timecode; keep this fallback
for other Insta360 variants even though both local X3 and X4 sets use 20-byte
records.

## X3 Sample Summary

| File | Video frames | AAC samples | Duration | Bitrate | Trailer size | IMU samples |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `VID_20251011_164332_00_013.insv` | 1,245 | 1,947 | 41.542 s | 52.86 Mbps | 1,854,836 | 45,216 |
| `VID_20251011_180010_00_015.insv` | 10,204 | 15,959 | 340.473 s | 50.44 Mbps | 7,796,389 | 343,456 |
| `VID_20251013_121252_00_018.insv` | 10,669 | 16,686 | 355.989 s | 50.61 Mbps | 8,137,477 | 358,624 |
| `VID_20251013_130438_00_019.insv` | 7,824 | 12,237 | 261.061 s | 50.32 Mbps | 6,191,429 | 264,048 |
| `VID_20251013_173534_00_022.insv` | 3,572 | 5,586 | 119.186 s | 50.90 Mbps | 3,277,460 | 122,288 |
| `VID_20251013_173750_00_023.insv` | 775 | 1,212 | 25.859 s | 52.31 Mbps | 1,306,467 | 29,408 |
| `VID_20251013_174204_00_024.insv` | 9,991 | 15,626 | 333.366 s | 50.51 Mbps | 7,718,725 | 336,192 |

## X4 Sample Summary

Each X4 row has the listed frame count in both HEVC video tracks.

| File | Video frames | AAC samples | Duration | Bitrate | Trailer size | IMU samples |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `VID_20260427_162323_00_551.insv` | 992 | 1,550 | 33.100 s | 47.18 Mbps | 12,533,194 | 33,616 |
| `VID_20260427_162532_00_552.insv` | 775 | 1,210 | 25.859 s | 48.72 Mbps | 12,408,002 | 26,256 |
| `VID_20260427_162754_00_553.insv` | 578 | 902 | 19.286 s | 51.18 Mbps | 12,144,159 | 19,648 |
| `VID_20260427_163008_00_554.insv` | 752 | 1,174 | 25.092 s | 50.21 Mbps | 11,911,048 | 25,488 |
| `VID_20260427_163219_00_555.insv` | 894 | 1,397 | 29.830 s | 47.72 Mbps | 12,256,432 | 30,256 |
| `VID_20260427_163355_00_556.insv` | 854 | 1,334 | 28.495 s | 47.89 Mbps | 12,355,305 | 28,896 |
| `VID_20260427_163542_00_557.insv` | 697 | 1,088 | 23.257 s | 50.20 Mbps | 12,057,277 | 23,648 |
| `VID_20260427_164223_00_560.insv` | 556 | 868 | 18.552 s | 51.17 Mbps | 11,854,267 | 18,928 |
| `VID_20260427_172613_00_561.insv` | 853 | 1,332 | 28.462 s | 47.65 Mbps | 12,243,728 | 28,864 |
| `VID_20260427_172833_00_562.insv` | 570 | 890 | 19.019 s | 51.90 Mbps | 11,801,459 | 19,392 |
| `VID_20260427_185946_00_563.insv` | 891 | 1,392 | 29.730 s | 47.31 Mbps | 12,573,071 | 30,128 |
| `VID_20260427_190246_00_564.insv` | 784 | 1,225 | 26.159 s | 48.16 Mbps | 12,668,748 | 26,560 |
| `VID_20260427_190526_00_565.insv` | 634 | 990 | 21.154 s | 49.83 Mbps | 11,971,284 | 21,552 |
| `VID_20260427_190727_00_566.insv` | 686 | 1,071 | 22.890 s | 47.16 Mbps | 11,788,093 | 23,280 |
| `VID_20260427_190921_00_567.insv` | 752 | 1,174 | 25.092 s | 47.20 Mbps | 11,787,041 | 25,488 |
| `VID_20260427_191138_00_568.insv` | 812 | 1,268 | 27.094 s | 46.19 Mbps | 12,338,362 | 27,488 |

## Repair Tool Consequences

The current `insv_repair.py` is intentionally X4-shaped:

- it builds `hvc1` sample descriptions
- it assumes two 1920x1920 video tracks
- it scans for X4 HEVC samples using the five-NAL pattern
- it reconstructs a three-track `moov`

For X3 support, add a separate profile instead of loosening the X4 scanner:

- detect model/profile from `0x101`, track count, and sample entry codec
- use an AVC/H.264 access-unit scanner for `avc1`
- reconstruct a two-track `moov`
- preserve or intentionally rebuild the `free` box before the trailer
- continue using the same trailer footer and `0x300` parser infrastructure

The X4 path also needs one update from the observed samples: trailer walking
must support the `0x000` directory table, because otherwise it will miss records
such as `0x300`, `0x400`, `0x900`, `0xb00`, and `0x1600`.

The clean architectural split is: shared MP4/trailer utilities, plus separate
X3 AVC and X4 HEVC media profiles.
