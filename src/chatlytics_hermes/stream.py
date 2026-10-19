"""WebSocket bot stream client — chatlytics ``chatlytics.bot.v1`` (v5.0.0).

The chatlytics server delivers a bot's inbound envelopes over ONE persistent
WebSocket (``GET /api/v1/bot/stream``) instead of long-poll. This module is
the transport only: it knows the wire protocol and the reconnect contract,
and nothing about Hermes. The adapter (:meth:`ChatlyticsAdapter._inbound_loop`)
owns the per-bot loop that chooses between this and long-poll.

Wire contract (server: chatlytics.ai ``src/botstream/protocol.ts``; design
``docs/architecture/bot-update-delivery-v2.md`` §3):

- Upgrade with ``Authorization: Bearer sk_bot_*`` (header ONLY — a token in
  the URL is refused) and subprotocol ``chatlytics.bot.v1``. No compression.
- Server sends ``hello``; we answer ``ready {caps:["control"], cursor?}``
  within 10 s. ``caps`` drives the server's control-capability check, so
  conversation commands and question resolutions keep flowing.
- Server sends one ``env {seq, cursor, id, envelope, delivery_count}`` per
  envelope; we dispatch it, then send a cumulative ``ack {cursor}``.
- ``env.id`` is stable across redelivery, failover and token rotation, so it
  is the dedupe key (:class:`DedupeCache`). Delivery is at-least-once.
- Server pings every 30 s (below Caddy's 120 s read_timeout). We answer each
  ping explicitly and reconnect if nothing at all arrives for 75 s.

Pings are answered by a dedicated reader task, and envelopes are dispatched
by a separate worker task, so a slow dispatch can never starve the pong and
get the socket killed by the server's 60 s heartbeat check.

INV-02: the bearer is only ever placed in the upgrade header. Nothing here
logs it; callers pass a fingerprint for log lines.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

SUBPROTOCOL: str = "chatlytics.bot.v1"
STREAM_PATH: str = "/api/v1/bot/stream"

#: Client watchdog: no ping and no frame for this long means the socket is
#: dead (protocol §3.2 — server pings every 30 s).
WATCHDOG_S: float = 75.0
#: ``hello`` must arrive within this long after the upgrade.
HELLO_TIMEOUT_S: float = 15.0
#: Upgrade handshake bound (DNS + TCP + TLS + HTTP 101).
HANDSHAKE_TIMEOUT_S: float = 30.0
#: A connection that stayed up this long resets the backoff counter (§3.6).
HEALTHY_RESET_S: float = 60.0
#: Full-jitter backoff cap and base (§3.6): random(0, min(30, 0.5 * 2^n)).
BACKOFF_CAP_S: float = 30.0
BACKOFF_BASE_S: float = 0.5
#: 4409 (another consumer took the socket): back off long, never hot-loop.
SUPERSEDED_BACKOFF_S: float = 60.0
SUPERSEDED_BACKOFF_CAP_S: float = 300.0
#: Dedupe window (§3.6: at least the last 1,000 ids or 24 h).
DEDUPE_MAX: int = 2000
DEDUPE_TTL_S: float = 24 * 3600.0

# §3.3 close codes.
CLOSE_NORMAL = 1000
CLOSE_SERVICE_RESTART = 1012
CLOSE_TRY_AGAIN_LATER = 1013
CLOSE_PROTOCOL_ERROR = 4400
CLOSE_TOKEN_INVALID = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_SLOW_CONSUMER = 4408
CLOSE_SUPERSEDED = 4409

#: Handshake statuses that mean "this server has no stream route" — the ONLY
#: answers that make ``transport: auto`` fall back to long-poll. 404 is the
#: server's flag-off answer (byte-identical to a missing route); 426 is an
#: old server that predates the subprotocol. Everything else is transient
#: (503/429/409/5xx/transport) or an auth verdict — retry the stream.
UNAVAILABLE_STATUSES = frozenset({404, 426})


class DedupeCache:
    """Bounded LRU of delivered ``env.id`` values (count + age bound)."""

    def __init__(self, max_items: int = DEDUPE_MAX, ttl_s: float = DEDUPE_TTL_S) -> None:
        self._items: "OrderedDict[str, float]" = OrderedDict()
        self._max = max_items
        self._ttl = ttl_s

    def seen(self, env_id: str) -> bool:
        ts = self._items.get(env_id)
        if ts is None:
            return False
        if time.monotonic() - ts > self._ttl:
            self._items.pop(env_id, None)
            return False
        return True

    def add(self, env_id: str) -> None:
        self._items[env_id] = time.monotonic()
        self._items.move_to_end(env_id)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


@dataclass
class StreamOutcome:
    """How one stream connection attempt ended. Drives the reconnect policy."""

    #: HTTP status of a refused upgrade (None once the socket opened).
    http_status: Optional[int] = None
    #: ``Retry-After`` seconds from a refused upgrade.
    retry_after_s: Optional[float] = None
    #: WebSocket close code (None when the socket never opened).
    close_code: Optional[int] = None
    #: Last ``reconnect`` frame seen before the close.
    reconnect_reason: Optional[str] = None
    reconnect_after_ms: Optional[int] = None
    #: ``error`` frame code, transport-error text, or watchdog note.
    error: Optional[str] = None
    hello: bool = False
    connected_s: float = 0.0
    envelopes: int = 0

    @property
    def unavailable(self) -> bool:
        return self.http_status in UNAVAILABLE_STATUSES

    @property
    def auth_rejected(self) -> bool:
        return self.http_status in (401, 403) or self.close_code in (
            CLOSE_TOKEN_INVALID,
            CLOSE_FORBIDDEN,
        )

    def describe(self) -> str:
        if self.http_status is not None:
            return f"upgrade refused HTTP {self.http_status}" + (
                f" ({self.error})" if self.error else ""
            )
        if self.close_code is not None:
            extra = f", reconnect={self.reconnect_reason}" if self.reconnect_reason else ""
            return f"closed {self.close_code}{extra}" + (f" ({self.error})" if self.error else "")
        return self.error or "disconnected"


def full_jitter(attempt: int) -> float:
    """§3.6 backoff: ``random(0, min(30, 0.5 * 2^attempt))``."""
    return random.uniform(0.0, min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** max(0, attempt))))


def reconnect_delay(outcome: StreamOutcome, attempt: int, superseded_streak: int) -> float:
    """Seconds to wait before the next stream attempt (non-terminal outcomes).

    - ``reconnect`` frame (deploy 1012, token_rotated 1012, redis 1013, …):
      ``after_ms + random(0, after_ms)`` — ``after_ms: 0`` reconnects now.
    - 4409 superseded / fenced: another consumer holds this bot. Long,
      escalating, jittered wait (60 s → 300 s cap) so two gateways on one
      token do not flap, while a one-off (a takeover during a restart)
      still self-heals.
    - refused upgrade with ``Retry-After`` (409/429/503): that value plus up
      to 1 s jitter, never less than the jittered backoff floor.
    - anything else: full-jitter backoff.
    """
    if outcome.close_code == CLOSE_SUPERSEDED:
        base = min(SUPERSEDED_BACKOFF_CAP_S, SUPERSEDED_BACKOFF_S * (2 ** max(0, superseded_streak - 1)))
        return base + random.uniform(0.0, base / 2)
    if outcome.reconnect_after_ms is not None and outcome.close_code is not None:
        after = max(0, outcome.reconnect_after_ms) / 1000.0
        return after + random.uniform(0.0, after)
    if outcome.retry_after_s is not None:
        return max(outcome.retry_after_s + random.uniform(0.0, 1.0), full_jitter(attempt))
    delay = full_jitter(attempt)
    if outcome.close_code == CLOSE_PROTOCOL_ERROR:
        # "fix the client, back off — do not hot-loop"
        delay = max(delay, 5.0 + random.uniform(0.0, 5.0))
    return delay


def stream_url(base_url: str) -> str:
    """``https://host`` → ``wss://host/api/v1/bot/stream`` (http → ws)."""
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return base + STREAM_PATH


def _parse_retry_after(headers: Any) -> Optional[float]:
    try:
        raw = headers.get("Retry-After") if headers is not None else None
    except Exception:  # noqa: BLE001
        return None
    if raw is None:
        return None
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return None


EnvelopeHandler = Callable[[Dict[str, Any]], Awaitable[None]]


async def run_stream_once(
    *,
    base_url: str,
    token: str,
    user_agent: str,
    cursor: Optional[str],
    dedupe: DedupeCache,
    on_envelope: EnvelopeHandler,
    on_cursor: Callable[[str], None],
    on_open: Optional[Callable[[Dict[str, Any]], None]] = None,
    caps: Optional[List[str]] = None,
    max_inflight: Optional[int] = None,
    should_run: Callable[[], bool] = lambda: True,
    log_tag: str = "",
) -> StreamOutcome:
    """Open ONE stream connection and serve it until it closes.

    Never raises except :class:`asyncio.CancelledError`; every other ending
    (refused upgrade, close frame, transport error, watchdog) is returned as
    a :class:`StreamOutcome` for the caller's reconnect policy.

    ``on_envelope`` is awaited once per NEW ``env.id`` (duplicates are acked
    without dispatch); the cumulative ack for that envelope is sent after it
    returns or raises — a dispatch exception is logged and still acked, the
    same contract as the long-poll consumer (a nack would re-run a turn that
    may already have replied). ``on_cursor`` receives every acked cursor so
    the caller can resume from it, on either transport.
    """
    outcome = StreamOutcome()
    headers = {"Authorization": f"Bearer {token}", "User-Agent": user_agent}
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, connect=15.0, sock_connect=15.0),
        trust_env=True,
    )
    ws: Optional[aiohttp.ClientWebSocketResponse] = None
    opened_at: Optional[float] = None
    try:
        try:
            ws = await asyncio.wait_for(
                session.ws_connect(
                    stream_url(base_url),
                    protocols=(SUBPROTOCOL,),
                    headers=headers,
                    autoping=False,  # pings are answered by the reader below
                    autoclose=True,
                    heartbeat=None,
                    compress=0,
                    max_msg_size=4 * 1024 * 1024,
                ),
                timeout=HANDSHAKE_TIMEOUT_S,
            )
        except aiohttp.WSServerHandshakeError as exc:
            outcome.http_status = exc.status
            outcome.retry_after_s = _parse_retry_after(getattr(exc, "headers", None))
            outcome.error = (exc.message or "")[:200] or None
            return outcome
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            outcome.error = f"transport error ({type(exc).__name__}: {exc})"[:300]
            return outcome

        opened_at = time.monotonic()
        queue: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue()

        async def _send(frame: Dict[str, Any]) -> None:
            assert ws is not None
            await ws.send_str(json.dumps(frame, separators=(",", ":")))

        async def _worker() -> None:
            while True:
                frame = await queue.get()
                if frame is None:
                    return
                env_id = frame.get("id")
                env_cursor = frame.get("cursor")
                envelope = frame.get("envelope")
                if isinstance(env_id, str) and dedupe.seen(env_id):
                    logger.debug("%sstream: duplicate env id=%s (seq=%s) — acking without dispatch", log_tag, env_id, frame.get("seq"))
                else:
                    if isinstance(env_id, str):
                        dedupe.add(env_id)
                    if isinstance(envelope, dict):
                        outcome.envelopes += 1
                        try:
                            await on_envelope(envelope)
                        except asyncio.CancelledError:
                            raise
                        except Exception:  # noqa: BLE001 -- one bad envelope must not stall the stream
                            logger.exception("%sstream: failed to dispatch one envelope (seq=%s); acking and continuing", log_tag, frame.get("seq"))
                if isinstance(env_cursor, str) and env_cursor:
                    if ws.closed:
                        return  # unacked → redelivered on resume, dedupe skips it
                    await _send({"t": "ack", "cursor": env_cursor})
                    on_cursor(env_cursor)

        worker: Optional["asyncio.Task[None]"] = None
        try:
            # --- hello → ready -------------------------------------------
            deadline = time.monotonic() + HELLO_TIMEOUT_S
            while True:
                msg = await ws.receive(timeout=max(0.1, deadline - time.monotonic()))
                if msg.type == aiohttp.WSMsgType.PING:
                    await ws.pong(msg.data)
                    continue
                if msg.type == aiohttp.WSMsgType.TEXT:
                    break
                outcome.close_code = ws.close_code
                outcome.error = "closed before hello"
                return outcome
            hello = json.loads(msg.data)
            if not isinstance(hello, dict) or hello.get("t") != "hello":
                outcome.error = f"first frame was not hello (t={hello.get('t') if isinstance(hello, dict) else '?'})"
                await ws.close(code=CLOSE_PROTOCOL_ERROR)
                outcome.close_code = CLOSE_PROTOCOL_ERROR
                return outcome
            outcome.hello = True
            ready: Dict[str, Any] = {"t": "ready", "caps": list(caps if caps is not None else ["control"])}
            if cursor:
                ready["cursor"] = cursor
            if max_inflight:
                ready["max_inflight"] = int(max_inflight)
            await _send(ready)
            if on_open is not None:
                with contextlib.suppress(Exception):
                    on_open(hello)
            worker = asyncio.create_task(_worker())

            # --- reader: pings, frames, watchdog ------------------------
            while should_run():
                if worker.done():
                    exc = worker.exception() if not worker.cancelled() else None
                    outcome.error = f"worker stopped ({type(exc).__name__ if exc else 'closed'})"
                    break
                try:
                    msg = await ws.receive(timeout=WATCHDOG_S)
                except asyncio.TimeoutError:
                    outcome.error = f"no ping or frame for {WATCHDOG_S:.0f}s (watchdog)"
                    break
                if msg.type == aiohttp.WSMsgType.PING:
                    await ws.pong(msg.data)
                    continue
                if msg.type == aiohttp.WSMsgType.PONG:
                    continue
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        frame = json.loads(msg.data)
                    except ValueError:
                        logger.warning("%sstream: non-JSON frame ignored", log_tag)
                        continue
                    if not isinstance(frame, dict):
                        continue
                    t = frame.get("t")
                    if t == "env":
                        queue.put_nowait(frame)
                    elif t == "reconnect":
                        outcome.reconnect_reason = str(frame.get("reason") or "") or None
                        try:
                            outcome.reconnect_after_ms = int(frame.get("after_ms") or 0)
                        except (TypeError, ValueError):
                            outcome.reconnect_after_ms = 0
                    elif t == "error":
                        outcome.error = f"{frame.get('code')}: {str(frame.get('message') or '')[:200]}"
                        logger.warning("%sstream: server error frame %s", log_tag, outcome.error)
                    elif t == "acked":
                        pass
                    else:
                        logger.debug("%sstream: unknown frame t=%r ignored", log_tag, t)
                    continue
                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                    break
                if msg.type == aiohttp.WSMsgType.ERROR:
                    outcome.error = f"socket error ({ws.exception()!r})"[:300]
                    break
                # BINARY is not part of chatlytics.bot.v1 — ignore it.
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, RuntimeError) as exc:
            outcome.error = outcome.error or f"{type(exc).__name__}: {exc}"[:300]
        finally:
            if worker is not None and not worker.done():
                # Let the in-flight dispatch finish (its ack may fail on a
                # closed socket — that envelope is redelivered and deduped),
                # then stop. Queued-but-undispatched frames are redelivered.
                while not queue.empty():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        queue.get_nowait()
                queue.put_nowait(None)
                try:
                    await asyncio.wait_for(asyncio.shield(worker), timeout=30.0)
                except asyncio.TimeoutError:
                    worker.cancel()
                    with contextlib.suppress(BaseException):
                        await worker
                except asyncio.CancelledError:
                    worker.cancel()
                    raise
                except Exception:  # noqa: BLE001
                    pass
        if outcome.close_code is None:
            if not ws.closed:
                with contextlib.suppress(Exception):
                    await ws.close()
            outcome.close_code = ws.close_code
        return outcome
    finally:
        if opened_at is not None:
            outcome.connected_s = time.monotonic() - opened_at
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()
        with contextlib.suppress(Exception):
            await session.close()
