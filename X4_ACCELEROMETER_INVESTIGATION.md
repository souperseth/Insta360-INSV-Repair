# Insta360 X4 Accelerometer Investigation

This report documents the accelerometer and angular-velocity data observed in
the X4 files in `sample_insvs_x4/`, then compares those findings with the X3
files in `sample_insvs_x3/`.

The investigation used ExifTool 13.55, `ffprobe`, and direct binary parsing of
the Insta360 trailer records.

## Executive Summary

The X4 samples store accelerometer and angular-velocity data in trailer record
`0x300`, the same record ID used by the X3 samples. In all observed X4 files,
record `0x300` is made of 20-byte samples:

```text
uint64 little-endian timecode
uint16 little-endian accel_x_raw
uint16 little-endian accel_y_raw
uint16 little-endian accel_z_raw
uint16 little-endian gyro_x_raw
uint16 little-endian gyro_y_raw
uint16 little-endian gyro_z_raw
```

Channel conversion matches ExifTool:

```text
value = (raw_uint16 - 0x8000) / 1000
```

The resulting first three values are ExifTool's `Accelerometer`; the final
three values are `AngularVelocity`. ExifTool notes that the accelerometer units
are usually `g`, but it does not confirm the camera-axis convention.

For timestamps, the practical export form is:

```text
relative_seconds = (timecode - first_timecode) / 1_000_000
```

The X4 samples average about 1003.34 Hz. The timecode deltas are mostly 1000
microseconds, with periodic shorter deltas around 947 microseconds. No
non-monotonic timestamps were observed.

## X4 Trailer Layout

The X4 files use the same trailer footer magic as the X3 files:

```text
uint32 little-endian trailer_size
uint32 little-endian value 3
ASCII "8db42d694ccc418790edff439fe026bf"
```

Unlike the X3 sample set, the X4 trailer uses a final `0x000` directory table.
A parser that only walks records sequentially backward will stop at the
directory and miss most of the actual X4 records.

The directory table entries are 10 bytes:

```text
uint16 little-endian table_id
uint32 little-endian record_data_size
uint32 little-endian record_data_offset_from_trailer_start
```

The table IDs are shortened for many records. For example, table ID `0x3`
points to a record whose actual footer ID is `0x300`. Code should seek to the
directory entry's data offset, then verify the real six-byte footer after the
record data instead of trusting the shortened table ID alone.

All observed X4 files contain these actual record IDs:

| Record ID | Observed size pattern | Notes |
| --- | ---: | --- |
| `0x000` | 250 bytes | Directory table at the end of the trailer |
| `0x101` | 1,907 bytes | Maker notes: model, firmware, serial, lens parameters |
| `0x200` | 1,228,840 bytes | Preview image / preview data |
| `0x300` | variable, multiple of 20 | Accelerometer and angular velocity |
| `0x400` | variable, multiple of 16 | Exposure time series |
| `0x900` | variable | Unknown timed/camera metadata |
| `0xa00` | 35 bytes | Unknown short metadata block |
| `0xb00` | variable | Unknown X4 metadata |
| `0x1600` | 8,306,836 bytes | Unknown large X4 metadata block |

The X4 maker notes identify the sample set as:

```text
Model: Insta360 X4
Firmware: v1.9.21_build5
SerialNumber: IBMEA2405YQG84
```

## X4 Accelerometer Record Findings

Every X4 `0x300` record is a multiple of 20 bytes. No observed X4 file used the
56-byte double-precision form that ExifTool supports for some Insta360 files.
Keep that 56-byte fallback in parser code, but the local X4 evidence points to
the 20-byte form.

The X4 IMU stream spans slightly longer than the video stream, usually by about
0.30 to 0.41 seconds in these short clips. That means export code should not
assume one IMU row per video frame or exact video-duration alignment.

Aggregate X4 statistics:

| Metric | Value |
| --- | ---: |
| Files examined | 16 |
| Total `0x300` samples | 409,488 |
| Sample stride | 20 bytes |
| Rate range | 1003.28-1003.43 Hz |
| Mean rate | 1003.34 Hz |
| Timestamp delta mode | 1000 microseconds |
| Timestamp delta correction band | about 933-951 microseconds |
| Non-monotonic timestamps | 0 |
| Accelerometer min | `-2.657 -2.581 -2.884` |
| Accelerometer max | `0.829 3.624 3.937` |
| Angular velocity min | `-7.716 -4.069 -3.461` |
| Angular velocity max | `8.111 3.573 4.717` |

## X4 Per-File Summary

| File | Video duration | Trailer size | `0x300` bytes | IMU samples | IMU span | Rate | First accelerometer | First angular velocity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `VID_20260427_162323_00_551.insv` | 33.100 s | 12,533,194 | 672,320 | 33,616 | 33.505 s | 1003.28 Hz | `-0.956 0.078 0.042` | `0.529 0.258 -0.673` |
| `VID_20260427_162532_00_552.insv` | 25.859 s | 12,408,002 | 525,120 | 26,256 | 26.169 s | 1003.29 Hz | `-0.944 -0.338 0.038` | `0.542 -0.269 0.611` |
| `VID_20260427_162754_00_553.insv` | 19.286 s | 12,144,159 | 392,960 | 19,648 | 19.582 s | 1003.30 Hz | `-0.975 0.007 -0.260` | `0.655 -0.001 -0.250` |
| `VID_20260427_163008_00_554.insv` | 25.092 s | 11,911,048 | 509,760 | 25,488 | 25.403 s | 1003.30 Hz | `-0.966 -0.258 -0.060` | `-1.466 -0.165 -0.981` |
| `VID_20260427_163219_00_555.insv` | 29.830 s | 12,256,432 | 605,120 | 30,256 | 30.155 s | 1003.31 Hz | `-1.053 -0.347 0.251` | `-0.832 -0.064 -0.604` |
| `VID_20260427_163355_00_556.insv` | 28.495 s | 12,355,305 | 577,920 | 28,896 | 28.800 s | 1003.31 Hz | `-0.932 -0.315 -0.033` | `-1.125 -0.222 -0.674` |
| `VID_20260427_163542_00_557.insv` | 23.257 s | 12,057,277 | 472,960 | 23,648 | 23.569 s | 1003.31 Hz | `-0.955 -0.248 -0.231` | `-0.176 -0.508 0.093` |
| `VID_20260427_164223_00_560.insv` | 18.552 s | 11,854,267 | 378,560 | 18,928 | 18.865 s | 1003.31 Hz | `-0.977 -0.418 -0.047` | `-0.104 -0.232 -0.343` |
| `VID_20260427_172613_00_561.insv` | 28.462 s | 12,243,728 | 577,280 | 28,864 | 28.767 s | 1003.34 Hz | `-0.942 0.164 0.133` | `1.656 0.688 -0.217` |
| `VID_20260427_172833_00_562.insv` | 19.019 s | 11,801,459 | 387,840 | 19,392 | 19.327 s | 1003.31 Hz | `-0.876 -0.438 0.104` | `-0.212 -0.490 0.050` |
| `VID_20260427_185946_00_563.insv` | 29.730 s | 12,573,071 | 602,560 | 30,128 | 30.025 s | 1003.38 Hz | `-0.930 -0.092 0.105` | `-0.256 -0.168 -0.180` |
| `VID_20260427_190246_00_564.insv` | 26.159 s | 12,668,748 | 531,200 | 26,560 | 26.469 s | 1003.40 Hz | `-1.029 -0.082 -0.008` | `-0.501 -0.163 -0.176` |
| `VID_20260427_190526_00_565.insv` | 21.154 s | 11,971,284 | 431,040 | 21,552 | 21.478 s | 1003.41 Hz | `-1.062 -0.020 0.006` | `-0.084 0.084 0.331` |
| `VID_20260427_190727_00_566.insv` | 22.890 s | 11,788,093 | 465,600 | 23,280 | 23.200 s | 1003.42 Hz | `-0.953 -0.097 0.034` | `-0.510 -0.280 -0.604` |
| `VID_20260427_190921_00_567.insv` | 25.092 s | 11,787,041 | 509,760 | 25,488 | 25.400 s | 1003.42 Hz | `-0.891 -0.142 -0.547` | `0.301 0.219 0.126` |
| `VID_20260427_191138_00_568.insv` | 27.094 s | 12,338,362 | 549,760 | 27,488 | 27.393 s | 1003.43 Hz | `-0.970 0.006 0.047` | `-0.115 0.017 0.009` |

## X4 vs X3 Accelerometer Comparison

| Area | X3 findings | X4 findings |
| --- | --- | --- |
| Trailer traversal | Sequential backward scan finds all observed records | Must read `0x000` directory table to find all records |
| Maker notes | `Insta360 X3`, firmware `v1.1.6_build1` | `Insta360 X4`, firmware `v1.9.21_build5` |
| Accelerometer record ID | `0x300` | `0x300` |
| Observed sample stride | 20 bytes | 20 bytes |
| Observed 56-byte samples | None | None |
| Fields | timecode, accel XYZ, angular velocity XYZ | Same |
| Channel conversion | `(raw - 0x8000) / 1000` | Same |
| Files examined | 7 | 16 |
| Total samples examined | 1,499,232 | 409,488 |
| Rate range | 996.27-996.43 Hz | 1003.28-1003.43 Hz |
| Mean rate | 996.36 Hz | 1003.34 Hz |
| Delta pattern | mostly 1000 us with longer corrections around 1058 us | mostly 1000 us with shorter corrections around 947 us |
| Non-monotonic timestamps | 0 | 0 |
| Extra trailer records | `0x900`, `0xa00` beyond common records | `0x900`, `0xa00`, `0xb00`, `0x1600` beyond common records |

The accelerometer payload itself is therefore very similar between the observed
X3 and X4 files. The main X4-specific parsing risk is the trailer directory
table, not the `0x300` sample layout.

## Parser Recommendations

- Detect the trailer start from the footer size and magic.
- Read the final trailer record. If it is `0x000`, parse it as a directory
  table and verify actual record IDs from each record's footer.
- Decode actual record ID `0x300` as 20-byte records when the record length is
  divisible by 20 and the first sample does not match ExifTool's 56-byte
  heuristic.
- Keep ExifTool's 56-byte fallback for other Insta360 files.
- Export both raw and converted values if possible; raw values are useful for
  later calibration or axis-convention checks.
- Use a zero-based relative timestamp derived from the first IMU timecode.
- Do not assume IMU duration equals video duration exactly.
