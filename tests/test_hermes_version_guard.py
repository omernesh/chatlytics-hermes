"""check_hermes_agent_version: undeterminable 0.0.0* versions are not a downgrade."""
from __future__ import annotations

import pytest

from chatlytics_hermes.diagnostics import check_hermes_agent_version


@pytest.mark.parametrize("v", ["0.0.0", "0.0.0+unknown", "0.0.0+g1234abc", "", "garbage"])
def test_undeterminable_versions_are_clear(v: str) -> None:
    assert check_hermes_agent_version(v) is None


def test_below_floor_is_flagged() -> None:
    msg = check_hermes_agent_version("0.13.9")
    assert msg and "OLDER" in msg


@pytest.mark.parametrize("v", ["0.14.0", "0.15.1", "1.2.3"])
def test_at_or_above_floor_is_clear(v: str) -> None:
    assert check_hermes_agent_version(v) is None
