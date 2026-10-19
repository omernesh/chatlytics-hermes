"""chatlytics#451 — a media message and the text sent right after it are ONE turn.

hermes-agent merges a follow-up into the pending turn only for a PHOTO burst,
so a video followed by "forward this to X" reached the agent as two turns and it
answered twice. The adapter now holds a media envelope for a short window and
folds in a text-only envelope from the same sender, chat and bot connection.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from chatlytics_hermes.adapter import ChatlyticsAdapter
from tests._fixtures import FakePlatformConfig
from tests.test_longpoll import BASE_URL, BOT_TOKEN, SESSION_ID

OWNER = "972500000451@c.us"
OTHER = "972500000452@c.us"
GROUP = "120363000000000451@g.us"
WINDOW_MS = 80


def _adapter(**extra_over: Any) -> ChatlyticsAdapter:
    extra: Dict[str, Any] = {
        "base_url": BASE_URL,
        "bot_token": BOT_TOKEN,
        "inbound_mode": "longpoll",
        "media_coalesce_ms": WINDOW_MS,
    }
    extra.update(extra_over)
    return ChatlyticsAdapter(FakePlatformConfig(extra=extra))


def _record(adapter: ChatlyticsAdapter) -> List[Any]:
    events: List[Any] = []

    async def _rec(event: Any) -> None:
        events.append(event)

    adapter.handle_message = _rec  # type: ignore[assignment]
    return events


def _env(text: str, sender: str = OWNER, chat: str = GROUP, media: bool = False) -> Dict[str, Any]:
    env: Dict[str, Any] = {
        "bot_token": BOT_TOKEN,
        "session_id": SESSION_ID,
        "chat_type": "group" if chat.endswith("@g.us") else "dm",
        "entity_jid": chat,
        "sender_jid": sender,
        "text": text,
        "dispatch": {"reason": "trigger-single", "god_mode": False},
        "ts": 1791283983000,
    }
    if media:
        env.update(has_media=True, media_type="video", message_id="AC61")
    return env


async def _settle() -> None:
    await asyncio.sleep(WINDOW_MS / 1000 * 2.5)


async def test_video_then_text_is_one_turn() -> None:
    a = _adapter()
    events = _record(a)
    await a._dispatch_envelope(_env("[media] mime=video/mp4 url=https://x/m/1", media=True))
    assert events == []  # held, waiting for a follow-up
    await a._dispatch_envelope(_env("Forward this to the X group"))
    assert len(events) == 1
    assert events[0].text == "Forward this to the X group\n[media] mime=video/mp4 url=https://x/m/1"
    await _settle()
    assert len(events) == 1  # the cancelled timer never re-sends the media


async def test_media_alone_is_released_after_the_window() -> None:
    a = _adapter()
    events = _record(a)
    await a._dispatch_envelope(_env("[media] video", media=True))
    assert events == []
    await _settle()
    assert [e.text for e in events] == ["[media] video"]


async def test_text_first_is_never_held() -> None:
    a = _adapter()
    events = _record(a)
    await a._dispatch_envelope(_env("hello"))
    assert [e.text for e in events] == ["hello"]


async def test_other_sender_or_chat_does_not_merge() -> None:
    a = _adapter()
    events = _record(a)
    await a._dispatch_envelope(_env("[media] video", media=True))
    await a._dispatch_envelope(_env("from someone else", sender=OTHER))
    await a._dispatch_envelope(_env("same sender, other chat", chat=OWNER))
    assert [e.text for e in events] == ["from someone else", "same sender, other chat"]
    await _settle()
    assert [e.text for e in events][-1] == "[media] video"
    assert len(events) == 3


async def test_media_burst_keeps_one_turn_per_media_and_the_last_takes_the_text() -> None:
    a = _adapter()
    events = _record(a)
    await a._dispatch_envelope(_env("[media] one", media=True))
    await a._dispatch_envelope(_env("[media] two", media=True))
    assert [e.text for e in events] == ["[media] one"]
    await a._dispatch_envelope(_env("caption"))
    assert [e.text for e in events] == ["[media] one", "caption\n[media] two"]


async def test_text_after_the_window_is_its_own_turn() -> None:
    a = _adapter()
    events = _record(a)
    await a._dispatch_envelope(_env("[media] video", media=True))
    await _settle()
    await a._dispatch_envelope(_env("late text"))
    assert [e.text for e in events] == ["[media] video", "late text"]


async def test_zero_window_disables_coalescing() -> None:
    a = _adapter(media_coalesce_ms=0)
    events = _record(a)
    await a._dispatch_envelope(_env("[media] video", media=True))
    await a._dispatch_envelope(_env("text"))
    assert [e.text for e in events] == ["[media] video", "text"]


async def test_env_var_overrides_and_bad_values_fall_back(monkeypatch) -> None:
    monkeypatch.setenv("CHATLYTICS_MEDIA_COALESCE_MS", "0")
    assert _adapter().media_coalesce_s == 0
    monkeypatch.setenv("CHATLYTICS_MEDIA_COALESCE_MS", "nope")
    assert _adapter().media_coalesce_s == 1.5
    monkeypatch.delenv("CHATLYTICS_MEDIA_COALESCE_MS")
    assert _adapter(media_coalesce_ms=None).media_coalesce_s == 1.5
    assert _adapter(media_coalesce_ms=99_999).media_coalesce_s == 10.0


async def test_disconnect_releases_a_held_media_turn() -> None:
    a = _adapter()
    events = _record(a)
    await a._dispatch_envelope(_env("[media] video", media=True))
    await a.disconnect()
    assert [e.text for e in events] == ["[media] video"]
    await _settle()
    assert len(events) == 1
