# Insta360 X4 INSV File Format

This document describes the `.insv` files observed from Insta360 X4 reference
recordings in this repository. `.insv` is not a wholly separate container
format; it is an ISO Base Media File Format / QuickTime-style file with an
Insta360 extension and a proprietary trailer.

The details below are based on the X4 files in `reference/` and the damaged
file in `broken/`. Other Insta360 models, firmware versions, or recording modes
may differ.

## High-Level Layout

Healthy X4 files use this top-level structure:

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

The `ftyp` box identifies the file as MP4/QuickTime-compatible media. The X4
reference files observed here use:

- major brand: `avc1`
- compatible brands: `avc1`, `isom`

The actual video samples are HEVC and are described by `hvc1` sample entries in
the track metadata.

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
In the observed X4 reference files, the child order is:

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

## Tracks

The observed X4 files contain three tracks.

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

For the X4 files observed in this repository, each video sample has this strong
pattern:

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

The X4 files observed here use `co64`, not 32-bit `stco`, because files can be
large enough that 64-bit offsets are safer.

## `udta`

The `udta` box inside `moov` contains Insta360-specific metadata. Observed
children include proprietary data such as:

- `AMBA`
- `nail`
- `free`

For repair, copying `udta` from a healthy reference file gives the rebuilt
`moov` a structure closer to a camera-authored X4 file. Some values inside that
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
extends to the end of the file. For these damaged X4 files, that can be wrong:
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

