"""Survivability diagnostics for the chatlytics Hermes plugin (v4.2.0, P3).

Three small, dependency-light primitives shared by ``adapter.register`` /
``ChatlyticsAdapter.connect`` and the ``python -m chatlytics_hermes.doctor``
self-check CLI:

1. **No-downgrade guard** (:func:`check_hermes_agent_version`) — detects the
   historical failure where a plain ``pip install`` / ``uv pip install`` of an
   old ``chatlytics-hermes`` pin (``hermes-agent==0.14.0`` in v4.1.1) silently
   DOWNGRADED the production hermes-agent (0.15.1 → 0.14.0). The guard only
   ever *logs*; it must never break an otherwise-working load.
2. **base_url symptom → fix mapping** (:func:`map_connect_error`) — the same
   handful of misconfigurations keep recurring (dead Tailscale-style IP →
   30s connect timeout; wrong host/port → connection refused; rotated token →
   401; Cloudflare tunnel longpoll limit → 502). Map them to the known fix
   once, here, so connect(), the longpoll loop, and doctor all say the same
   actionable thing.
3. **bot/me payload helpers** (:func:`extract_bot_name`) — tolerant name
   extraction shared by the boot identity probe and doctor.

Kept free of any ``gateway.*`` (hermes-agent) imports so it is importable in
environments without hermes-agent installed (mirrors the adapter's
``try/except ImportError`` discipline).
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

import httpx

# Minimum hermes-agent version this plugin supports. MUST stay in lockstep
# with the dependency floor in pyproject.toml (``hermes-agent>=0.14,<1.0``,
# fixed in v4.1.2 after the ==0.14.0 pin downgraded production).
HERMES_AGENT_FLOOR: Tuple[int, int] = (0, 14)
HERMES_AGENT_DIST: str = "hermes-agent"

# Canonical base_url values (see chatlytics.ai CLAUDE.md / QMD
# "chatlytics DNS names"): LAN for on-prem gateways (no Cloudflare longpoll
# limit), DNS for everything else. Tailscale IPs are the known-bad shape —
# the hpg5↔hpg6 Tailscale data-plane half-connects TCP and hangs HTTP.
LAN_BASE_URL: str = "http://192.168.1.133:8050"
DNS_BASE_URL: str = "https://node.chatlytics.ai"


def parse_version(version: str) -> Tuple[int, ...]:
    """Parse the leading numeric components of a version string.

    ``"0.15.1"`` -> ``(0, 15, 1)``; ``"0.15.1rc1"`` -> ``(0, 15, 1)``
    (the trailing pre-release tag on the last component is ignored — close
    enough for a floor comparison); ``"weird"`` -> ``()``.

    Deliberately NOT ``packaging.version`` — this module must not grow a
    dependency just for a floor check, and PEP 440 edge cases (epochs,
    post-releases) do not occur in hermes-agent's release history.
    """
    parts = []
    for piece in version.split("."):
        m = re.match(r"\d+", piece)
        if not m:
            break
        parts.append(int(m.group(0)))
    return tuple(parts)


def installed_hermes_agent_version() -> Optional[str]:
    """Installed hermes-agent dist version, or ``None`` when undeterminable."""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover — importlib.metadata is 3.8+
        return None
    try:
        return version(HERMES_AGENT_DIST)
    except PackageNotFoundError:
        return None


def check_hermes_agent_version(installed: Optional[str] = None) -> Optional[str]:
    """No-downgrade guard: return an ERROR message iff hermes-agent < floor.

    Returns ``None`` (all clear) when the installed version meets the floor
    OR cannot be determined — an unknown version must never block / scare an
    otherwise-working load (e.g. a vendored hermes-agent without dist
    metadata, or a directory-plugin load in an exotic venv). The caller logs
    the returned message at ERROR; this function never raises.
    """
    if installed is None:
        installed = installed_hermes_agent_version()
    if not installed:
        return None
    parsed = parse_version(installed)
    if not parsed:
        return None
    if parsed < HERMES_AGENT_FLOOR:
        floor_str = ".".join(str(p) for p in HERMES_AGENT_FLOOR)
        return (
            f"installed hermes-agent {installed} is OLDER than the chatlytics "
            f"plugin's floor (>={floor_str}) — this environment has a "
            "DOWNGRADED hermes-agent. A plain `pip install` / `uv pip install` "
            "of an old chatlytics-hermes pin likely resolved hermes-agent "
            "DOWN (the v4.1.1 ==0.14.0 pin did exactly this to production). "
            "Fix: reinstall the correct hermes-agent first, then ALWAYS "
            "install this plugin with `--no-deps` (e.g. "
            "`uv pip install --no-deps /path/to/chatlytics-hermes`) so the "
            "resolver can never touch hermes-agent again."
        )
    return None


def map_connect_error(
    exc: Optional[BaseException] = None,
    status_code: Optional[int] = None,
) -> str:
    """Map a connect / longpoll failure symptom to an operator-actionable fix.

    Pass EITHER ``exc`` (transport-level failure) OR ``status_code``
    (HTTP-level failure). The returned string is appended to log lines and
    doctor FAIL output — keep it one sentence of symptom + one of fix.
    """
    if status_code == 401:
        return (
            "HTTP 401: the bot token is bad / rotated / revoked. Rotate or "
            "recreate it via the chatlytics admin (app.chatlytics.ai → Bots, "
            "or `chatlytics bots create`) and update CHATLYTICS_BOT_TOKEN in "
            "the gateway profile config/.env."
        )
    if status_code == 502:
        return (
            "HTTP 502: Cloudflare tunnel longpoll limit (~4 concurrent "
            "longpolls per tunnel). Switch this gateway's base_url to the LAN "
            f"URL {LAN_BASE_URL} (on-prem gateways must not longpoll through "
            "the tunnel)."
        )
    if status_code is not None:
        return (
            f"HTTP {status_code} from the gateway; check the chatlytics "
            "service logs on the host."
        )
    if exc is not None:
        text = str(exc).lower()
        if isinstance(exc, httpx.TimeoutException) or "timed out" in text or "timeout" in text:
            return (
                "timed out reaching CHATLYTICS_BASE_URL — likely a dead "
                "Tailscale-style IP whose data-plane silently drops traffic "
                "(TCP half-connects, HTTP hangs). Use the LAN URL "
                f"{LAN_BASE_URL} on-prem, or {DNS_BASE_URL}."
            )
        if "refused" in text:
            return (
                "connection refused — wrong host/port in CHATLYTICS_BASE_URL "
                "(or the chatlytics service is down). Expected "
                f"{LAN_BASE_URL} on-prem or {DNS_BASE_URL}."
            )
        if "reset" in text or "closed" in text or "disconnected" in text:
            return (
                "connection reset/closed — the chatlytics service is likely "
                "restarting; the plugin reconnects automatically with backoff."
            )
        return (
            f"transport error ({type(exc).__name__}) — verify "
            f"CHATLYTICS_BASE_URL ({LAN_BASE_URL} on-prem, or {DNS_BASE_URL}) "
            "and that the chatlytics service is up."
        )
    return "unknown failure — run `python -m chatlytics_hermes.doctor`."


def extract_bot_name(payload: Any) -> Optional[str]:
    """Tolerant bot-name extraction from a ``GET /api/v1/bot/me`` payload.

    Accepts both flat (``{"name": ...}``) and nested (``{"bot": {"name":
    ...}}``) shapes so the probe survives gateway response-shape evolution.
    Returns ``None`` when no usable name is present.
    """
    if not isinstance(payload, dict):
        return None
    candidates = [payload]
    bot = payload.get("bot")
    if isinstance(bot, dict):
        candidates.append(bot)
    for obj in candidates:
        for key in ("name", "bot_name", "display_name"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None
