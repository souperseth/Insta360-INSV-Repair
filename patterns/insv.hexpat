#pragma description MPEG-4 / QuickTime container (MP4/MOV/.insv)
// Supports Insta360 .insv files: hvc1/HEVC video, mp4a/AAC audio,
// co64 chunk offsets, edts/elst edit lists, ctts composition offsets,
// AMBA/nail/free udta boxes.

#pragma endian big

#pragma MIME audio/mp4
#pragma MIME video/mp4
#pragma MIME application/mp4

import std.io;
import std.mem;
import std.string;

fn to_string(auto var) {
    return str(var);
};

fn format_string(auto string) {
    return string.value;
};

fn format_fixed32(auto fp32) {
    return fp32.integer + fp32.fraction / 65536.0;
};

fn format_fixed16(auto fp16) {
    return fp16.integer + fp16.fraction / 256.0;
};

struct FixedPoint16 {
    u8 integer;
    u8 fraction;
} [[format("format_fixed16")]];

struct FixedPoint32 {
    u16 integer;
    u16 fraction;
} [[format("format_fixed32")]];

struct string {
    char value[std::mem::find_sequence_in_range(0, $, std::mem::size(), 0x00) - $];
} [[sealed, format("format_string")]];

struct BaseBox {
    u64 boxSize = 0;
    u64 startOffset = $;
    u64 endOffset = 0;

    u32 size;
    char type[4];

    // Calculate the size of the current box
    // 1. If the size is equal to 1 -> the box size is equal to 'largeSize' attribute.
    // 2. If the size is equal to 0 -> The box extends to the end of the file.
    // 3. Otherwise, size is equal to the 'size' attribute.
    if (this.size == 1) {
        u64 largeSize;
        boxSize = largeSize;
        endOffset = startOffset + boxSize;
    } else if (this.size == 0) {
        boxSize = std::mem::size() - startOffset;
        endOffset = std::mem::size();
    } else {
        boxSize = size;
        endOffset = startOffset + boxSize;
    }

    // Clamp endOffset to the file boundary so that array expansions like
    // u8 data[while($ < endOffset)] never try to read past available data.
    if (endOffset > std::mem::size()) {
        endOffset = std::mem::size();
    }

    if (this.type == "uuid") {
        char usertype[16];
    }
};

struct FullBox : BaseBox {
    u8 version;
    u24 flags;
};

struct UnknownBox : BaseBox {
    u8 unk[endOffset - $];
};

// ---------------------------------------------------------------------------
// ftyp
// ---------------------------------------------------------------------------

using brand = u32 [[format("to_string")]];
struct FileTypeBox : BaseBox {
    brand major_brand;
    u32 minor_version;
    brand compatible_brands[(endOffset - $) / sizeof(brand)];
};

// ---------------------------------------------------------------------------
// mvhd
// ---------------------------------------------------------------------------

struct MovieHeaderBox : FullBox {
    if (this.version == 1) {
        u64 creation_time;
        u64 modification_time;
        u32 timescale;
        u64 duration;
    } else {  // version == 0
        u32 creation_time;
        u32 modification_time;
        u32 timescale;
        u32 duration;
    }
    FixedPoint32 rate;
    FixedPoint16 volume;
    u8 reserved[10] [[sealed]];
    u32 matrix[9];
    u32 preview_time;
    u32 preview_duration;
    u32 poster_time;
    u32 selection_time;
    u32 selection_duration;
    u32 current_time;
    u32 next_track_id;
};

// ---------------------------------------------------------------------------
// tkhd
// ---------------------------------------------------------------------------

struct TrackHeaderBox : FullBox {
    if (this.version == 1) {
        u64 creation_time;
        u64 modification_time;
        u32 track_id;
        u32 reserved;
        u64 duration;
    } else {  // version == 0
        u32 creation_time;
        u32 modification_time;
        u32 track_id;
        u32 reserved;
        u32 duration;
    }
    u32 reserved_2[2] [[sealed]];
    s16 layer;
    s16 alternate_group;
    s16 volume;
    u16 reserved_3;
    s32 matrix[9];
    FixedPoint32 width;
    FixedPoint32 height;
};

// ---------------------------------------------------------------------------
// mdhd
// ---------------------------------------------------------------------------

struct MediaHeaderBox : FullBox {
    if (this.version == 1) {
        u64 creation_time;
        u64 modification_time;
        u32 timescale;
        u64 duration;
    } else { // version==0
        u32 creation_time;
        u32 modification_time;
        u32 timescale;
        u32 duration;
    }
    u16 language [[comment("ISO-639-2/T language code")]];
    u16 quality;
};

// ---------------------------------------------------------------------------
// hdlr
// ---------------------------------------------------------------------------

struct HandlerBox : FullBox {
    u32 component_type;
    char handler_type[4];
    u32 reserved[3];
    char name[endOffset - $];
};

// ---------------------------------------------------------------------------
// vmhd / smhd
// ---------------------------------------------------------------------------

struct VideoMediaHeaderBox : FullBox {
    u16 graphicsmode;
    u16 opcolor[3];
};

struct SoundMediaHeaderBox : FullBox {
    s16 balance;
    u16 reserved;
};

// ---------------------------------------------------------------------------
// dinf / dref / url  / alis
// ---------------------------------------------------------------------------

struct DataEntryUrlBox : FullBox {
    // flags & 0x000001 == 1 means self-contained (no location string)
    if ((this.flags & 0x000001) == 0) {
        char location[endOffset - $];
    }
};

struct DataEntryAliasBox : FullBox {
    // Alias entries: flags & 0x000001 == 1 means self-contained
    if ((this.flags & 0x000001) == 0) {
        u8 alias_data[endOffset - $];
    }
};

struct DataEntryUrnBox : FullBox {
    string name;
    if ($ < endOffset) {
        string location;
    }
};

struct SubDataEntryBox {
    u32 type = std::mem::read_unsigned($ + 4, 4, std::mem::Endian::Big);

    match (str(type)) {
        ("url "): DataEntryUrlBox   box [[inline]];
        ("alis"): DataEntryAliasBox box [[inline]];
        ("urn "): DataEntryUrnBox   box [[inline]];
        (_):      UnknownBox        box [[inline]];
    }
} [[name(std::format("DataEntry({})", box.type))]];

struct DataReferenceBox : FullBox {
    u32 entry_count;
    SubDataEntryBox data_entries[this.entry_count];
};

struct SubDataInformationBox {
    u32 type = std::mem::read_unsigned($ + 4, 4, std::mem::Endian::Big);

    match (str(type)) {
        ("dref"): DataReferenceBox box [[inline]];
        (_): UnknownBox box [[inline]];
    }
} [[name(std::format("DataInformationBox({})", box.type))]];

struct DataInformationBox : BaseBox {
    SubDataInformationBox box[while($ < endOffset)] [[inline]];
};

// ---------------------------------------------------------------------------
// elst / edts
// ---------------------------------------------------------------------------

struct EditListEntry64 {
    u64 segment_duration;
    s64 media_time;
    s16 media_rate_integer;
    s16 media_rate_fraction;
};

struct EditListEntry32 {
    u32 segment_duration;
    s32 media_time;
    s16 media_rate_integer;
    s16 media_rate_fraction;
};

struct EditListBox : FullBox {
    u32 entry_count;
    if (this.version == 1) {
        EditListEntry64 entry_list[this.entry_count];
    } else {  // version 0
        EditListEntry32 entry_list[this.entry_count];
    }
};

struct SubEditBox {
    u32 type = std::mem::read_unsigned($ + 4, 4, std::mem::Endian::Big);

    match (str(type)) {
        ("elst"): EditListBox box [[inline]];
        (_): UnknownBox box [[inline]];
    }
} [[name(std::format("EditBox({})", box.type))]];

struct EditBox : BaseBox {
    SubEditBox box[while($ < endOffset)] [[inline]];
};

// ---------------------------------------------------------------------------
// stbl children
// ---------------------------------------------------------------------------

// hvcC (HEVC Decoder Configuration Record) inside hvc1/hev1
struct HEVCNALArray {
    u8  array_completeness_and_nal_unit_type;
    u8  nal_unit_type = array_completeness_and_nal_unit_type & 0x3F;
    u16 num_nalus;
    // Each NAL: u16 length + data
    u8  nalu_data[while($ < $ + 0)] ; // placeholder - parsed manually below
};

struct HEVCDecoderConfigRecord : BaseBox {
    u8   configurationVersion;
    u8   general_profile_space_tier_idc;
    u32  general_profile_compatibility_flags;
    u8   general_constraint_indicator[6];
    u8   general_level_idc;
    u16  min_spatial_segmentation_idc;    // 0xF000 | value
    u8   parallelismType;                 // 0xFC | value
    u8   chromaFormat;                    // 0xFD | value
    u8   bitDepthLumaMinus8;              // 0xF8 | value
    u8   bitDepthChromaMinus8;            // 0xF8 | value
    u16  avgFrameRate;
    u8   misc;                            // constantFrameRate(2) | numTemporalLayers(3) | lengthSizeMinusOne(2)
    u8   numOfArrays;
    u8   array_data[endOffset - $];
};

// hvc1 / hev1 visual sample entry
struct Hvc1Box : BaseBox {
    u48 reserved;
    u16 data_reference_index;
    u16 pre_defined;
    u16 reserved2;
    u32 pre_defined2[3];
    u16 width;
    u16 height;
    FixedPoint32 horizontal_resolution;
    FixedPoint32 vertical_resolution;
    u32 reserved3;
    u16 frame_count;
    char compressor_name[32];
    u16 depth;
    s16 pre_defined3;
    // Child boxes (hvcC, etc.)
    u8 codec_config[endOffset - $];
};

// avc1 visual sample entry (H.264)
struct Avc1Box : BaseBox {
    u48 reserved;
    u16 data_reference_index;
    u16 version;
    u16 revision_level;
    u32 max_packet_size;
    if (this.version == 0 || this.version == 1) {
        u32 temporal_quality;
        u32 spatial_quality;
        u16 width;
        u16 height;
        FixedPoint32 horizontal_resolution;
        FixedPoint32 vertical_resolution;
        u32 data_size;
        u16 frame_count;
        char compressor_name[32];
        u16 depth;
        s16 color_table_id;
    }
    u8 codec_config[endOffset - $];
};

// esds (Elementary Stream Descriptor) inside mp4a
struct EsdsBox : FullBox {
    u8 descriptor_data[endOffset - $];
};

// mp4a audio sample entry
struct Mp4aBox : BaseBox {
    u48 reserved;
    u16 data_reference_index;
    u16 version;
    u16 revision_level;
    u32 max_packet_size;
    if (this.version == 0) {
        u16 num_audio_channels;
        u16 sample_size;
        u16 compression_id;
        u16 packet_size;
        FixedPoint32 sample_rate;
    } else if (this.version == 1) {
        u16 num_audio_channels;
        u16 sample_size;
        u16 compression_id;
        u16 packet_size;
        FixedPoint32 sample_rate;
        u32 samples_per_packet;
        u32 bytes_per_packet;
        u32 bytes_per_frame;
        u32 bytes_per_sample;
    }
    // Child boxes (esds, etc.)
    u8 codec_config[endOffset - $];
};

struct SubSampleDescriptionBox {
    u32 type = std::mem::read_unsigned($ + 4, 4, std::mem::Endian::Big);

    match (str(type)) {
        ("hvc1"): Hvc1Box box [[inline]];
        ("hev1"): Hvc1Box box [[inline]];
        ("avc1"): Avc1Box box [[inline]];
        ("mp4a"): Mp4aBox box [[inline]];
        (_): UnknownBox box [[inline]];
    }
} [[name(std::format("SampleEntry({})", box.type))]];

struct SampleDescriptionBox : FullBox {
    u32 entry_count;
    SubSampleDescriptionBox box[while($ < endOffset)] [[inline]];
};

// stts
struct SampleTimeToSampleEntry {
    u32 sample_count;
    u32 sample_delta;
};

struct SampleTimeToSampleBox: FullBox {
    u32 entry_count;
    SampleTimeToSampleEntry entry_list[this.entry_count];
};

// ctts (composition time offset)
struct CompositionOffsetEntryV0 {
    u32 sample_count;
    u32 sample_offset;
};

struct CompositionOffsetEntryV1 {
    u32 sample_count;
    s32 sample_offset;
};

struct CompositionOffsetBox: FullBox {
    u32 entry_count;
    if (this.version == 0) {
        CompositionOffsetEntryV0 entry_list[this.entry_count];
    } else if (this.version == 1) {
        CompositionOffsetEntryV1 entry_list[this.entry_count];
    }
};

// stsc
struct SampleToChunkEntry {
    u32 first_chunk;
    u32 samples_per_chunk;
    u32 sample_description_index;
};

struct SampleToChunkBox: FullBox {
    u32 entry_count;
    SampleToChunkEntry entry_list[this.entry_count];
};

// stco (32-bit chunk offsets)
struct ChunkOffsetBox: FullBox {
    u32 entry_count;
    u32 chunk_offset[this.entry_count];
};

// co64 (64-bit chunk offsets)
struct ChunkLargeOffsetBox: FullBox {
    u32 entry_count;
    u64 chunk_offset[this.entry_count];
};

// stsz
struct SampleSizeBox: FullBox {
    u32 sample_size;
    u32 sample_count;
    if (this.sample_size == 0) {
        u32 entry_size[this.sample_count];
    }
};

// stss
struct SyncSampleBox: FullBox {
    u32 entry_count;
    u32 sample_number[this.entry_count];
};

struct SubSampleBoxTable {
    u32 type = std::mem::read_unsigned($ + 4, 4, std::mem::Endian::Big);

    match (str(type)) {
        ("stsd"): SampleDescriptionBox  box [[inline]];
        ("stts"): SampleTimeToSampleBox box [[inline]];
        ("ctts"): CompositionOffsetBox  box [[inline]];
        ("stsc"): SampleToChunkBox      box [[inline]];
        ("stco"): ChunkOffsetBox        box [[inline]];
        ("co64"): ChunkLargeOffsetBox   box [[inline]];
        ("stss"): SyncSampleBox         box [[inline]];
        ("stsz"): SampleSizeBox         box [[inline]];
        (_): UnknownBox box [[inline]];
    }
} [[name(std::format("SampleTable({})", box.type))]];

struct SampleBoxTable : BaseBox {
    SubSampleBoxTable box[while($ < endOffset)] [[inline]];
};

// ---------------------------------------------------------------------------
// minf
// ---------------------------------------------------------------------------

struct SubMediaInformationBox {
    u32 type = std::mem::read_unsigned($ + 4, 4, std::mem::Endian::Big);

    match (str(type)) {
        ("vmhd"): VideoMediaHeaderBox box [[inline]];
        ("smhd"): SoundMediaHeaderBox box [[inline]];
        ("hdlr"): HandlerBox          box [[inline]];
        ("dinf"): DataInformationBox  box [[inline]];
        ("stbl"): SampleBoxTable      box [[inline]];
        (_): UnknownBox box [[inline]];
    }
} [[name(std::format("MediaInformation({})", box.type))]];

struct MediaInformationBox : BaseBox {
    SubMediaInformationBox box[while($ < endOffset)] [[inline]];
};

// ---------------------------------------------------------------------------
// mdia
// ---------------------------------------------------------------------------

struct SubMediaBox {
    u32 type = std::mem::read_unsigned($ + 4, 4, std::mem::Endian::Big);

    match (str(type)) {
        ("mdhd"): MediaHeaderBox      box [[inline]];
        ("hdlr"): HandlerBox          box [[inline]];
        ("minf"): MediaInformationBox box [[inline]];
        (_): UnknownBox box [[inline]];
    }
} [[name(std::format("Media({})", box.type))]];

struct MediaBox : BaseBox {
    SubMediaBox box[while($ < endOffset)] [[inline]];
};

// ---------------------------------------------------------------------------
// udta children: AMBA (Insta360 metadata), nail (thumbnail), free
// ---------------------------------------------------------------------------

struct AMBABox : BaseBox {
    // Insta360 proprietary metadata blob
    u8 data[endOffset - $];
};

struct NailBox : BaseBox {
    // Thumbnail image data (typically JPEG)
    u8 data[endOffset - $];
};

struct FreeBox : BaseBox {
    // Free/padding space - content is unused
    u8 data[endOffset - $];
};

struct WideBox : BaseBox {
    // Wide placeholder box — typically 8 bytes, no payload.
    // Used before mdat to reserve space for a 64-bit extended size.
};

struct SubUserDataBox {
    u32 type = std::mem::read_unsigned($ + 4, 4, std::mem::Endian::Big);

    match (str(type)) {
        ("AMBA"): AMBABox    box [[inline]];
        ("nail"): NailBox    box [[inline]];
        ("free"): FreeBox    box [[inline]];
        (_):      UnknownBox box [[inline]];
    }
} [[name(std::format("UserData({})", box.type))]];

struct UserDataBox : BaseBox {
    SubUserDataBox box[while($ < endOffset)] [[inline]];
};

// ---------------------------------------------------------------------------
// trak
// ---------------------------------------------------------------------------

struct SubTrackBox {
    u32 type = std::mem::read_unsigned($ + 4, 4, std::mem::Endian::Big);

    match (str(type)) {
        ("tkhd"): TrackHeaderBox box [[inline]];
        ("edts"): EditBox        box [[inline]];
        ("mdia"): MediaBox       box [[inline]];
        (_): UnknownBox box [[inline]];
    }
} [[name(std::format("Track({})", box.type))]];

struct TrackBox : BaseBox {
    SubTrackBox box[while($ < endOffset)] [[inline]];
};

// ---------------------------------------------------------------------------
// moov
// ---------------------------------------------------------------------------

struct SubMovieBox {
    u32 type = std::mem::read_unsigned($ + 4, 4, std::mem::Endian::Big);

    match (str(type)) {
        ("mvhd"): MovieHeaderBox box [[inline]];
        ("udta"): UserDataBox    box [[inline]];
        ("trak"): TrackBox       box [[inline]];
        (_): UnknownBox box [[inline]];
    }
} [[name(std::format("Movie({})", box.type))]];

struct MovieBox : BaseBox {
    SubMovieBox box[while($ < endOffset)] [[inline]];
};

// ---------------------------------------------------------------------------
// mdat
// ---------------------------------------------------------------------------

struct MediaDataBox : BaseBox {
    // Raw media data - opaque bytes.
    // Use (endOffset - $) so this works correctly for both 32-bit and 64-bit
    // (largeSize) box headers. After BaseBox is parsed, $ is already positioned
    // at the first byte of payload, and endOffset = startOffset + boxSize.
    std::mem::Bytes<endOffset - $> data;
};

// ---------------------------------------------------------------------------
// Top-level dispatcher
// ---------------------------------------------------------------------------

struct Box {
    u32 type = std::mem::read_unsigned($ + 4, 4, std::mem::Endian::Big);

    match (str(type)) {
        ("ftyp"): FileTypeBox  box [[inline]];
        ("moov"): MovieBox     box [[inline]];
        ("mdat"): MediaDataBox box [[inline]];
        ("wide"): WideBox      box [[inline]];
        ("free"): FreeBox      box [[inline]];
        (_): UnknownBox box [[inline]];
    }
} [[name(std::format("Box({})", box.type))]];

Box insv[while(!std::mem::eof())] @ 0x0;
