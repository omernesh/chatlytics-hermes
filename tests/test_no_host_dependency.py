"""Guard for issue #4: the package must never declare hermes-agent (the host).

A declared ``hermes-agent`` requirement lets ``pip install`` / ``uv pip
install`` resolve the host DOWN (v4.1.1 did this to production; 2026-10-09 it
broke ``hermes update`` on hpg6). The floor is enforced at runtime instead.
Runtime deps are also lower-bound only: an upper cap can force a downgrade of
a package the host already provides.
"""
from __future__ import annotations

import re
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _name(req: str) -> str:
    return re.split(r"[\s<>=!~;\[@(]", req.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _all_requirements() -> list[str]:
    project = PYPROJECT["project"]
    reqs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        reqs.extend(extra)
    return reqs


def test_hermes_agent_not_in_runtime_dependencies() -> None:
    deps = PYPROJECT["project"]["dependencies"]
    assert not [d for d in deps if _name(d) in {"hermes-agent", "hermes"}], (
        "hermes-agent is the HOST and must not be a runtime dependency (#4)"
    )


def test_hermes_agent_not_in_any_extra_or_build_requires() -> None:
    assert not [r for r in _all_requirements() if _name(r).startswith("hermes")]
    assert not [
        r for r in PYPROJECT["build-system"]["requires"] if _name(r).startswith("hermes")
    ]


def test_runtime_dependencies_are_lower_bound_only() -> None:
    for dep in PYPROJECT["project"]["dependencies"]:
        assert not re.search(r"(<|==|~=|!=)", dep), (
            f"{dep!r}: runtime deps must be lower-bound only (>=) so the "
            "resolver can never downgrade a host package"
        )
        assert "@" not in dep, f"{dep!r}: no direct-URL requirements"
