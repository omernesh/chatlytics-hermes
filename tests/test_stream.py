"""v5.0.0 — WebSocket bot stream client (chatlytics#793 Phase 3).

Every test runs the REAL adapter (``connect()`` → per-bot ``_inbound_loop``
→ ``run_stream_once`` / ``_poll_loop``) against a local fake chatlytics
server built on aiohttp: a ``chatlytics.bot.v1`` WebSocket endpoint plus the
long-poll, ack, send, health and bot/me routes. No respx — the stream and
the HTTP calls hit real sockets on 127.0.0.1.

Covered: happy path (hello → ready{caps:["control"]} → env → ack), dedupe
on ``env.id``, resume cursor, pong under a server heartbeat, watchdog,
fallback to long-poll on 404 and re-probe switch-over, transient 503 is NOT
a fallback, 1012/1013 reconnect, 4409 long backoff, 4401 stop, multiplex
(two bots → two sockets → replies on the right bearer), status reporting.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import pytest
from aiohttp import WSMsgType, web

from chatlytics_hermes import adapter as adapter_mod
from chatlytics_hermes import stream as stream_mod
from chatlytics_hermes.adapter import ChatlyticsAdapter
from chatlytics_hermes.stream import DedupeCache, StreamOutcome, reconnect_delay
from tests._fixtures import FakePlatformConfig

TOKEN_A = "sk_bot_" + "A" * 43
TOKEN_B = "sk_bot_" + "B" * 43
CHAT_A = "120363421825201386@g.us"
CHAT_B = "120363412367458428@g.us"
SESSION_A = "3cf11776_logan"
SESSION_B = "3cf11776_omer"


def _bearer(request: web.Request) -> str:
    return request.headers.get("Authorization", "").removeprefix("Bearer ")


def _envelope(chat: str, session: str, text: str, mid: str) -> Dict[str, Any]:
    return {
        "session_id": session,
        "chat_type": "group",
        "entity_jid": chat,
        "sender_jid": "972544329000@c.us",
        "text": text,
        "message_id": mid,
        "ts": 1719000000000,
    }


class FakeServer:
    """Minimal chatlytics: stream + long-poll + send, per bot token."""

    def __init__(self) -> None:
        self.stream_enabled = True
        self.next_seq: Dict[str, int] = {}
        self.pending: Dict[str, List[Dict[str, Any]]] = {}  # stream env frames
        self.lp_pending: Dict[str, List[Dict[str, Any]]] = {}  # long-poll envelopes
        self.refuse: Dict[str, List[Tuple[int, Dict[str, str]]]] = {}
        self.close_next: Dict[str, List[Tuple[int, Optional[Dict[str, Any]]]]] = {}
        self.heartbeat: Optional[float] = None
        self.silent = False  # True: never send anything after hello (watchdog test)
        self.ws_conns: List[Dict[str, Any]] = []  # {token, ready, at, protocol}
        self.acks: List[Tuple[str, str]] = []
        self.lp_gets: List[str] = []
        self.lp_acks: List[Tuple[str, str]] = []
        self.sends: List[Tuple[str, Dict[str, Any]]] = []
        self.upgrade_attempts: List[Tuple[str, float]] = []
        self.wake = asyncio.Event()
        self.runner: Optional[web.AppRunner] = None
        self.base_url = ""

    # --- helpers --------------------------------------------------------
    def push(self, token: str, envelope: Dict[str, Any], env_id: Optional[str] = None) -> None:
        seq = self.next_seq.get(token, 0) + 1
        self.next_seq[token] = seq
        self.pending.setdefault(token, []).append(
            {
                "t": "env",
                "seq": seq,
                "cursor": f"cur-{seq}",
                "id": env_id or f"id-{envelope.get('message_id')}",
                "envelope": envelope,
                "delivery_count": 1,
            }
        )
        self.wake.set()

    def conns_for(self, token: str) -> List[Dict[str, Any]]:
        return [c for c in self.ws_conns if c["token"] == token]

    # --- routes ---------------------------------------------------------
    async def stream(self, request: web.Request) -> web.StreamResponse:
        token = _bearer(request)
        self.upgrade_attempts.append((token, time.monotonic()))
        if not self.stream_enabled:
            return web.Response(status=404, text="Not Found")
        if self.refuse.get(token):
            status, headers = self.refuse[token].pop(0)
            return web.json_response({"error": "refused"}, status=status, headers=headers)
        ws = web.WebSocketResponse(
            protocols=("chatlytics.bot.v1",), heartbeat=self.heartbeat, autoping=True
        )
        await ws.prepare(request)
        conn: Dict[str, Any] = {"token": token, "ready": None, "at": time.monotonic(), "protocol": ws.ws_protocol}
        self.ws_conns.append(conn)
        await ws.send_json(
            {
                "t": "hello", "v": 1, "conn_id": f"conn-{len(self.ws_conns)}", "bot_fp": "fp",
                "session_id": "s", "heartbeat_ms": 30000, "max_inflight": 32,
                "server_acked_cursor": "", "resume_hint_ms": 1000,
            }
        )
        msg = await ws.receive(timeout=5)
        conn["ready"] = json.loads(msg.data)
        sent = 0
        while not ws.closed:
            if not self.silent:
                for frame in list(self.pending.get(token, [])):
                    if frame["seq"] > sent:
                        await ws.send_json(frame)
                        sent = frame["seq"]
            if self.close_next.get(token) and not self.pending.get(token):
                code, reconnect = self.close_next[token].pop(0)
                if reconnect is not None:
                    await ws.send_json({"t": "reconnect", **reconnect})
                await ws.close(code=code)
                break
            try:
                msg = await ws.receive(timeout=0.05)
            except asyncio.TimeoutError:
                continue
            if msg.type == WSMsgType.TEXT:
                frame = json.loads(msg.data)
                if frame.get("t") == "ack":
                    self.acks.append((token, frame["cursor"]))
                    upto = int(frame["cursor"].split("-")[1])
                    self.pending[token] = [f for f in self.pending.get(token, []) if f["seq"] > upto]
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                break
        return ws

    async def updates(self, request: web.Request) -> web.Response:
        token = _bearer(request)
        self.lp_gets.append(token)
        batch = self.lp_pending.pop(token, [])
        if not batch:
            await asyncio.sleep(0.05)
            return web.json_response({"envelopes": [], "cursor": request.query.get("cursor", "")})
        return web.json_response({"envelopes": batch, "cursor": f"lp-{len(self.lp_gets)}"})

    async def updates_ack(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.lp_acks.append((_bearer(request), body.get("cursor")))
        return web.json_response({"ok": True})

    async def send(self, request: web.Request) -> web.Response:
        self.sends.append((_bearer(request), await request.json()))
        return web.json_response({"success": True, "messageId": "m-1"})

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "webhook_registered": True})

    async def me(self, request: web.Request) -> web.Response:
        return web.json_response({"bot": {"name": "bot-" + _bearer(request)[7:8]}})

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api/v1/bot/stream", self.stream)
        app.router.add_get("/api/v1/bot/updates", self.updates)
        app.router.add_post("/api/v1/bot/updates/ack", self.updates_ack)
        app.router.add_post("/api/v1/send", self.send)
        app.router.add_get("/health", self.health)
        app.router.add_get("/api/v1/bot/me", self.me)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        self.base_url = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()


async def _until(pred, timeout: float = 5.0, step: float = 0.02) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        await asyncio.sleep(step)
    raise AssertionError("condition not met within %.1fs" % timeout)


@pytest.fixture
async def server():
    srv = FakeServer()
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture(autouse=True)
def _stream_env(monkeypatch):
    # conftest pins the pre-5.0 suites to long-poll; these tests choose.
    monkeypatch.delenv("CHATLYTICS_TRANSPORT", raising=False)


def _adapter(server: FakeServer, *, transport: Optional[str] = None, extra_tokens=None):
    extra: Dict[str, Any] = {
        "base_url": server.base_url,
        "bot_token": TOKEN_A,
        "inbound_mode": "longpoll",
    }
    if transport:
        extra["transport"] = transport
    if extra_tokens:
        extra["extra_bot_tokens"] = extra_tokens
    a = ChatlyticsAdapter(FakePlatformConfig(extra=extra))
    events: List[Any] = []

    async def _record(event: Any) -> None:
        events.append(event)

    a.handle_message = _record  # type: ignore[assignment]
    return a, events


# --------------------------------------------------------------------------
# Pure policy
# --------------------------------------------------------------------------


def test_dedupe_cache_bounds() -> None:
    d = DedupeCache(max_items=3, ttl_s=3600)
    for i in range(5):
        d.add(f"x{i}")
    assert len(d) == 3 and not d.seen("x0") and d.seen("x4")
    d2 = DedupeCache(max_items=10, ttl_s=0.0)
    d2.add("y")
    time.sleep(0.01)
    assert not d2.seen("y")


def test_reconnect_delay_policy() -> None:
    # reconnect frame: after_ms .. 2*after_ms
    o = StreamOutcome(close_code=1012, reconnect_reason="deploy", reconnect_after_ms=1000)
    assert all(1.0 <= reconnect_delay(o, 3, 0) <= 2.0 for _ in range(50))
    # token_rotated after_ms 0 → now
    o = StreamOutcome(close_code=1012, reconnect_reason="token_rotated", reconnect_after_ms=0)
    assert reconnect_delay(o, 5, 0) == 0.0
    # 4409: long backoff, escalating, capped
    o = StreamOutcome(close_code=4409, reconnect_reason="superseded", reconnect_after_ms=0)
    assert 60.0 <= reconnect_delay(o, 0, 1) <= 90.0
    assert 300.0 <= reconnect_delay(o, 0, 9) <= 450.0
    # Retry-After honoured
    o = StreamOutcome(http_status=503, retry_after_s=2.0)
    assert reconnect_delay(o, 0, 0) >= 2.0
    # full jitter within cap
    o = StreamOutcome(close_code=1013)
    assert all(0.0 <= reconnect_delay(o, 20, 0) <= 30.0 for _ in range(50))
    # classification
    assert StreamOutcome(http_status=404).unavailable
    assert StreamOutcome(http_status=426).unavailable
    assert not StreamOutcome(http_status=503).unavailable
    assert StreamOutcome(close_code=4401).auth_rejected
    assert StreamOutcome(http_status=401).auth_rejected


def test_transport_config_parsing(monkeypatch) -> None:
    monkeypatch.delenv("CHATLYTICS_TRANSPORT", raising=False)
    mk = lambda **e: ChatlyticsAdapter(FakePlatformConfig(extra={"bot_token": TOKEN_A, **e}))
    assert mk().transport == "auto"
    assert mk(transport="LongPoll").transport == "longpoll"
    assert mk(transport="bogus").transport == "auto"
    monkeypatch.setenv("CHATLYTICS_TRANSPORT", "stream")
    assert mk(transport="longpoll").transport == "stream"  # env wins


# --------------------------------------------------------------------------
# Stream happy path, ack, dedupe, resume
# --------------------------------------------------------------------------


async def test_stream_happy_path_ready_caps_ack(server: FakeServer) -> None:
    a, events = _adapter(server)
    server.push(TOKEN_A, _envelope(CHAT_A, SESSION_A, "hello", "m1"))
    assert await a.connect()
    try:
        await _until(lambda: len(events) == 1 and server.acks)
        conn = server.conns_for(TOKEN_A)[0]
        assert conn["protocol"] == "chatlytics.bot.v1"
        assert conn["ready"]["t"] == "ready" and conn["ready"]["caps"] == ["control"]
        assert "cursor" not in conn["ready"]  # nothing acked yet
        assert events[0].source.chat_id == CHAT_A
        assert server.acks == [(TOKEN_A, "cur-1")]
        assert a._conns[0].cursor == "cur-1"
        assert a._conns[0].transport == "stream"
        assert server.lp_gets == []  # never long-polled
        st = a.inbound_status()
        assert st[0]["transport"] == "stream" and st[0]["configured"] == "auto"
        assert "sk_bot_" not in json.dumps(st)
        # session threading for the reply path
        assert a._resolve_session_for_chat(CHAT_A) == SESSION_A
    finally:
        await a.disconnect()


async def test_stream_dedupes_on_env_id_and_still_acks(server: FakeServer) -> None:
    a, events = _adapter(server)
    server.push(TOKEN_A, _envelope(CHAT_A, SESSION_A, "one", "m1"), env_id="SAME")
    server.push(TOKEN_A, _envelope(CHAT_A, SESSION_A, "one again", "m1"), env_id="SAME")
    server.push(TOKEN_A, _envelope(CHAT_A, SESSION_A, "two", "m2"), env_id="OTHER")
    assert await a.connect()
    try:
        await _until(lambda: (TOKEN_A, "cur-3") in server.acks)
        assert [e.text for e in events] == ["one", "two"]
        assert [c for _, c in server.acks] == ["cur-1", "cur-2", "cur-3"]
    finally:
        await a.disconnect()


async def test_reconnect_resumes_cursor_and_dedupes_redelivery(server: FakeServer, monkeypatch) -> None:
    a, events = _adapter(server)
    server.push(TOKEN_A, _envelope(CHAT_A, SESSION_A, "first", "m1"))
    server.close_next[TOKEN_A] = [(1012, {"reason": "deploy", "after_ms": 50})]
    assert await a.connect()
    try:
        await _until(lambda: len(server.conns_for(TOKEN_A)) == 2 and server.conns_for(TOKEN_A)[1]["ready"])
        second = server.conns_for(TOKEN_A)[1]
        assert second["ready"]["cursor"] == "cur-1"  # resume from the acked position
        assert second["at"] - server.conns_for(TOKEN_A)[0]["at"] >= 0.05
        # A redelivery of the same id on the new socket is acked, not re-run.
        server.next_seq[TOKEN_A] = 1
        server.push(TOKEN_A, _envelope(CHAT_A, SESSION_A, "first", "m1"))
        await _until(lambda: server.acks.count((TOKEN_A, "cur-2")) == 1)
        assert [e.text for e in events] == ["first"]
    finally:
        await a.disconnect()


async def test_answers_server_pings(server: FakeServer) -> None:
    """Server heartbeat 0.2s closes a socket that does not pong."""
    server.heartbeat = 0.2
    a, _ = _adapter(server)
    assert await a.connect()
    try:
        await _until(lambda: server.conns_for(TOKEN_A))
        await asyncio.sleep(1.2)
        assert len(server.conns_for(TOKEN_A)) == 1  # never dropped
        assert a._conns[0].transport == "stream"
    finally:
        await a.disconnect()


async def test_watchdog_reconnects_a_silent_socket(server: FakeServer, monkeypatch) -> None:
    monkeypatch.setattr(stream_mod, "WATCHDOG_S", 0.3)
    server.silent = True  # no pings (aiohttp server without heartbeat), no frames
    a, _ = _adapter(server)
    assert await a.connect()
    try:
        await _until(lambda: len(server.conns_for(TOKEN_A)) >= 2, timeout=5)
    finally:
        await a.disconnect()


# --------------------------------------------------------------------------
# Fallback + re-probe
# --------------------------------------------------------------------------


async def test_404_falls_back_to_longpoll_then_switches_to_stream(server: FakeServer) -> None:
    server.stream_enabled = False
    server.lp_pending[TOKEN_A] = [_envelope(CHAT_A, SESSION_A, "via longpoll", "m1")]
    a, events = _adapter(server)
    a.stream_reprobe_s = 0.4
    assert await a.connect()
    try:
        await _until(lambda: len(events) == 1 and server.lp_acks)
        assert a._conns[0].transport == "longpoll"
        assert "re-probing" in a._conns[0].transport_detail
        assert server.lp_acks[0] == (TOKEN_A, "lp-1")
        # Server flag flips on: the next re-probe moves the bot to the stream
        # and resumes from the long-poll cursor.
        server.stream_enabled = True
        server.push(TOKEN_A, _envelope(CHAT_A, SESSION_A, "via stream", "m2"))
        await _until(lambda: len(events) == 2, timeout=5)
        assert a._conns[0].transport == "stream"
        assert server.conns_for(TOKEN_A)[0]["ready"]["cursor"] == "lp-1"
        n = len(server.lp_gets)
        await asyncio.sleep(0.6)
        assert len(server.lp_gets) == n  # long-poll stopped after switch-over
    finally:
        await a.disconnect()


async def test_reprobe_keeps_longpoll_while_still_404(server: FakeServer) -> None:
    server.stream_enabled = False
    a, _ = _adapter(server)
    a.stream_reprobe_s = 0.2
    assert await a.connect()
    try:
        await _until(lambda: len(server.upgrade_attempts) >= 3, timeout=5)
        assert a._conns[0].transport == "longpoll"
        assert server.lp_gets  # still receiving between probes
    finally:
        await a.disconnect()


async def test_transport_longpoll_never_touches_stream(server: FakeServer) -> None:
    a, events = _adapter(server, transport="longpoll")
    server.lp_pending[TOKEN_A] = [_envelope(CHAT_A, SESSION_A, "lp", "m1")]
    assert await a.connect()
    try:
        await _until(lambda: len(events) == 1)
        assert server.upgrade_attempts == []
        assert a._conns[0].transport == "longpoll"
    finally:
        await a.disconnect()


async def test_transport_stream_never_falls_back(server: FakeServer, monkeypatch) -> None:
    monkeypatch.setattr(adapter_mod, "_STREAM_FORCED_RETRY_S", 0.1)
    server.stream_enabled = False
    a, _ = _adapter(server, transport="stream")
    assert await a.connect()
    try:
        await _until(lambda: len(server.upgrade_attempts) >= 2)
        assert server.lp_gets == []
        assert a._conns[0].transport == "stopped"
    finally:
        await a.disconnect()


async def test_503_is_transient_not_a_fallback(server: FakeServer) -> None:
    server.refuse[TOKEN_A] = [(503, {"Retry-After": "0"}), (503, {"Retry-After": "0"})]
    server.push(TOKEN_A, _envelope(CHAT_A, SESSION_A, "after 503", "m1"))
    a, events = _adapter(server)
    assert await a.connect()
    try:
        await _until(lambda: len(events) == 1, timeout=8)
        assert len(server.upgrade_attempts) == 3
        assert server.lp_gets == []  # a transient refusal never falls back
    finally:
        await a.disconnect()


# --------------------------------------------------------------------------
# Close codes
# --------------------------------------------------------------------------


async def test_1013_backs_off_and_reconnects(server: FakeServer) -> None:
    server.close_next[TOKEN_A] = [(1013, None)]
    a, _ = _adapter(server)
    assert await a.connect()
    try:
        await _until(lambda: len(server.conns_for(TOKEN_A)) == 2 and server.conns_for(TOKEN_A)[1]["ready"])
        assert server.lp_gets == []
    finally:
        await a.disconnect()


async def test_1012_token_rotated_reconnects_immediately(server: FakeServer) -> None:
    server.close_next[TOKEN_A] = [(1012, {"reason": "token_rotated", "after_ms": 0})]
    a, _ = _adapter(server)
    assert await a.connect()
    try:
        await _until(lambda: len(server.conns_for(TOKEN_A)) == 2, timeout=3)
    finally:
        await a.disconnect()


async def test_4409_superseded_backs_off_long_then_reclaims(server: FakeServer, monkeypatch) -> None:
    monkeypatch.setattr(stream_mod, "SUPERSEDED_BACKOFF_S", 0.5)
    server.close_next[TOKEN_A] = [(4409, {"reason": "superseded", "after_ms": 0})]
    a, _ = _adapter(server)
    assert await a.connect()
    try:
        await _until(lambda: len(server.conns_for(TOKEN_A)) == 2, timeout=5)
        c1, c2 = server.conns_for(TOKEN_A)
        assert c2["at"] - c1["at"] >= 0.5  # not the after_ms:0 hot reconnect
    finally:
        await a.disconnect()


async def test_4401_stops_the_bot(server: FakeServer, monkeypatch) -> None:
    monkeypatch.setattr(adapter_mod, "_STREAM_AUTH_RETRY_S", 30.0)
    server.close_next[TOKEN_A] = [(4401, {"reason": "token_rotated", "after_ms": 0})]
    a, _ = _adapter(server)
    assert await a.connect()
    try:
        await _until(lambda: a._conns[0].transport == "stopped")
        await asyncio.sleep(0.5)
        assert len(server.upgrade_attempts) == 1  # no reconnect loop
        assert server.lp_gets == []  # and no silent long-poll fallback
        assert "token rejected" in a._conns[0].transport_detail
    finally:
        await a.disconnect()


async def test_http_401_handshake_stops_the_bot(server: FakeServer, monkeypatch) -> None:
    monkeypatch.setattr(adapter_mod, "_STREAM_AUTH_RETRY_S", 30.0)
    server.refuse[TOKEN_A] = [(401, {})]
    a, _ = _adapter(server)
    assert await a.connect()
    try:
        await _until(lambda: a._conns[0].transport == "stopped")
        await asyncio.sleep(0.3)
        assert len(server.upgrade_attempts) == 1
    finally:
        await a.disconnect()


# --------------------------------------------------------------------------
# Multiplex: two bots → two sockets → replies on the matching bearer
# --------------------------------------------------------------------------


async def test_multiplex_two_sockets_and_reply_bearer(server: FakeServer) -> None:
    a, events = _adapter(server, extra_tokens=[TOKEN_B])

    async def _reply(event: Any) -> None:
        events.append(event)
        await a.send(event.source.chat_id, f"re: {event.text}")

    a.handle_message = _reply  # type: ignore[assignment]
    server.push(TOKEN_A, _envelope(CHAT_A, SESSION_A, "to A", "ma"))
    server.push(TOKEN_B, _envelope(CHAT_B, SESSION_B, "to B", "mb"))
    assert await a.connect()
    try:
        await _until(lambda: len(server.sends) == 2)
        assert len(server.conns_for(TOKEN_A)) == 1 and len(server.conns_for(TOKEN_B)) == 1
        by_chat = {body["chatId"]: tok for tok, body in server.sends}
        assert by_chat == {CHAT_A: TOKEN_A, CHAT_B: TOKEN_B}
        assert (TOKEN_A, "cur-1") in server.acks and (TOKEN_B, "cur-1") in server.acks
        st = {s["fp"]: s["transport"] for s in a.inbound_status()}
        assert list(st.values()) == ["stream", "stream"]
    finally:
        await a.disconnect()


async def test_multiplex_one_bot_rejected_other_keeps_streaming(server: FakeServer, monkeypatch) -> None:
    monkeypatch.setattr(adapter_mod, "_STREAM_AUTH_RETRY_S", 30.0)
    server.refuse[TOKEN_B] = [(401, {})]
    a, events = _adapter(server, extra_tokens=[TOKEN_B])
    server.push(TOKEN_A, _envelope(CHAT_A, SESSION_A, "A ok", "ma"))
    assert await a.connect()
    try:
        await _until(lambda: len(events) == 1 and a._conns[1].transport == "stopped")
        assert a._conns[0].transport == "stream"
    finally:
        await a.disconnect()


async def test_health_tool_reports_transport(server: FakeServer) -> None:
    from chatlytics_hermes.tools import chatlytics_health

    a, _ = _adapter(server)
    assert await a.connect()
    try:
        await _until(lambda: a._conns[0].transport == "stream")
        res = await chatlytics_health(a.client, adapter=a)
        assert res["success"] is True
        assert res["inbound"][0]["transport"] == "stream"
    finally:
        await a.disconnect()


def test_doctor_stream_probe_is_non_consuming() -> None:
    """The doctor probes WITHOUT the subprotocol or a takeover: 426 = on, 404 = off."""
    import httpx

    from chatlytics_hermes.doctor import check_stream

    seen: List[httpx.Request] = []

    def _mk(status: int):
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(status)
        return httpx.Client(base_url="http://x", transport=httpx.MockTransport(handler))

    ok, msg = check_stream(_mk(426))
    assert ok and "ENABLED" in msg
    ok, msg = check_stream(_mk(404))
    assert ok and "long-poll" in msg
    ok, _ = check_stream(_mk(503))
    assert not ok
    assert all("sec-websocket-protocol" not in r.headers for r in seen)
