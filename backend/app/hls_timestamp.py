"""Opt-in HLS source clock: decode the identified segment, never the playlist URL.

Supported input is an unencrypted video-only MPEG-TS media playlist with explicit
PDT on every segment. The operator must establish that PDT is the UTC observation
time of the segment's first displayed video sample, not publication/receipt time.
Master playlists, audio, byte ranges and init segments deliberately fail closed.
"""
import io
import math
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen


class SourceTimingError(ValueError):
    pass


@dataclass(frozen=True)
class Segment:
    sequence: int
    uri: str
    duration: float
    observed_at: datetime
    discontinuity: int


def parse_playlist(text, playlist_url):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise SourceTimingError("hls_invalid_playlist")
    sequence = None
    discontinuity = 0
    duration = None
    anchor = None
    segments = []
    for line in lines[1:]:
        if line.startswith(("#EXT-X-STREAM-INF", "#EXT-X-MAP", "#EXT-X-BYTERANGE", "#EXT-X-PART", "#EXT-X-GAP")):
            raise SourceTimingError("hls_unsupported_playlist_layout")
        if line.startswith("#EXT-X-KEY:") and line != "#EXT-X-KEY:METHOD=NONE":
            raise SourceTimingError("hls_encrypted_segment_unsupported")
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            sequence = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE:"):
            discontinuity = int(line.split(":", 1)[1])
        elif line == "#EXT-X-DISCONTINUITY":
            discontinuity += 1
            anchor = None
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            anchor = datetime.fromisoformat(line.split(":", 1)[1].replace("Z", "+00:00"))
            if anchor.tzinfo is None or anchor.utcoffset() is None:
                raise SourceTimingError("hls_pdt_timezone_missing")
            anchor = anchor.astimezone(timezone.utc)
        elif line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0])
        elif not line.startswith("#"):
            if sequence is None or duration is None or anchor is None:
                raise SourceTimingError("hls_segment_source_timestamp_unavailable")
            if not math.isfinite(duration) or not 0 < duration <= 30:
                raise SourceTimingError("hls_segment_duration_unsupported")
            uri = urljoin(playlist_url, line)
            if urlparse(uri).scheme not in {"http", "https"}:
                raise SourceTimingError("hls_segment_url_unsupported")
            segments.append(Segment(sequence, uri, duration, anchor, discontinuity))
            sequence += 1
            anchor = duration = None
    if not segments:
        raise SourceTimingError("hls_no_complete_segments")
    return segments


def fetch_bytes(url, limit=32 * 1024 * 1024):
    with urlopen(url, timeout=5) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise SourceTimingError("hls_response_too_large")
    return data


def decode_segment(data, segment):
    """Return (BGR image, UTC time, PTS) from precisely these segment bytes."""
    import av
    with av.open(io.BytesIO(data), mode="r", format="mpegts", options={"err_detect": "explode"}) as container:
        videos = list(container.streams.video)
        if len(videos) != 1 or any(stream.type != "video" for stream in container.streams):
            raise SourceTimingError("hls_video_only_segment_required")
        frames = list(container.decode(videos[0]))
    if not frames or not frames[0].key_frame:
        raise SourceTimingError("hls_independent_segment_required")
    timed = []
    first_pts = None
    previous = None
    for frame in frames:
        if frame.is_corrupt:
            raise SourceTimingError("hls_corrupt_frame")
        if frame.pts is None or frame.time_base is None:
            raise SourceTimingError("hls_frame_pts_unavailable")
        pts = frame.pts * frame.time_base
        if first_pts is None:
            first_pts = pts
        offset = float(pts - first_pts)
        if previous is not None and pts <= previous:
            raise SourceTimingError("hls_frame_pts_not_increasing")
        if offset < 0 or offset >= segment.duration:
            raise SourceTimingError("hls_frame_outside_segment")
        timed.append((frame.to_ndarray(format="bgr24"), segment.observed_at + timedelta(seconds=offset), pts))
        previous = pts
    return timed


class SegmentClock:
    """Track segment identity across polling; failures retain the consumed watermark."""
    def __init__(self, playlist_url, *, trust_pdt=False, fetch=fetch_bytes, decode=decode_segment):
        if not trust_pdt:
            raise SourceTimingError("hls_source_clock_trust_required")
        self.playlist_url, self.fetch, self.decode = playlist_url, fetch, decode
        self.last_segment = None
        self.last_frame_time = None
        self.interrupted = False

    def poll(self):
        """Yield ('invalid', reason) or ('frame', (image, time, identity))."""
        try:
            segments = parse_playlist(self.fetch(self.playlist_url).decode("utf-8-sig"), self.playlist_url)
            candidates = [s for s in segments if self.last_segment is None or s.sequence > self.last_segment.sequence]
            if self.last_segment is not None:
                same = next((s for s in segments if s.sequence == self.last_segment.sequence), None)
                if same is not None and same != self.last_segment:
                    raise SourceTimingError("hls_segment_identity_changed")
                if segments[-1].sequence < self.last_segment.sequence:
                    raise SourceTimingError("hls_sequence_regressed")
            # At startup/reconnect use the latest complete anchored segment, not an old backlog.
            if self.last_segment is None or self.interrupted:
                candidates = candidates[-1:]
            for segment in candidates:
                previous = self.last_segment
                if previous and (segment.sequence != previous.sequence + 1 or segment.discontinuity != previous.discontinuity):
                    yield "invalid", "hls_segment_discontinuity"
                elif previous:
                    delta = (segment.observed_at - previous.observed_at).total_seconds()
                    if abs(delta - previous.duration) > 0.1:
                        yield "invalid", "hls_source_clock_discontinuity"
                # Mark before decoding: an uncertain partially consumed segment is never replayed.
                self.last_segment = segment
                frames = self.decode(self.fetch(segment.uri), segment)
                for image, observed, pts in frames:
                    if self.last_frame_time is not None and observed <= self.last_frame_time:
                        raise SourceTimingError("hls_source_time_not_increasing")
                    self.last_frame_time = observed
                    yield "frame", (image, observed, (segment.discontinuity, segment.sequence, segment.uri, str(pts)))
                self.interrupted = False
        except Exception as exc:
            self.interrupted = True
            yield "invalid", str(exc) if isinstance(exc, SourceTimingError) else "hls_source_or_decode_unavailable"
