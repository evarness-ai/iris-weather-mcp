"""Removal closure (G8) for an MCP registration, and the boundary of what this suite needs.

In-process, uninstalling the plugin leaves a stale profile entry that boots as `failed` and
names the missing plugin (graduation P0-7). Over MCP the harness never held a record of the
server, so there is nothing to unregister and nothing to report: closure is the operator's
file edit plus the server venv's own removal.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
import yaml
from iris_harness.testing import check_stable_imports, harness

from .support import (
    REPO,
    SERVER,
    TOOL,
    launch_args,
    make_bridge,
    pid_alive,
    raw_rows,
)

ENV_KEY = "IRIS_MCP_SERVERS_CONFIG_PATH"


def _call(op, h):
    return make_bridge(op, h).invoke_external_tool(
        SERVER, TOOL, {"location": "x"}, approval_granted=True
    )


def test_removing_the_registration_closes_the_door_and_spawns_nothing(op, meteo, base) -> None:
    pidfile = base / "pids"
    op.write_servers(args=launch_args(), env={**meteo.env, "WEATHER_MCP_PIDFILE": str(pidfile)})
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        _call(op, h)
        assert len(pidfile.read_text().split()) == 1
        # disable
        op.edit_server(enabled=False)
        with pytest.raises(ValueError, match="is disabled"):
            _call(op, h)
        # remove the entry altogether
        doc = op.read_servers()
        doc["servers"] = []
        op.servers_yaml.write_text(yaml.safe_dump(doc))
        with pytest.raises(ValueError, match="unknown MCP server: weather"):
            _call(op, h)
        assert len(pidfile.read_text().split()) == 1  # nothing started after the first call
        assert not any(pid_alive(int(p)) for p in pidfile.read_text().split())


def test_the_harness_holds_no_record_of_the_server_before_or_after(op, meteo) -> None:
    op.write_servers(env=meteo.env)
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        before = dict(h.plugins())
        _call(op, h)
        assert dict(h.plugins()) == before  # no plugin record appears or changes
        assert SERVER not in {r.name for r in h._runtime.plugin_registry.plugins()}
        assert "weather_forecast" not in {
            r["payload"].get("tool_name") for r in raw_rows(h)
        }  # the bare tool name never appears; only the route


def test_a_stale_registration_after_the_server_package_is_gone_fails_at_call_time(op, base) -> None:
    """Uninstall the server's package from a copy of its venv and leave the registration:
    nothing reports it at boot or in health; the first call fails with a transport error."""
    venv = base / "venv2"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)  # noqa: S603
    py = venv / "bin" / "python"
    subprocess.run(  # noqa: S603
        [str(py), "-m", "pip", "install", "-q", "--no-deps", str(REPO)],
        check=True,
        capture_output=True,
    )
    listing = subprocess.run(  # noqa: S603
        [str(py), "-m", "pip", "show", "-f", "iris-weather-mcp"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "iris_weather_mcp/server.py" in listing
    site = next((venv / "lib").glob("python*/site-packages"))
    before = {p.relative_to(site) for p in site.rglob("*") if p.is_file()}
    subprocess.run(  # noqa: S603
        [str(py), "-m", "pip", "uninstall", "-y", "-q", "iris-weather-mcp"], check=True
    )
    after = {p.relative_to(site) for p in site.rglob("*") if p.is_file()}
    gone = {str(p) for p in before - after}
    assert gone and all(
        p.startswith(("iris_weather_mcp", "iris_weather_mcp-")) for p in gone
    ), gone  # exactly the package's own files went; nothing else was touched
    assert after - before == set()

    op.write_servers(command=py, args=("-m", "iris_weather_mcp.server"), timeout=3.0)
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        assert SERVER not in h.plugins()  # boot saw nothing wrong
        with pytest.raises(ValueError, match="unexpected EOF while reading MCP stdio message"):
            _call(op, h)  # and the cause ('No module named') is lost (GAP-M8)


def test_the_stable_tier_does_not_cover_what_this_suite_needs_from_the_harness() -> None:
    """GAP-M1 / GAP-M2 as data: the imports an operator-side host needs are all outside the
    stable tier. `check_stable_imports` over this repository's tests finds exactly these."""
    violations = check_stable_imports([REPO / "tests"])
    modules = {v.module for v in violations}
    assert modules == {
        "iris_harness.server.iris_api.mcp_server",
        "iris_harness.tools",
        "iris_harness.tools.mcp_bridge",
        "iris_harness.kernel.governor",
        "iris_harness.kernel.governance.audit.log",
        "iris_harness.kernel.governance.mcp_signing",
        "iris_harness.foundation.paths",
    }, modules
    # while the server process itself (src) has none, and imports nothing of the harness at all
    assert check_stable_imports([REPO / "src"]) == []
