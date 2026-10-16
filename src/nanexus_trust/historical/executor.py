"""Deterministic compressed MP4 packet reconstruction and finite backend execution."""

import hashlib
import json
from pathlib import Path
import tempfile

from .backend import run_tool
from .models import HistoricalError


def reconstruct_mp4(data: bytes, probe: dict, maximum: int) -> bytes:
    """Preserve packet NAL payloads, including SEI-only samples; no avcC injection."""
    try:
        streams = probe["streams"]
        if len(streams) != 1 or streams[0]["codec_name"] != "h264":
            raise ValueError("unsupported stream layout")
        stream = streams[0]
        width = int(stream["nal_length_size"])
        if width not in (1, 2, 3, 4) or not probe["packets"]:
            raise ValueError("invalid packet layout")
        out = bytearray()
        previous_end = -1
        for packet in probe["packets"]:
            pos, size = int(packet["pos"]), int(packet["size"])
            if (packet["stream_index"] != stream["index"] or pos < 0 or size <= 0
                    or pos < previous_end or pos + size > len(data)):
                raise ValueError("invalid packet extent/order")
            previous_end = pos + size
            offset, end = pos, pos + size
            while offset < end:
                if offset + width > end:
                    raise ValueError("truncated NAL length")
                length = int.from_bytes(data[offset:offset + width], "big")
                offset += width
                if length <= 0 or offset + length > end:
                    raise ValueError("invalid NAL length")
                if len(out) + 4 + length > maximum:
                    raise HistoricalError("RESOURCE_LIMIT")
                out.extend(b"\x00\x00\x00\x01" + data[offset:offset + length])
                offset += length
        return bytes(out)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise HistoricalError("MEDIA_UNSUPPORTED") from exc


class FiniteExecutor:
    def __init__(self, backend, ffprobe: Path):
        self.backend = backend
        self.ffprobe = Path(ffprobe)

    def execute(self, candidate, source, limits):
        if len(candidate.descriptors) > limits.max_files or candidate.byte_count > limits.max_bytes:
            raise HistoricalError("RESOURCE_LIMIT")
        # Owned temporary files only: immutable candidate copies keep probe offsets and
        # payload reads coherent even if the caller's retained media changes mid-call.
        with tempfile.TemporaryDirectory(prefix="historical-") as directory:
            root = Path(directory)
            window = root / "window.h264"
            total = 0
            with window.open("xb") as output:
                for index, descriptor in enumerate(candidate.descriptors):
                    data = source.read(descriptor, limits)
                    if len(data) != descriptor.size or hashlib.sha256(data).hexdigest() != descriptor.sha256:
                        raise HistoricalError("MEDIA_CHANGED")
                    local = root / f"media-{index}.mp4"
                    local.write_bytes(data)
                    code, stdout = run_tool([self.ffprobe, "-v", "error", "-show_streams", "-show_packets",
                                             "-show_entries", "packet=stream_index,pos,size", "-of", "json", local], limits)
                    if code != 0:
                        raise HistoricalError("MEDIA_UNSUPPORTED")
                    try:
                        probe = json.loads(stdout)
                    except (ValueError, RecursionError) as exc:
                        raise HistoricalError("MEDIA_UNSUPPORTED") from exc
                    payload = reconstruct_mp4(data, probe, limits.max_bytes - total)
                    output.write(payload)
                    total += len(payload)
                    # Recheck the adapter snapshot before accepting this candidate.
                    current = source.read(descriptor, limits)
                    if len(current) != descriptor.size or hashlib.sha256(current).hexdigest() != descriptor.sha256:
                        raise HistoricalError("MEDIA_CHANGED")
            return self.backend.verify(window, limits)
