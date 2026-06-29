"""Regression tests for the media-send client timeout.

Bug: ``_send_media_payload`` POSTed to ``/api/v1/send`` on the client-level
default timeout (~30s). For video/animation the chatlytics server runs a WAHA
ffmpeg transcode (``convert: true``) that routinely exceeds 30s, so EVERY
``send_animation`` / ``send_video`` tripped an httpx ReadTimeout (surfaced as
``"send transport error"``) while ``send_image`` (no transcode) succeeded.

Fix: video/animation sends use a long client read timeout that exceeds the
server's ~120s transcode budget; other media gets a moderate window for large
uploads.  These tests lock the contract (the constants + the kind→send-type map)
so the timeout can't silently regress back below the server budget.
"""

from __future__ import annotations

from chatlytics_hermes.adapter import (
    _MEDIA_DEFAULT_TIMEOUT,
    _MEDIA_KIND_TO_SEND_TYPE,
    _MEDIA_VIDEO_TIMEOUT,
)

# The server waits this long (ms) for the WAHA transcode — the client read
# timeout MUST exceed it. Mirrors VIDEO_TRANSCODE_TIMEOUT_MS in chatlytics.ai
# src/send.ts (120_000).
_SERVER_TRANSCODE_BUDGET_S = 120.0


def test_video_timeout_exceeds_server_transcode_budget():
    # The client must wait longer than the server's transcode window, or it
    # gives up before the server responds (the original transport-error bug).
    assert _MEDIA_VIDEO_TIMEOUT.read is not None
    assert _MEDIA_VIDEO_TIMEOUT.read > _SERVER_TRANSCODE_BUDGET_S


def test_video_timeout_longer_than_default_media_timeout():
    assert _MEDIA_DEFAULT_TIMEOUT.read is not None
    assert _MEDIA_VIDEO_TIMEOUT.read > _MEDIA_DEFAULT_TIMEOUT.read


def test_default_media_timeout_beats_old_30s_default():
    # The old ~30s client default was the root cause; the moderate media window
    # must be more generous than that for large base64 uploads.
    assert _MEDIA_DEFAULT_TIMEOUT.read > 30.0


def test_animation_and_video_both_route_to_video_send_type():
    # Both kinds map to the slow transcoding send-type, so both get the long
    # timeout via `send_type == "video"` in _send_media_payload.
    assert _MEDIA_KIND_TO_SEND_TYPE["animation"] == "video"
    assert _MEDIA_KIND_TO_SEND_TYPE["video"] == "video"
    # Image is the fast path that worked all along — NOT the video send-type.
    assert _MEDIA_KIND_TO_SEND_TYPE["image"] != "video"
