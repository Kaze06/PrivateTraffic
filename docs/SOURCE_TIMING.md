# Source observation time

The worker needs the time associated with the decoded frame. Receipt time, an HTTP Date header, a segment filename and an HLS media sequence are not capture timestamps.

## Configured MnDOT sources

On 2026-11-15 UTC, a metadata-only probe fetched the master and selected media playlists for C631, C825, C1628 and C8351. All returned HTTP 200. None of the observed media playlists contained EXT-X-PROGRAM-DATE-TIME. They contained relative segment durations and sequence numbers only. The exact responses and check times are in ../artifacts/source-timestamp-probe.json. No traffic video segments were downloaded for this probe.

This snapshot establishes that these playlist responses do not provide the required UTC anchor. It does not establish whether the provider has a separate timing service. The existing four feeds cannot be enabled as trustworthy live measurements with playlist timing alone.

## Required source evidence

Chen Jiajun / source owner should supply an allowed source and its timing specification: where the absolute timestamp is carried, which frame it refers to, whether it represents capture or packaging time, and the clock accuracy/known offset. Also supply the access and retention permission reference. A source with PROGRAM-DATE-TIME still needs its timing semantics verified before it is treated as capture time.

The implementation must keep each UTC anchor tied to the exact segment and decoded presentation timestamps. Fetching a recent playlist independently of an OpenCV capture and assigning its latest time to an arbitrary decoded frame is invalid. Missing or discontinuous timing must suppress the current measurement and restart the count window when usable timing returns.

## Implemented path

`run_detector_worker.py --source MEDIA_PLAYLIST --hls-pdt --trust-hls-source-clock` selects a segment-aware reader. `backend/app/hls_timestamp.py` binds the URI, media sequence, discontinuity sequence and decoded presentation timestamp to each image. Frame UTC is the segment's explicit anchor plus its presentation-time offset from the first displayed video sample. The existing custom adapter and recording-time replay paths remain available.

The supported input is an unencrypted, independent, video-only MPEG-TS media playlist with timezone-aware PROGRAM-DATE-TIME on every segment and segment duration at most 30 seconds. The reader rejects unsupported layouts and unavailable timing; it does not guess anchors from adjacent playlists. Discontinuity resets measurement accumulation before new frames. Consumed segments are not replayed after reconnect.

HLS delivers completed segments in batches. The worker waits up to 35 seconds for the next batch before reporting missing frames, rather than applying the OpenCV reader's 2-second timeout to normal segment publication. The current observation still has to be no more than 60 seconds old; this does not relax measurement freshness. Segment publication delay contributes to end-to-end latency and must be measured on the real pilot.

PyAV 16.1.0 is pinned to preserve the repository's Python 3.10 minimum. Synthetic fixtures encode and decode actual MPEG-TS video, including reordered frames, and verify frame timestamps and worker delivery. They establish software timing behavior, not MnDOT capture-time accuracy or RF-DETR accuracy.

Real-source acceptance remains pending the evidence above.
