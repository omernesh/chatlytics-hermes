"""Shared test helpers for chatlytics-hermes tests.

This module consolidates copy-pasted test shims that appeared across
8 test files in the v2.0 milestone. Phase 11 (HERMES-11) carved this
out as part of test-infra cleanup (PR-review INFO-02).
"""
from __future__ import annotations

from typing import Any, Dict


class FakePlatformConfig:
    """Minimal PlatformConfig stand-in for tests.

    The adapter only reads ``getattr(config, "extra", {})`` plus the
    convenience attributes set below. A namespace-like object is
    sufficient and keeps tests insulated from upstream PlatformConfig
    schema churn.
    """

    def __init__(self, extra: Dict[str, Any]) -> None:
        self.extra = extra
        self.enabled = True
        self.name = "chatlytics"
        self.token = None
        self.api_key = extra.get("api_key")
        self.home_channel = extra.get("home_channel")
