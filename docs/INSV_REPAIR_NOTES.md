# Insta360 X4 INSV repair notes

## What matters from untrunc

The local `untrunc reference/` source is useful mainly as a recovery strategy

Relevant untrunc ideas:

- Parse one or more healthy reference files first.  Keep their `moov`, track
  metadata, chunk offsets, sample sizes, keyframe tables, and codec sample
  descriptions as the model for the broken file.
- Do not greedily parse media until bytes stop looking valid.  Media payloads
  often contain false positives, especially raw audio and proprietary camera
  data.
- Learn recurring sample/chunk patterns from the reference.  Untrunc generates
  "mutual patterns" around known sample/chunk transitions, filters weak
  patterns, and uses those patterns while scanning the corrupt `mdat`.
- Treat unknown regions explicitly.  Untrunc can exclude or skip unknown bytes
  instead of letting them become bogus media samples.
- Save repaired files only after rewriting coherent sample tables.  The sample
  counts, chunk offsets, durations, and `mdat` size must agree or tools will
  report nonsense durations/bitrates.

Important source areas:

- `mp4.cpp::repair()` drives the repair loop over `mdat`.
- `mp4.cpp::wouldMatch()`, `tryMatch()`, and `tryChunkPrediction()` classify
  bytes as samples/chunks.
- `mp4.cpp::genDynPatterns()` and `mutual_pattern.cpp` build transition
  patterns from the healthy file.
- `track.cpp::writeToAtoms()` and related `save*` methods rewrite sample
  tables after recovery.
- `hvc1/nal.cpp` and `hvc1/nal-slice.cpp` validate MP4-style length-prefixed
  HEVC NALs and use `first_slice_segment_in_pic_flag` for frame boundaries.

The most relevant design point is that untrunc does not rely on a single codec
probe.  It keeps a candidate position, asks all tracks whether the bytes match
their expected sample type, checks predicted chunk order, and advances only
when the local evidence agrees with the learned reference structure.  For this
X4 repair, the equivalent simplified strategy is:

- Learn the exact X4 track order and codec descriptions from a good file.
- Learn the observed HEVC access-unit shape from references.
- Reject weak HEVC-looking matches instead of letting raw AAC or trailer bytes
  masquerade as video.
- Treat skipped/unknown bytes as non-media unless they sit between two good
  video access units, where they are likely raw AAC.
- Use coherent output tables as the final truth: sample counts, durations,
  chunk offsets, and copied `mdat` bytes must agree.

## X4 structure confirmed from references

The healthy X4 `.insv` files in `reference/` have this top-level layout:

```text
ftyp
mdat
moov
Insta360 trailer
```

The broken file has:

```text
ftyp
wide
mdat size=0 / extends to EOF
```

For recordings that stopped badly, `mdat` may swallow bytes that are actually
the trailer or possibly a hidden `moov`.  The trailer is not an MP4 box.  It has
a footer:

```text
uint32 little-endian trailer_size
uint32 little-endian 3
ASCII "8db42d694ccc418790edff439fe026bf"
```

So the reliable trailer start is:

```text
file_size - trailer_size
```

For `broken/VID_20260509_161202_00_660.insv`, that gives `0xc4c00000`.
Media scanning must stop before that offset, and the trailer bytes from that
offset to EOF should be appended after the rebuilt `moov`.

The broken file did not contain a complete valid hidden `moov` that could be
salvaged directly, so the repair path must reconstruct sample tables from the
media payload and then preserve the original trailer suffix.

## X4 sample pattern learned from references

All three healthy references show the same basic media shape:

- Track 1: `hvc1`, 1920x1920, 30000 timescale, 29.97 fps.
- Track 2: `hvc1`, 1920x1920, 30000 timescale, 29.97 fps.
- Track 3: `mp4a`, 48 kHz stereo, 48000 timescale.
- `samples_per_chunk = 1` for all tracks.
- Video sample duration is 1001 ticks.
- Audio sample duration is 1024 ticks.

Most importantly for scanning:

- Each X4 video sample observed so far is exactly five MP4-style length-prefixed
  HEVC VCL NAL units.
- The five NALs have the same NAL type.
- The first NAL has `first_slice_segment_in_pic_flag = 1`.
- The following four NALs have `first_slice_segment_in_pic_flag = 0`.
- Raw AAC has no ADTS header, so audio frames cannot be identified by syncword.
  Non-video gaps between two detected video samples are treated as AAC samples.

This explains the original repair failure: the old scanner consumed consecutive
valid HEVC NALs until it hit invalid data, so it merged multiple video samples
into one giant sample, then wandered into audio/trailer bytes.  The repaired
file therefore indexed only a few seconds of video while copying gigabytes of
payload, causing the absurd bitrate in Insta360 Studio.

## Repair rules for this project

- Prefer reference mode.  Use the reference `stsd` boxes for `hvc1` and `mp4a`
  rather than rebuilding codec descriptions by hand.
- Stop `mdat` at the footer-derived trailer start, not EOF.
- Detect video samples with the strict five-NAL X4 rule.
- Split non-video gaps before the next video sample into raw AAC chunks.
- Alternate detected video samples between the two video tracks.
- Put `moov` children in reference order: `mvhd`, `udta`, `trak`, `trak`,
  `trak`.
- Append the original Insta360 trailer after `moov` unchanged.
- Validate output by checking:
  - Track durations are close to the Insta360 repaired MP4 duration.
  - Video sample counts are close on both lens tracks.
  - ExifTool reports the trailer after `moov`.
  - Bitrate is roughly in the reference range, not gigabits per second.

## Current repair result for VID_20260509_161202_00_660

Using `reference/VID_20260509_150725_00_656.insv`, the current scanner recovers:

- Track 1: 21202 HEVC samples, 707.440067 seconds.
- Track 2: 21202 HEVC samples, 707.440067 seconds.
- Track 3: 33161 AAC samples, 707.434667 seconds.
- Repaired file bitrate: about 37.3 Mbps by ExifTool.
- `moov` child order: `mvhd`, `udta`, `trak`, `trak`, `trak`.
- Trailer: appended after `moov`; footer magic is present at EOF.

The remaining important caveat is that raw AAC frame boundaries are estimated
inside each audio gap because the X4 stores AAC without ADTS sync headers.  The
scanner now trims the audio table to the recovered video duration so trailing
non-media bytes do not inflate the movie duration or bitrate.
