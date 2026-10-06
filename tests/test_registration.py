"""How an MCP server is registered with the harness, and what the harness does with it.

Everything here is observed, not assumed: ``trust: mcp`` in a plugin manifest, the bridge's
disabled default, discovery, the governed call, spawn-per-call, and the loop's reach.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

import pytest
import yaml
from iris_harness.foundation.paths import packaged_config_dir
from iris_harness.testing import harness, plugin

from .support import (
    ASK,
    LOOP_SCRIPT,
    SERVER,
    TOOL,
    Adapter,
    launch_args,
    make_bridge,
    mcp_rows,
    pid_alive,
    raw_rows,
    text_of,
)

ENV_KEY = "IRIS_MCP_SERVERS_CONFIG_PATH"


def _harness(op, adapter=None, **kw):
    env = {ENV_KEY: str(op.servers_yaml), **kw.pop("env", {})}
    plugins = [adapter.plugin()] if adapter else []
    return harness(plugins=plugins, fake_model=kw.pop("fake_model", None), env=env, **kw)


# -- registration surface --------------------------------------------------------------
def test_a_trust_mcp_plugin_is_not_loaded_by_the_plugin_host(base: Path) -> None:
    """The documented route (`trust: mcp` in a plugin manifest) is a stub: the host records
    the plugin as `unsupported` and never starts anything (loader.py: 'not available yet')."""
    home = base / "iris-home"
    (home / "plugins" / "weather-mcp").mkdir(parents=True)
    manifest = {
        "name": "weather-mcp",
        "version": "0.1.0",
        "party": "trusted-third-party",
        "trust": "mcp",
        "provides": ["tool"],
        "tools": {TOOL: {"effect": "read", "content": "external"}},
    }
    (home / "plugins" / "weather-mcp" / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    (home / "profile.yaml").write_text(yaml.safe_dump({"plugins": [{"name": "weather-mcp"}]}))
    with harness(env={"IRIS_HOME": str(home)}) as h:
        status, error = h.plugins()["weather-mcp"]
        assert status == "unsupported"
        assert error == "trust: mcp (out-of-process host) is not available yet; not loaded"
        pool = {t.name for t in h._runtime.tool_service.describe()}
        assert "system_health" in pool and TOOL not in pool  # the pool is readable, and lacks it


def test_the_testing_harness_cannot_mount_a_trust_mcp_plugin() -> None:
    """`iris_harness.testing.plugin()` always wants an in-process `setup`: a plugin author's
    own test has no way to say 'this one is out of process' (GAP-M3)."""
    with pytest.raises(TypeError, match="needs a setup callable"):
        plugin(None, manifest={"name": "weather-mcp", "trust": "mcp", "version": "0"})


def test_the_bridge_is_off_and_unconfigured_in_a_fresh_install(op) -> None:
    from iris_harness.tools.mcp_bridge import MCPBridge, load_mcp_bridge_config

    assert not (packaged_config_dir() / "coding-agent").exists()  # no shipped mcp-servers.yaml
    config = load_mcp_bridge_config(op.root)  # an operator root with no file
    assert config.enabled is False and config.servers == ()
    bridge = MCPBridge(op.root, config=config)
    with pytest.raises(ValueError, match="MCP bridge is disabled"):
        bridge.invoke_external_tool(SERVER, TOOL, {})


def test_the_default_config_path_is_the_coding_agents(op) -> None:
    """Registration is a file under config/coding-agent/ (the coding agent is not in release
    1), not the plugin manifest or the profile."""
    from iris_harness.tools import mcp_bridge

    src = Path(mcp_bridge.__file__).read_text()
    assert '"config" / "coding-agent" / "mcp-servers.yaml"' in src


# -- discovery and the governed call ---------------------------------------------------
def test_tools_list_discovers_the_server_and_writes_no_tool_use_row(op, meteo) -> None:
    op.write_servers(env=meteo.env)
    with _harness(op) as h:
        bridge = make_bridge(op, h)
        tools = bridge.list_external_tools(SERVER, approval_granted=True)
        assert {t.name for t in tools} == {"weather_code_meaning", TOOL}
        assert {t.governor_route for t in tools} == {"coding/mcp"}  # not a per-tool route
        # no effect / content / classification / caller-grant fields exist on a discovered tool
        assert set(type(tools[0]).model_fields) == {
            "name", "description", "input_schema", "governor_route", "source_kind", "source_name",
        }  # fmt: skip
        assert [r for r in raw_rows(h) if r["hook"] in ("pre_tool_use", "post_tool_use")] == []


def test_a_bridge_call_is_governed_pre_and_post_with_keyed_digests(op, meteo) -> None:
    op.write_servers(env=meteo.env)
    with _harness(op) as h:
        out = make_bridge(op, h).invoke_external_tool(
            SERVER, TOOL, {"location": "Lisbon", "days": 2}, approval_granted=True
        )
        assert text_of(out.result).startswith("Forecast for Lisbon, Lisboa, Portugal:")
        assert out.result["isError"] is False
        rows = mcp_rows(h)
        pre = [r for r in rows if r["hook"] == "pre_tool_use"]
        post = [r for r in rows if r["hook"] == "post_tool_use"]
        assert len(pre) == 9 and len(post) == 3
        assert {r["decision"] for r in rows} == {"allow"}
        assert pre[0]["payload"]["args_digest"] and post[0]["payload"]["result_digest"]
        assert pre[0]["payload"]["digest_alg"].startswith("hmac-sha256/v1/")
        text = json.dumps([r["payload"] for r in rows])
        assert "Lisbon" not in text  # digests only, never the text


def test_no_server_without_an_adapter_is_reachable_from_the_loop(op, meteo, base) -> None:
    """The governed loop's tool pool is the plugin host's. Configure the server, script the
    model to call its tool: nothing reaches the server (GAP-M2)."""
    pidfile = base / "pids"
    op.write_servers(env={**meteo.env, "WEATHER_MCP_PIDFILE": str(pidfile)})
    with _harness(op, fake_model=LOOP_SCRIPT) as h:
        make_bridge(op, h)  # configured and enabled
        result = h.chat(ASK)
        assert result.answered
        assert "Sunny, then rain." not in result.text
        assert meteo.requests == [] and not pidfile.exists()
        tool_use = [
            r for r in raw_rows(h)
            if r["hook"] in ("pre_tool_use", "post_tool_use") and r["payload"].get("tool_name") == TOOL
        ]  # fmt: skip
        assert tool_use == []  # the model asked; the loop had no such tool to govern or run


@pytest.mark.parametrize("entry", ["chat", "chat_stream"])
def test_in_the_loop_through_the_measurement_adapter(op, meteo, entry) -> None:
    op.write_servers(env=meteo.env)
    adapter = Adapter()
    with _harness(op, adapter, fake_model=LOOP_SCRIPT) as h:
        adapter.bridge = make_bridge(op, h)
        result = getattr(h, entry)(ASK)
        assert result.answered and "Sunny, then rain." in result.text
        assert h.audit_gaps() == []
        loop_pre = raw_rows(h, tool=TOOL, hook="pre_tool_use")
        assert {r["payload"]["caller"] for r in loop_pre} == {"model:system"}
        assert len(mcp_rows(h)) == 12  # 9 PRE + 3 POST for the MCP leg, on top of the loop's
        assert len(meteo.requests) == 2  # geocode + forecast, from the SERVER process


def test_each_call_spawns_and_reaps_a_process_and_what_it_costs(op, meteo, base) -> None:
    pidfile = base / "pids"
    op.write_servers(args=launch_args(), env={**meteo.env, "WEATHER_MCP_PIDFILE": str(pidfile)})
    with _harness(op) as h:
        bridge = make_bridge(op, h)
        durations = []
        for _ in range(4):
            t0 = time.perf_counter()
            bridge.invoke_external_tool(SERVER, TOOL, {"location": "Lisbon"}, approval_granted=True)
            durations.append(time.perf_counter() - t0)
        pids = [int(p) for p in pidfile.read_text().split()]
        assert len(pids) == len(set(pids)) == 4  # a new process per call
        assert not any(pid_alive(p) for p in pids)  # and none left behind
        out = Path(__file__).parent / "_out"
        out.mkdir(exist_ok=True)
        (out / "mcp_latency.json").write_text(
            json.dumps({"calls_s": durations, "median_s": statistics.median(durations)})
        )
        assert os.environ.get("IRIS_HOME")  # inside the throwaway harness


def test_the_bridge_does_not_check_the_tool_against_what_the_server_declared(op, meteo) -> None:
    """In-process, a tool the manifest does not declare is refused (graduation P0-5). Here the
    bridge forwards any tool name the caller supplies: the kernel runs on it as on a declared
    one, and only the server's own answer (the real SDK server reports an unknown tool as a
    successful call whose result says `isError: true`) says it does not exist."""
    op.write_servers(env=meteo.env)
    with _harness(op) as h:
        out = make_bridge(op, h).invoke_external_tool(
            SERVER, "delete_everything", {}, approval_granted=True
        )
        assert out.result["isError"] is True
        assert "delete_everything" in text_of(out.result)
        rows = raw_rows(h, tool="mcp/weather/delete_everything")
        assert [r["hook"] for r in rows].count("pre_tool_use") == 9
        assert [r["hook"] for r in rows].count("post_tool_use") == 3
        assert {r["decision"] for r in rows} == {"allow"}
