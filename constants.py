"""
constants.py - Shared constants and namedtuples for Insta360 INSV repair modules
"""

from collections import namedtuple

# Container atom types
CONTAINER_ATOMS = {b'moov', b'trak', b'mdia', b'minf', b'stbl', b'dinf',
                   b'edts', b'udta', b'mvex', b'sinf', b'schi'}

# HEVC NAL unit types
HEVC_NAL_VPS = 32
HEVC_NAL_SPS = 33
HEVC_NAL_PPS = 34
HEVC_NAL_AUD = 35
HEVC_NAL_IDR_W_RADL = 19
HEVC_NAL_IDR_N_LP = 20
HEVC_NAL_CRA = 21
HEVC_NAL_TRAIL_N = 0
HEVC_NAL_TRAIL_R = 1
HEVC_NAL_TSA_N = 2
HEVC_NAL_TSA_R = 3
HEVC_NAL_STSA_N = 4
HEVC_NAL_STSA_R = 5
HEVC_NAL_RADL_N = 6
HEVC_NAL_RADL_R = 7
HEVC_NAL_RASL_N = 8
HEVC_NAL_RASL_R = 9
HEVC_NAL_BLA_W_LP = 16
HEVC_NAL_BLA_W_RADL = 17
HEVC_NAL_BLA_N_LP = 18
HEVC_NAL_SEI_PREFIX = 39
HEVC_NAL_SEI_SUFFIX = 40

HEVC_IDR_TYPES = {HEVC_NAL_IDR_W_RADL, HEVC_NAL_IDR_N_LP, HEVC_NAL_CRA,
                  HEVC_NAL_BLA_W_LP, HEVC_NAL_BLA_W_RADL, HEVC_NAL_BLA_N_LP}
HEVC_SLICE_TYPES = (set(range(0, 10)) | set(range(16, 22)))

# AAC ADTS sync word
AAC_SYNC = 0xFFF

# Insta360 X4 defaults
X4_VIDEO_WIDTH = 1920
X4_VIDEO_HEIGHT = 1920
X4_VIDEO_TIMESCALE = 30000
X4_VIDEO_SAMPLE_DELTA = 1001  # 29.97fps = 30000/1001
X4_AUDIO_TIMESCALE = 48000
X4_AUDIO_CHANNELS = 2
X4_AUDIO_SAMPLE_RATE = 48000

# Namedtuples
Atom = namedtuple('Atom', ['offset', 'size', 'type', 'header_size'])
FrameInfo = namedtuple('FrameInfo', ['offset', 'size', 'is_keyframe', 'track'])
SampleEntry = namedtuple('SampleEntry', ['offset', 'size', 'duration', 'is_sync'])