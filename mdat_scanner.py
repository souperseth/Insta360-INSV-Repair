"""
mdat_scanner.py - MdatScanner class for scanning mdat to build sample tables
"""

import struct
from typing import BinaryIO, Optional, Dict
from constants import (
    X4_VIDEO_TIMESCALE, X4_AUDIO_TIMESCALE, X4_VIDEO_WIDTH, X4_VIDEO_HEIGHT,
    X4_AUDIO_SAMPLE_RATE, X4_AUDIO_CHANNELS, HEVC_IDR_TYPES, HEVC_SLICE_TYPES
)
from hevc_scanner import HEVCScanner

class MdatScanner:
    """Scan mdat to discover interleaved chunks and build sample tables."""

    def __init__(self, f: BinaryIO, mdat_offset: int, mdat_size: int,
                 mdat_header_size: int = 8):
        self.f = f
        self.mdat_offset = mdat_offset
        self.mdat_header_size = mdat_header_size
        self.mdat_data_start = mdat_offset + mdat_header_size
        self.mdat_end = mdat_offset + mdat_size
        self.mdat_size = mdat_size

    def _try_parse_hevc_sample(self, pos: int) -> Optional[tuple]:
        if pos + 6 > self.mdat_end:
            return None
        self.f.seek(pos)
        first_4 = self.f.read(4)
        if len(first_4) < 4:
            return None
        first_len = struct.unpack('>I', first_4)[0]
        if first_len < 2 or first_len > 30 * 1024 * 1024:
            return None
        nal_hdr = self.f.read(2)
        if len(nal_hdr) < 2:
            return None
        first_nal_type = HEVCScanner.get_nal_type(nal_hdr[0])
        if first_nal_type > 40:
            return None
        if nal_hdr[0] & 0x80:
            return None
        total_size = 0
        is_keyframe = False
        nal_types = []
        cur = pos
        found_slice = False
        while cur + 4 < self.mdat_end:
            self.f.seek(cur)
            lb = self.f.read(4)
            if len(lb) < 4:
                break
            nal_length = struct.unpack('>I', lb)[0]
            if nal_length < 2 or nal_length > 30 * 1024 * 1024:
                break
            if cur + 4 + nal_length > self.mdat_end:
                break
            nh = self.f.read(2)
            if len(nh) < 2:
                break
            if nh[0] & 0x80:
                break
            nal_type = HEVCScanner.get_nal_type(nh[0])
            if nal_type > 40:
                break
            nal_types.append(nal_type)
            if nal_type in HEVC_IDR_TYPES:
                is_keyframe = True
            total_size += 4 + nal_length
            cur += 4 + nal_length
            if nal_type in (32, 33, 34, 35, 39, 40):
                continue
            if nal_type in HEVC_SLICE_TYPES or nal_type in HEVC_IDR_TYPES:
                found_slice = True
                if cur + 6 <= self.mdat_end:
                    self.f.seek(cur)
                    peek = self.f.read(6)
                    if len(peek) >= 6:
                        peek_len = struct.unpack('>I', peek[:4])[0]
                        if 2 <= peek_len <= 30 * 1024 * 1024:
                            peek_type = HEVCScanner.get_nal_type(peek[4])
                            if peek_type == 40:
                                continue
                            if peek_type in HEVC_SLICE_TYPES and not (peek[5] & 0x80):
                                continue
                break
        if total_size == 0 or not found_slice:
            return None
        if total_size < 100:
            return None
        return (total_size, is_keyframe, nal_types)

    def _find_next_hevc_start(self, start: int, max_search: int = 64 * 1024) -> Optional[int]:
        search_end = min(start + max_search, self.mdat_end)
        pos = start
        while pos < search_end:
            result = self._try_parse_hevc_sample(pos)
            if result is not None:
                return pos
            pos += 1
        return None

    def scan_chunks_with_reference(self, ref_info: dict) -> dict:
        tracks = ref_info['tracks']
        num_video_tracks = sum(1 for t in tracks if t.get('handler_type') == b'vide')
        has_audio = any(t.get('handler_type') == b'soun' for t in tracks)
        video_track_indices = [i for i, t in enumerate(tracks) if t.get('handler_type') == b'vide']
        audio_track_idx = next((i for i, t in enumerate(tracks) if t.get('handler_type') == b'soun'), None)
        result = {}
        for i, track in enumerate(tracks):
            result[i] = {
                'chunk_offsets': [],
                'sample_sizes': [],
                'sync_samples': [],
                'samples_per_chunk': 1,
                'timescale': track.get('timescale', 60000),
                'sample_delta': track.get('sample_delta', 1001),
            }
        pos = self.mdat_data_start
        total_bytes = self.mdat_end - self.mdat_data_start
        last_progress = -1
        video_sample_count = {i: 0 for i in video_track_indices}
        audio_sample_count = 0
        next_video_track = 0
        while pos < self.mdat_end - 6:
            progress = int((pos - self.mdat_data_start) / total_bytes * 100)
            if progress >= last_progress + 5:
                last_progress = progress
            hevc_result = self._try_parse_hevc_sample(pos)
            if hevc_result is not None:
                sample_size, is_keyframe, nal_types = hevc_result
                track_idx = video_track_indices[next_video_track % num_video_tracks]
                next_video_track += 1
                result[track_idx]['chunk_offsets'].append(pos)
                result[track_idx]['sample_sizes'].append(sample_size)
                video_sample_count[track_idx] = video_sample_count.get(track_idx, 0) + 1
                if is_keyframe:
                    result[track_idx]['sync_samples'].append(
                        video_sample_count[track_idx])
                pos += sample_size
            else:
                if audio_track_idx is not None and has_audio:
                    next_hevc = self._find_next_hevc_start(pos + 1)
                    if next_hevc is not None:
                        audio_region = next_hevc - pos
                        audio_pos = pos
                        while audio_pos + 100 <= next_hevc:
                            remaining = next_hevc - audio_pos
                            frame_size = min(505, remaining)
                            if remaining > 505:
                                check = self._try_parse_hevc_sample(audio_pos + 505)
                                if check is not None:
                                    frame_size = 505
                                elif remaining >= 1010:
                                    frame_size = 505
                                else:
                                    frame_size = remaining
                            result[audio_track_idx]['chunk_offsets'].append(audio_pos)
                            result[audio_track_idx]['sample_sizes'].append(frame_size)
                            audio_sample_count += 1
                            audio_pos += frame_size
                        pos = next_hevc
                    else:
                        remaining = self.mdat_end - pos
                        if remaining > 100:
                            audio_pos = pos
                            while audio_pos + 100 < self.mdat_end:
                                frame_size = min(505, self.mdat_end - audio_pos)
                                if frame_size < 50:
                                    break
                                result[audio_track_idx]['chunk_offsets'].append(audio_pos)
                                result[audio_track_idx]['sample_sizes'].append(frame_size)
                                audio_sample_count += 1
                                audio_pos += frame_size
                        break
                else:
                    next_hevc = self._find_next_hevc_start(pos + 1, max_search=1024*1024)
                    if next_hevc:
                        pos = next_hevc
                    else:
                        break
        return result

    def scan_mdat_standalone(self) -> dict:
        result = {
            0: {'chunk_offsets': [], 'sample_sizes': [], 'sync_samples': [],
                'samples_per_chunk': 1, 'timescale': X4_VIDEO_TIMESCALE,
                'sample_delta': 1001},
            1: {'chunk_offsets': [], 'sample_sizes': [], 'sync_samples': [],
                'samples_per_chunk': 1, 'timescale': X4_VIDEO_TIMESCALE,
                'sample_delta': 1001},
            2: {'chunk_offsets': [], 'sample_sizes': [], 'sync_samples': [],
                'samples_per_chunk': 1, 'timescale': X4_AUDIO_TIMESCALE,
                'sample_delta': 1024},
        }
        pos = self.mdat_data_start
        total_bytes = self.mdat_end - self.mdat_data_start
        last_progress = -1
        video_count = {0: 0, 1: 0}
        audio_count = 0
        next_video_track = 0
        while pos < self.mdat_end - 6:
            progress = int((pos - self.mdat_data_start) / total_bytes * 100)
            if progress >= last_progress + 5:
                last_progress = progress
            hevc_result = self._try_parse_hevc_sample(pos)
            if hevc_result is not None:
                sample_size, is_keyframe, nal_types = hevc_result
                track_idx = next_video_track % 2
                next_video_track += 1
                result[track_idx]['chunk_offsets'].append(pos)
                result[track_idx]['sample_sizes'].append(sample_size)
                video_count[track_idx] += 1
                if is_keyframe:
                    result[track_idx]['sync_samples'].append(video_count[track_idx])
                pos += sample_size
            else:
                next_hevc = self._find_next_hevc_start(pos + 1)
                if next_hevc is not None:
                    audio_pos = pos
                    while audio_pos + 50 <= next_hevc:
                        frame_size = min(505, next_hevc - audio_pos)
                        if frame_size < 50:
                            break
                        result[2]['chunk_offsets'].append(audio_pos)
                        result[2]['sample_sizes'].append(frame_size)
                        audio_count += 1
                        audio_pos += frame_size
                    pos = next_hevc
                else:
                    next_hevc = self._find_next_hevc_start(pos + 1, max_search=1024 * 1024)
                    if next_hevc:
                        pos = next_hevc
                    else:
                        break
        return result