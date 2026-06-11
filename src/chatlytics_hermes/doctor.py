"""chatlytics plugin self-check CLI (v4.2.0, P3 survivability).

Run inside the gateway venv (or any env with this package importable)::

    python -m chatlytics_hermes.doctor
    python -m chatlytics_hermes.doctor --base-url http://192.168.1.133:8050 --token sk_bot_...

Prints one ``PASS — ...`` / ``FAIL — ...`` line per check and exits non-zero
if ANY check fails. Designed for the "gateway boots with 2 platforms instead
of 3 and nobody notices" failure class: when the bot goes silent, this is the
first command an operator runs.

Checks:

a. plugin directory present (``$HERMES_HOME/plugins/chatlytics`` with the
   root ``__init__.py`` shim) — the durable directory-plugin install channel
b. config readable + auth token present (``CHATLYTICS_BOT_TOKEN`` preferred,
   legacy ``CHATLYTICS_API_KEY`` fallback)
c. base_url reachable — ``GET /health``
d. token valid — ``GET /api/v1/bot/me`` (prints the bot name)
e. longpoll endpoint reachable — ``GET /api/v1/bot/updates``
f. hermes-agent version >= plugin floor (no-downgrade guard)

Network checks share one sync ``httpx.Client`` and classify failures through
:func:`chatlytics_hermes.diagnostics.map_connect_error` so the doctor's FAIL
lines carry the same actionable fixes the adapter logs at runtime.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

import httpx

from .diagnostics import (
    DNS_BASE_URL,
    check_hermes_agent_version,
    extract_bot_name,
    installed_hermes_agent_version,
    map_connect_error,
)

# (ok, message) — every check returns this shape so doctor() can render a
# uniform PASS/FAIL report and tests can assert on each check in isolation.
CheckResult = Tuple[bool, str]

# Network-check timeout. The longpoll probe asks the server for a 1s hold
# (timeout_ms=1000, server clamps); 30s read comfortably covers it.
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


def resolve_base_url(base_url: Optional[str] = None) -> str:
    """CLI arg > env > DNS default (LAN URL preferred on-prem — see README)."""
    return (
        (base_url or "").strip()
        or (os.getenv("CHATLYTICS_BASE_URL") or "").strip()
        # Default mirrors ChatlyticsAdapter / _standalone_send (v4.1.2
        # token-only onboarding). On-prem gateways should override with the
        # LAN URL to dodge the Cloudflare tunnel longpoll limit.
        or DNS_BASE_URL
    )


def resolve_token(token: Optional[str] = None) -> str:
    """CLI arg > CHATLYTICS_BOT_TOKEN > legacy CHATLYTICS_API_KEY > ''."""
    return (
        (token or "").strip()
        or (os.getenv("CHATLYTICS_BOT_TOKEN") or "").strip()
        or (os.getenv("CHATLYTICS_API_KEY") or "").strip()
    )


def default_plugins_dir() -> Path:
    """``$HERMES_HOME/plugins/chatlytics`` (``~/.hermes`` when HERMES_HOME unset)."""
    home = os.getenv("HERMES_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".hermes"
    return base / "plugins" / "chatlytics"


def check_plugin_dir(plugin_dir: Optional[Path] = None) -> CheckResult:
    """(a) Directory-plugin install present (the update-survivable channel)."""
    target = plugin_dir if plugin_dir is not None else default_plugins_dir()
    shim = target / "__init__.py"
    if target.is_dir() and shim.is_file():
        return True, f"plugin dir present: {target} (directory-plugin shim found)"
    # Fallback: a pip entry-point install also works — it just does NOT
    # survive a hermes-agent update (setup-hermes.sh rm -rf venv).
    try:
        from importlib.metadata import PackageNotFoundError, version

        pip_version = version("chatlytics-hermes")
    except Exception:  # noqa: BLE001 — PackageNotFoundError + metadata quirks
        pip_version = None
    if pip_version:
        return True, (
            f"plugin dir {target} missing, but pip install chatlytics-hermes "
            f"{pip_version} found. WARNING: pip installs do NOT survive "
            "hermes-agent updates — prefer `hermes plugins install "
            "omernesh/chatlytics-hermes` (directory plugin)."
        )
    return False, (
        f"plugin dir {target} missing (no __init__.py shim) and no pip "
        "install found. Fix: `hermes plugins install "
        "omernesh/chatlytics-hermes && hermes plugins enable chatlytics`."
    )


def check_config(token: str) -> CheckResult:
    """(b) Auth token present in config/env."""
    if not token:
        return False, (
            "no auth token: set CHATLYTICS_BOT_TOKEN (sk_bot_..., create via "
            "app.chatlytics.ai → Bots) in the gateway profile config/.env. "
            "Legacy CHATLYTICS_API_KEY also accepted."
        )
    if token.startswith("sk_bot_"):
        return True, "auth token present (bot token, sk_bot_*)"
    return True, (
        "auth token present (legacy operator api_key shape — migrate to a "
        "per-bot CHATLYTICS_BOT_TOKEN; the legacy fallback is removed in "
        "plugin v5.0)"
    )


def check_hermes_agent() -> CheckResult:
    """(f) hermes-agent meets the plugin floor (no-downgrade guard)."""
    msg = check_hermes_agent_version()
    if msg:
        return False, msg
    installed = installed_hermes_agent_version()
    if installed:
        return True, f"hermes-agent {installed} >= plugin floor"
    return True, "hermes-agent version undeterminable (not installed here?) — skipping floor check"


def check_health(client: httpx.Client) -> CheckResult:
    """(c) base_url reachable — GET /health."""
    try:
        resp = client.get("/health")
    except httpx.RequestError as exc:
        return False, f"GET /health failed: {map_connect_error(exc=exc)}"
    if resp.status_code != 200:
        return False, (
            f"GET /health returned HTTP {resp.status_code}: "
            f"{map_connect_error(status_code=resp.status_code)}"
        )
    return True, "base_url reachable (GET /health 200)"


def check_bot_me(client: httpx.Client) -> CheckResult:
    """(d) token valid — GET /api/v1/bot/me, prints bot name."""
    try:
        resp = client.get("/api/v1/bot/me")
    except httpx.RequestError as exc:
        return False, f"GET /api/v1/bot/me failed: {map_connect_error(exc=exc)}"
    if resp.status_code in (401, 403):
        return False, (
            f"GET /api/v1/bot/me returned HTTP {resp.status_code}: "
            f"{map_connect_error(status_code=401)}"
        )
    if resp.status_code != 200:
        return False, (
            f"GET /api/v1/bot/me returned HTTP {resp.status_code}: "
            f"{map_connect_error(status_code=resp.status_code)}"
        )
    try:
        payload: Any = resp.json()
    except Exception:  # noqa: BLE001
        payload = None
    name = extract_bot_name(payload)
    if name:
        return True, f"token valid — authenticated as {name}"
    return True, "token valid (200 from /api/v1/bot/me; no bot name in payload)"


def check_longpoll(client: httpx.Client) -> CheckResult:
    """(e) longpoll endpoint reachable — GET /api/v1/bot/updates (1s hold)."""
    try:
        resp = client.get(
            "/api/v1/bot/updates",
            params={"cursor": "", "timeout_ms": 1000},
        )
    except httpx.RequestError as exc:
        return False, f"GET /api/v1/bot/updates failed: {map_connect_error(exc=exc)}"
    if resp.status_code == 200:
        return True, "longpoll endpoint reachable (GET /api/v1/bot/updates 200)"
    if resp.status_code == 404:
        return False, (
            "GET /api/v1/bot/updates returned 404 — the chatlytics server "
            "predates the v4.1 bot-updates contract; update the server or "
            "use webhook inbound mode."
        )
    return False, (
        f"GET /api/v1/bot/updates returned HTTP {resp.status_code}: "
        f"{map_connect_error(status_code=resp.status_code)}"
    )


def doctor(
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    plugin_dir: Optional[Path] = None,
    *,
    print_fn: Callable[[str], Any] = print,
) -> int:
    """Run all checks; print PASS/FAIL per item; return 0 iff all passed."""
    resolved_url = resolve_base_url(base_url)
    resolved_token = resolve_token(token)

    print_fn(f"chatlytics doctor — base_url={resolved_url}")
    results: List[Tuple[str, CheckResult]] = [
        ("plugin-dir", check_plugin_dir(plugin_dir)),
        ("config", check_config(resolved_token)),
        ("hermes-agent", check_hermes_agent()),
    ]

    if resolved_token:
        headers = {
            "Authorization": f"Bearer {resolved_token}",
            "Accept": "application/json",
            "User-Agent": "chatlytics-hermes-doctor",
        }
    else:
        headers = {"Accept": "application/json", "User-Agent": "chatlytics-hermes-doctor"}
    with httpx.Client(
        base_url=resolved_url.rstrip("/"), timeout=_HTTP_TIMEOUT, headers=headers
    ) as client:
        results.append(("health", check_health(client)))
        if resolved_token:
            results.append(("bot-me", check_bot_me(client)))
            results.append(("longpoll", check_longpoll(client)))
        else:
            results.append(
                ("bot-me", (False, "skipped — no auth token (see config check)"))
            )
            results.append(
                ("longpoll", (False, "skipped — no auth token (see config check)"))
            )

    failures = 0
    for label, (ok, message) in results:
        status = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print_fn(f"{status} — {label}: {message}")

    if failures:
        print_fn(f"doctor: {failures} check(s) FAILED")
        return 1
    print_fn("doctor: all checks passed")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m chatlytics_hermes.doctor",
        description="Self-check the chatlytics Hermes plugin environment.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"Gateway base URL (default: $CHATLYTICS_BASE_URL or {DNS_BASE_URL})",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Bot token (default: $CHATLYTICS_BOT_TOKEN / $CHATLYTICS_API_KEY)",
    )
    parser.add_argument(
        "--plugins-dir",
        default=None,
        help="Plugin install dir to check (default: $HERMES_HOME/plugins/chatlytics)",
    )
    args = parser.parse_args(argv)
    plugin_dir = Path(args.plugins_dir).expanduser() if args.plugins_dir else None
    return doctor(base_url=args.base_url, token=args.token, plugin_dir=plugin_dir)


if __name__ == "__main__":  # pragma: no cover — exercised via main() tests
    sys.exit(main())
