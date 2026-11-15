"""All media and timestamps in this module are synthetic; no traffic is fetched."""
import io
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from fractions import Fraction

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import pytest
from app.hls_timestamp import Segment, SegmentClock, SourceTimingError, parse_playlist, decode_segment

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
URL = "https://fixture.test/media.m3u8"


def playlist(sequence=10, anchor=START, discontinuity=False, uri="segment.ts"):
    return (f"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:{sequence}\n"
            + ("#EXT-X-DISCONTINUITY-SEQUENCE:1\n" if discontinuity else "")
            + (f"#EXT-X-PROGRAM-DATE-TIME:{anchor.isoformat()}\n" if anchor else "")
            + f"#EXTINF:1.0,\n{uri}\n").encode()


def fake_decode(data, segment):
    return [(data, segment.observed_at + timedelta(milliseconds=i * 100), Fraction(i, 10)) for i in range(3)]


def test_untrusted_clock_and_missing_pdt_fail_before_segment_fetch():
    with pytest.raises(SourceTimingError, match="trust_required"):
        SegmentClock(URL)
    requested = []
    def fetch(url):
        requested.append(url)
        return playlist(anchor=None)
    result = list(SegmentClock(URL, trust_pdt=True, fetch=fetch, decode=fake_decode).poll())
    assert result == [("invalid", "hls_segment_source_timestamp_unavailable")]
    assert requested == [URL]


def test_segment_uri_identity_and_pts_stay_bound_across_playlist_refresh():
    current = [playlist()]
    requested = []
    def fetch(url):
        requested.append(url)
        return current[0] if url == URL else url.encode()
    clock = SegmentClock(URL, trust_pdt=True, fetch=fetch, decode=fake_decode)
    first = list(clock.poll())
    assert first[0][1][0] == b"https://fixture.test/segment.ts"
    assert first[1][1][1] == START + timedelta(milliseconds=100)
    assert first[1][1][2] == (0, 10, "https://fixture.test/segment.ts", "1/10")
    assert list(clock.poll()) == []  # polling never decodes the same segment twice
    current[0] = playlist(11, START + timedelta(seconds=1), uri="next.ts")
    second = list(clock.poll())
    assert all(kind == "frame" for kind, _ in second)
    assert second[0][1][0] == b"https://fixture.test/next.ts"
    assert requested.count("https://fixture.test/segment.ts") == 1


@pytest.mark.parametrize("next_playlist, reason", [
    (playlist(11, START + timedelta(seconds=1), True), "hls_segment_discontinuity"),
    (playlist(12, START + timedelta(seconds=2)), "hls_segment_discontinuity"),
    (playlist(11, START + timedelta(seconds=9)), "hls_source_clock_discontinuity"),
    (playlist(10, START, uri="replacement.ts"), "hls_segment_identity_changed"),
    (playlist(9), "hls_sequence_regressed"),
])
def test_discontinuity_resets_before_new_frames(next_playlist, reason):
    current = [playlist()]
    clock = SegmentClock(URL, trust_pdt=True, fetch=lambda url: current[0] if url == URL else b"frame", decode=fake_decode)
    list(clock.poll())
    current[0] = next_playlist
    assert list(clock.poll())[0] == ("invalid", reason)


def test_reconnect_never_reuses_consumed_segments_and_requires_new_anchor():
    current = [playlist()]
    def fetch(url):
        if current[0] is None:
            raise OSError("synthetic disconnect")
        return current[0] if url == URL else b"frame"
    clock = SegmentClock(URL, trust_pdt=True, fetch=fetch, decode=fake_decode)
    list(clock.poll())
    current[0] = None
    assert list(clock.poll())[0][0] == "invalid"
    current[0] = playlist()
    assert list(clock.poll()) == []
    current[0] = playlist(11, anchor=None)
    assert list(clock.poll())[0][0] == "invalid"
    current[0] = playlist(11, START + timedelta(seconds=1))
    assert list(clock.poll())[0][0] == "frame"


@pytest.mark.parametrize("tag", ["#EXT-X-STREAM-INF:BANDWIDTH=100", "#EXT-X-MAP:URI=init.mp4", "#EXT-X-BYTERANGE:100", "#EXT-X-KEY:METHOD=AES-128", "#EXT-X-GAP"])
def test_unsupported_layouts_fail_closed(tag):
    with pytest.raises(SourceTimingError):
        parse_playlist(playlist().decode().replace("#EXTINF", tag + "\n#EXTINF"), URL)


def encode_synthetic_segment():
    import av
    output = io.BytesIO()
    with av.open(output, "w", format="mpegts") as container:
        stream = container.add_stream("mpeg2video", rate=10)
        stream.width, stream.height, stream.pix_fmt = 32, 32, "yuv420p"
        stream.codec_context.max_b_frames = 2
        for i in range(10):
            frame = av.VideoFrame(32, 32, "yuv420p")
            for plane_index, plane in enumerate(frame.planes):
                plane.update(bytes([16 + i * 15 if plane_index == 0 else 128]) * plane.buffer_size)
            frame.pts = i
            frame.time_base = Fraction(1, 10)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return output.getvalue()


def test_real_mpegts_decode_preserves_frame_pts_and_anchor():
    data = encode_synthetic_segment()
    segment = Segment(10, "https://fixture.test/segment.ts", 1.0, START, 0)
    frames = decode_segment(data, segment)
    assert len(frames) == 10
    assert [observed for _, observed, _ in frames] == [START + timedelta(milliseconds=i * 100) for i in range(10)]
    assert len({float(image.mean()) for image, _, _ in frames}) > 5
    assert all(image.shape == (32, 32, 3) for image, _, _ in frames)
    with pytest.raises(SourceTimingError, match="outside_segment"):
        decode_segment(data, Segment(10, segment.uri, 0.2, START, 0))


def test_worker_reader_delivers_segment_decoded_images_with_same_identity(monkeypatch):
    import run_detector_worker as worker
    import app.hls_timestamp as hls
    data = encode_synthetic_segment()
    requests = []
    def fetch(url):
        requests.append(url)
        return playlist() if url == URL else data
    clock = SegmentClock(URL, trust_pdt=True, fetch=fetch)
    monkeypatch.setattr(hls, "SegmentClock", lambda *args, **kwargs: clock)
    reader = worker.create_source_reader(URL, False, False, None, None, True)
    try:
        packets = [reader.frame_queue.get(timeout=5) for _ in range(10)]
    finally:
        reader.stop()
    assert [p.observed_at for p in packets] == [START + timedelta(milliseconds=i * 100) for i in range(10)]
    assert all(p.sequence[:3] == (0, 10, "https://fixture.test/segment.ts") for p in packets)
    assert len({float(p.image.mean()) for p in packets}) > 5
    assert requests.count("https://fixture.test/segment.ts") == 1
    assert all(not p.is_replay for p in packets)


def test_reconnect_rejects_backward_clock_even_after_invalid_marker():
    current = [playlist()]
    clock = SegmentClock(URL, trust_pdt=True, fetch=lambda url: current[0] if url == URL else b"frame", decode=fake_decode)
    list(clock.poll())
    current[0] = playlist(11, START - timedelta(seconds=10), True)
    events = list(clock.poll())
    assert all(kind == "invalid" for kind, _ in events)
    assert events[-1] == ("invalid", "hls_source_time_not_increasing")


def test_custom_adapter_and_replay_keep_original_reader(monkeypatch):
    import run_detector_worker as worker
    captured = []
    monkeypatch.setattr(worker, "MonotonicPacedReader", lambda *args, **kwargs: captured.append((args, kwargs)))
    adapter = object()
    worker.create_source_reader(URL, False, False, None, adapter, False)
    worker.create_source_reader("clip.mp4", True, True, START, None, True)
    assert captured[0][1]["timestamp_adapter"] is adapter
    assert captured[1][1]["is_replay"] and captured[1][1]["recording_time"] == START
