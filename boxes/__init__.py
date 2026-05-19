"""MP4 box implementations."""

from .base import MP4Box
from .ftyp import FileTypeBox
from .mvhd import MovieHeaderBox
from .tkhd import TrackHeaderBox
from .mdhd import MediaHeaderBox
from .moov import MovieBox
from .trak import TrackBox
from .free import FreeSpaceBox
from .mdat import MediaDataBox
from .edts import EditBox
from .elst import EditListBox
from .hdlr import HandlerBox
from .mdia import MediaBox
from .minf import MediaInformationBox
from .vmhd import VideoMediaHeaderBox
from .smhd import SoundMediaHeaderBox
from .dinf import DataInformationBox
from .dref import DataReferenceBox
from .url_ import DataEntryUrlBox
from .stbl import SampleTableBox
from .stsd import SampleDescriptionBox
from .hev1 import HEVCSampleEntry
from .hvc1 import HVC1SampleEntry
from .mp4a import MP4AudioSampleEntry
from .hvcc import HEVCConfigurationBox
from .btrt import BitRateBox
from .co64 import ChunkLargeOffsetBox
from .wide import WideBox
from .alis import DataEntryAliasBox
from .amba import AMBABox
from .nail import NailBox
from .insv import INSVFile
from .colr import ColourInformationBox
from .pasp import PixelAspectRatioBox
from .fiel import FieldHandlingBox
from .esds import ElementaryStreamDescriptorBox
from .stts import TimeToSampleBox
from .ctts import CompositionOffsetBox
from .stss import SyncSampleBox
from .stsc import SampleToChunkBox
from .stsz import SampleSizeBox
from .stco import ChunkOffsetBox


__all__ = [
    "MP4Box",
    "FileTypeBox",
    "MovieHeaderBox",
    "TrackHeaderBox",
    "MediaHeaderBox",
    "MovieBox",
    "TrackBox",
    "FreeSpaceBox",
    "MediaDataBox",
    "EditBox",
    "EditListBox",
    "HandlerBox",
    "MediaBox",
    "MediaInformationBox",
    "VideoMediaHeaderBox",
    "SoundMediaHeaderBox",
    "DataInformationBox",
    "DataReferenceBox",
    "DataEntryUrlBox",
    "SampleTableBox",
    "SampleDescriptionBox",
    "HEVCSampleEntry",
    "HVC1SampleEntry",
    "ChunkLargeOffsetBox",
    "WideBox",
    "DataEntryAliasBox",
    "AMBABox",
    "NailBox",
    "INSVFile",
    "MP4AudioSampleEntry",
    "HEVCConfigurationBox",
    "BitRateBox",
    "ColourInformationBox",
    "PixelAspectRatioBox",
    "FieldHandlingBox",
    "ElementaryStreamDescriptorBox",
    "TimeToSampleBox",
    "CompositionOffsetBox",
    "SyncSampleBox",
    "SampleToChunkBox",
    "SampleSizeBox",
    "ChunkOffsetBox",
]
