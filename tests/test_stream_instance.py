"""v5.0.1 -- gateway instance id + 409 duplicate_consumer (chatlytics#838).

Client side of the server's liveness-checked takeover (design
``bot-update-delivery-v2.md`` 3.7 / 4.3.1): one random id per process sent on
every stream connect, and a long, jittered, loud-once backoff when the server
refuses a duplicate consumer.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from chatlytics_hermes import stream as stream_mod
from chatlytics_hermes.stream import (
    GATEWAY_INSTANCE_HEADER,
    GATEWAY_INSTANCE_ID,
    StreamOutcome,
    reconnect_delay,
)
from tests.test_stream import (  # noqa: F401  (server fixture + autouse env)
    TOKEN_A,
    _adapter,
    _stream_env,
    _until,
    server,
)

DUP = {"Retry-After": "0", "X-Chatlytics-Reject-Reason": "duplicate_consumer"}
SRC = Path(__file__).resolve().parents[1] / "src"


def _dup_outcome(retry_after: float = 60.0) -> StreamOutcome:
    return StreamOutcome(http_status=409, retry_after_s=retry_after, reject_reason="duplicate_consumer")


# --- the id -----------------------------------------------------------------


def test_instance_id_matches_server_charset_and_length() -> None:
    assert re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", GATEWAY_INSTANCE_ID)


def test_instance_id_differs_across_processes() -> None:
    """Fresh random per process: never derived from env/config/hostname."""
    code = "import chatlytics_hermes.stream as s; print(s.GATEWAY_INSTANCE_ID)"
    env_same = {"PYTHONPATH": str(SRC), "HOSTNAME": "same", "CHATLYTICS_BOT_TOKEN": "same"}
    ids = {
        subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, **env_same}, check=True
        ).stdout.strip()
        for _ in range(2)
    }
    assert len(ids) == 2 and all(ids)


async def test_header_sent_and_id_stable_across_reconnects(server) -> None:
    server.close_next[TOKEN_A] = [(1012, {"reason": "deploy", "after_ms": 0})]
    a, _ = _adapter(server)
    assert await a.connect()
    try:
        await _until(lambda: len(server.conns_for(TOKEN_A)) == 2, timeout=5)
        sent = [h.get(GATEWAY_INSTANCE_HEADER) for h in server.upgrade_headers]
        assert len(sent) >= 2
        assert set(sent) == {GATEWAY_INSTANCE_ID}
    finally:
        await a.disconnect()


# --- 409 duplicate_consumer ---------------------------------------------------


def test_duplicate_consumer_is_only_409_with_the_reason() -> None:
    assert _dup_outcome().duplicate_consumer
    assert not StreamOutcome(http_status=409, retry_after_s=2.0).duplicate_consumer
    assert not StreamOutcome(http_status=503, reject_reason="duplicate_consumer").duplicate_consumer


def test_duplicate_backoff_is_60s_doubling_to_10min_with_jitter() -> None:
    o = _dup_outcome()
    for streak, base in [(1, 60), (2, 120), (3, 240), (4, 480), (5, 600), (9, 600)]:
        for _ in range(30):
            d = reconnect_delay(o, 0, 0, streak)
            assert base <= d <= base * 1.25
    assert len({reconnect_delay(o, 0, 0, 1) for _ in range(20)}) > 1  # jittered


def test_duplicate_backoff_honours_a_larger_retry_after() -> None:
    assert reconnect_delay(_dup_outcome(300.0), 0, 0, 1) >= 300.0


async def test_409_duplicate_backs_off_doubling_warns_once_and_recovers(
    server, monkeypatch, caplog
) -> None:
    monkeypatch.setattr(stream_mod, "DUPLICATE_BACKOFF_S", 0.3)
    server.refuse[TOKEN_A] = [(409, dict(DUP)), (409, dict(DUP))]
    caplog.set_level(logging.DEBUG, logger="chatlytics_hermes.adapter")
    a, _ = _adapter(server)
    assert await a.connect()
    try:
        await _until(lambda: len(server.conns_for(TOKEN_A)) == 1, timeout=10)
        t = [at for _, at in server.upgrade_attempts]
        assert len(t) == 3  # two refusals, then the takeover succeeds
        assert t[1] - t[0] >= 0.3  # honoured the backoff, not a hot loop
        assert t[2] - t[1] >= 0.6  # doubled
        loud = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and "already consuming this bot token" in r.getMessage()
        ]
        assert len(loud) == 1  # one loud line per episode, not per refusal
        assert server.lp_gets == []  # no long-poll fallback on a duplicate
    finally:
        await a.disconnect()


async def test_plain_409_keeps_ordinary_retry_after_behaviour(server) -> None:
    server.refuse[TOKEN_A] = [(409, {"Retry-After": "0"})]
    a, _ = _adapter(server)
    assert await a.connect()
    try:
        await _until(lambda: len(server.conns_for(TOKEN_A)) == 1, timeout=5)
        t = [at for _, at in server.upgrade_attempts]
        assert t[1] - t[0] < 5.0  # NOT the 60 s duplicate backoff
    finally:
        await a.disconnect()
