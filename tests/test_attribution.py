"""Explainability: what the audit rows of the same turn say about who, what and from where.

G10: model / tier / caller / governance decision / plugin identity, end to end. The two arms
run the same scripted turn; the rows of the forecast call are compared field by field.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from iris_harness.testing import harness

from .support import ASK, LOOP_SCRIPT, MCP_ROUTE, TOOL, Adapter, make_bridge, raw_rows

ENV_KEY = "IRIS_MCP_SERVERS_CONFIG_PATH"
PLUGIN_SRC = Path(__file__).resolve().parents[2] / "iris-weather-plugin" / "src"


def _row_summary(rows):
    pre = next(r for r in rows if r["hook"] == "pre_tool_use")
    return {
        "payload_keys": sorted(pre["payload"]),
        "agent": pre["agent"],
        "run_id": pre["run_id"],
        "caller": pre["payload"].get("caller"),
        "session_id": pre["payload"].get("session_id"),
        "tool_name": pre["payload"]["tool_name"],
    }


def test_mcp_rows_carry_digests_and_a_route_but_no_caller_no_session_no_server_identity(
    op, meteo, tmp_path
) -> None:
    op.write_servers(env=meteo.env)
    adapter = Adapter()
    with harness(
        plugins=[adapter.plugin()], fake_model=LOOP_SCRIPT, env={ENV_KEY: str(op.servers_yaml)}
    ) as h:
        adapter.bridge = make_bridge(op, h)
        h.chat(ASK)
        loop = [r for r in raw_rows(h, tool=TOOL) if r["hook"] in ("pre_tool_use", "post_tool_use")]
        mcp = raw_rows(h, tool=MCP_ROUTE)
        a, b = _row_summary(loop), _row_summary(mcp)
        # the loop's own row: the model is the caller, the turn's run id, the session
        assert a["caller"] == "model:system" and a["session_id"]
        # the bridge's row for the SAME call:
        assert b["caller"] is None  # nobody is named as calling the server
        assert b["agent"] == "mcp" and b["run_id"] == "mcp-weather"  # constant, not the turn's
        assert b["run_id"] != a["run_id"]  # cannot be joined to the turn by run id
        assert b["tool_name"] == MCP_ROUTE  # the only provider attribution: the config's name
        # nothing names the server's version, hash, signer, or interpreter
        blob = json.dumps([r["payload"] for r in mcp]) + " ".join(r["reason"] for r in mcp)
        for needle in ("version", "sha256:", "signed", "key_id", "weather-mcp", "0.1.0"):
            assert needle not in blob.replace("hmac-sha256", "")
        # digests exist and are keyed on both legs
        assert {"args_digest", "digest_alg"} <= set(b["payload_keys"])


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M12: a bridged call's rows are not tied to the turn: run_id defaults to the "
    "constant `mcp-<server>` and agent_type is `mcp`. invoke_external_tool accepts `persona` "
    "and `run_id` but no caller, and a host has no stable accessor for the turn's run id to "
    "pass. The session_id stamp (a contextvar) is the only join, and only when the call is "
    "made on the turn's own thread",
)
def test_a_bridged_calls_rows_share_the_turns_run_id(op, meteo) -> None:
    op.write_servers(env=meteo.env)
    adapter = Adapter()
    with harness(
        plugins=[adapter.plugin()], fake_model=LOOP_SCRIPT, env={ENV_KEY: str(op.servers_yaml)}
    ) as h:
        adapter.bridge = make_bridge(op, h)
        h.chat(ASK)
        loop_run = {r["run_id"] for r in raw_rows(h, tool=TOOL, hook="pre_tool_use")}
        mcp_run = {r["run_id"] for r in raw_rows(h, tool=MCP_ROUTE)}
        assert mcp_run <= loop_run


def test_a_bridged_row_does_carry_the_session_when_called_inside_a_turn(op, meteo) -> None:
    op.write_servers(env=meteo.env)
    adapter = Adapter()
    with harness(
        plugins=[adapter.plugin()], fake_model=LOOP_SCRIPT, env={ENV_KEY: str(op.servers_yaml)}
    ) as h:
        adapter.bridge = make_bridge(op, h)
        h.chat(ASK, session_id="sess-1")
        sessions = {r["payload"].get("session_id") for r in raw_rows(h, tool=MCP_ROUTE)}
        assert sessions == {"sess-1"}


def test_the_stable_audit_row_type_has_no_caller_for_either_arm() -> None:
    from iris_harness.testing import TurnAuditRow

    assert "caller" not in TurnAuditRow.__dataclass_fields__
