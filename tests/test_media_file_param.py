"""Regression tests for the media-tool ``file`` parameter.

Bug: the media tools (``chatlytics_send_image`` / ``_send_file`` /
``_send_animation`` / ``_send_voice`` / ``_send_video``) advertised only
``mediaUrl`` / ``filePath`` in their schema and handler signatures, so a caller
passing the natural ``file`` arg (a URL or a local path) reached the handler
with neither set and got ``"Either mediaUrl or filePath is required."``.

Fix: ``file`` is the canonical media param (URL or local path; the adapter
auto-detects), with ``mediaUrl`` / ``filePath`` kept as back-compat aliases.
Precedence: file > mediaUrl > filePath.
"""

from __future__ import annotations

from chatlytics_hermes.tools import (
    SEND_ANIMATION_SCHEMA,
    SEND_FILE_SCHEMA,
    SEND_IMAGE_SCHEMA,
    SEND_VIDEO_SCHEMA,
    SEND_VOICE_SCHEMA,
    _resolve_resource,
)

_ALL_MEDIA_SCHEMAS = [
    SEND_IMAGE_SCHEMA,
    SEND_VOICE_SCHEMA,
    SEND_VIDEO_SCHEMA,
    SEND_FILE_SCHEMA,
    SEND_ANIMATION_SCHEMA,
]


def test_resolve_resource_prefers_file():
    # file is canonical and wins over the aliases.
    assert _resolve_resource(file="https://x/img.png") == "https://x/img.png"
    assert (
        _resolve_resource(file="https://x/a.png", mediaUrl="https://y/b.png")
        == "https://x/a.png"
    )
    assert _resolve_resource(file="/tmp/a.png", filePath="/tmp/b.png") == "/tmp/a.png"


def test_resolve_resource_back_compat_aliases():
    # mediaUrl / filePath still work when file is absent.
    assert _resolve_resource(mediaUrl="https://y/b.png") == "https://y/b.png"
    assert _resolve_resource(filePath="/tmp/b.png") == "/tmp/b.png"
    assert _resolve_resource(mediaUrl="https://y/b.png", filePath="/tmp/b.png") == "https://y/b.png"


def test_resolve_resource_none_when_empty():
    assert _resolve_resource() is None
    assert _resolve_resource(file="", mediaUrl="", filePath="") in (None, "")


def test_every_media_schema_advertises_file():
    for schema in _ALL_MEDIA_SCHEMAS:
        props = schema["properties"]
        assert "file" in props, f"{schema['title']} missing 'file' property"
        # file satisfies the at-least-one-media requirement.
        anyof_required = [tuple(branch.get("required", [])) for branch in schema["anyOf"]]
        assert ("file",) in anyof_required, f"{schema['title']} anyOf must accept 'file'"
        # back-compat aliases still present.
        assert "mediaUrl" in props and "filePath" in props
