"""Egress: what is governed now that the HTTP happens in another process.

The in-process weather plugin's GAP-1: nothing mediates a plugin's own HTTP; the operator's
only record is what the plugin chose to log. Here the plugin is a child process. The
question is what the harness can still see, confine or refuse.
"""

from __future__ import annotations

import json
import logging
import socket

import pytest
from iris_harness.testing import harness

from .support import (
    SERVER,
    TOOL,
    faulty_args,
    make_bridge,
    mcp_rows,
    raw_rows,
    text_of,
    wait_until,
)

ENV_KEY = "IRIS_MCP_SERVERS_CONFIG_PATH"


def _call(op, h, arguments=None):
    return make_bridge(op, h).invoke_external_tool(
        SERVER, TOOL, arguments or {"location": "Lisbon"}, approval_granted=True
    )


def test_the_harnesses_offline_guard_stops_the_parent_but_not_the_child(op, meteo) -> None:
    """`no_network()` (what makes the in-process suite offline, G9) patches this interpreter's
    sockets. The server is another interpreter: it reaches the loopback stub while the
    parent, in the same harness, is refused."""
    op.write_servers(env=meteo.env)
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        with pytest.raises(Exception, match="network access blocked") as refused:
            socket.create_connection(("127.0.0.1", meteo.port), timeout=2).close()
        assert refused.type.__name__ == "NetworkBlockedError"
        out = _call(op, h)
        assert text_of(out.result).startswith("Forecast for Lisbon")
        assert len(meteo.requests) == 2  # the child's two requests got through


def test_the_ledger_never_names_a_host_the_server_contacted(op, meteo) -> None:
    op.write_servers(env=meteo.env)
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        _call(op, h)
        blob = json.dumps([(r["payload"], r["reason"]) for r in raw_rows(h)], default=str)
        assert "127.0.0.1" not in blob and str(meteo.port) not in blob
        assert "geocode" not in blob and "open-meteo" not in blob
        net = [r for r in mcp_rows(h) if r["plugin"] == "network_egress"]
        assert [r["reason"] for r in net] == ["network_egress: not a network tool"]


def test_the_servers_own_log_is_the_only_record_and_it_is_outside_the_harness(
    op, meteo, base, caplog
) -> None:
    log = base / "egress.log"
    op.write_servers(env={**meteo.env, "WEATHER_MCP_EGRESS_LOG": str(log)})
    with caplog.at_level(logging.INFO, logger="iris.egress"):
        with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
            _call(op, h)
    assert [json.loads(line)["purpose"] for line in log.read_text().splitlines()] == [
        "geocode",
        "forecast",
    ]
    # the harness's own egress logger (where the in-process plugin's log_egress goes) is silent
    assert [r for r in caplog.records if r.name == "iris.egress"] == []


def test_the_bridge_logs_egress_only_for_http_and_sse_transports() -> None:
    import inspect

    from iris_harness.tools import mcp_bridge

    assert "log_egress" not in inspect.getsource(mcp_bridge._run_stdio_exchange)
    assert "log_egress" in inspect.getsource(mcp_bridge._default_http_requester)


def test_nothing_in_the_registration_can_declare_the_servers_hosts() -> None:
    import dataclasses

    from iris_harness.kernel.governance.mcp_signing import ServerSpec
    from iris_harness.tools.mcp_bridge import MCPServerConfig

    names = {f.name for f in dataclasses.fields(ServerSpec)} | set(MCPServerConfig.model_fields)
    assert not [
        n for n in names if "egress" in n or "host" in n or "allow" in n.replace("allowed", "")
    ]


# -- an undeclared host -----------------------------------------------------------------------
def test_a_server_that_contacts_an_undeclared_host_is_neither_stopped_nor_recorded(
    op, sink
) -> None:
    op.write_servers(args=faulty_args("exfil"), env={"FAULT_SINK_PORT": str(sink.port)})
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        _call(op, h)
        assert wait_until(lambda: sink.received)
        assert sink.received[0].startswith(b"owner data leaves here")
        assert {r["decision"] for r in mcp_rows(h)} == {"allow"}
        assert str(sink.port) not in json.dumps([r["payload"] for r in raw_rows(h)], default=str)


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M5: the bridge launches stdio servers with a plain Popen (no sandbox, proxy, "
    "network namespace or per-server host list), so a signed server's undeclared outbound "
    "connection is neither refused nor recorded by the harness",
)
def test_an_undeclared_host_connection_is_refused_or_recorded(op, sink) -> None:
    op.write_servers(args=faulty_args("exfil"), env={"FAULT_SINK_PORT": str(sink.port)})
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        try:
            _call(op, h)
        except Exception:  # noqa: BLE001, S110
            pass
        blob = json.dumps([(r["payload"], r["reason"]) for r in raw_rows(h)], default=str)
        assert not sink.received or str(sink.port) in blob


# -- what the server inherits -----------------------------------------------------------------
def _envdump(op, h, monkeypatch=None):
    out = _call(op, h)
    return json.loads(text_of(out.result))


def test_the_server_inherits_the_whole_host_environment_including_the_vault_key(
    op, monkeypatch
) -> None:
    """`_run_stdio_exchange` starts the child with `os.environ.copy()` plus the server's env.
    The harness's throwaway vault master key (the real one is the same variable) and any
    other variable of the host are in the plugin's process. Only the key's presence and a
    hash prefix are recorded here, never the value."""
    monkeypatch.setenv("WEATHER_CANARY", "canary-value")
    op.write_servers(args=faulty_args("envdump"))
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        seen = _envdump(op, h)
        assert seen["canary"] == "canary-value"
        assert seen["vault_key_present"] is True
        assert "IRIS_VAULT_MASTER_KEY" in seen["names"] and "IRIS_HOME" in seen["names"]


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M5b: the child gets the host's full environment (os.environ.copy()), so a "
    "third-party server holds IRIS_VAULT_MASTER_KEY and every IRIS_* setting; there is no "
    "env allow-list for a stdio server",
)
def test_the_server_does_not_receive_the_vault_master_key(op) -> None:
    op.write_servers(args=faulty_args("envdump"))
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        assert _envdump(op, h)["vault_key_present"] is False
