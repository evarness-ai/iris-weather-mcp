"""Who may call the MCP-served tool, and what denies a call.

The in-process plugin graduation (P1-3) shows a `plugin:` caller refused by
`CallerPolicyHook` with a row naming it. Here the same question is put to the MCP bridge:
what identity does a bridged call carry, what policy can refuse it, and what records the
refusal.
"""

from __future__ import annotations

import pytest
from iris_harness.testing import harness
from iris_harness.tools.mcp_bridge import MCPApprovalRequired

from .support import (
    ASK,
    LOOP_SCRIPT,
    MCP_ROUTE,
    SERVER,
    TOOL,
    Adapter,
    launch_args,
    make_bridge,
    mcp_rows,
    raw_rows,
)

ENV_KEY = "IRIS_MCP_SERVERS_CONFIG_PATH"
GOV = {
    "allowed_for_personas": [
        {"persona": "developer", "tools": ["weather_*"]},
        {"persona": "tester", "tools": ["weather_code_meaning"]},
    ]
}


def attempt(op, h, *, persona=None, approval=True, server_tool=TOOL):
    try:
        out = make_bridge(op, h).invoke_external_tool(
            SERVER, server_tool, {"location": "x"}, approval_granted=approval, persona=persona
        )
        return out, None
    except Exception as exc:  # noqa: BLE001
        return None, exc


def deny_rows(h):
    return [r for r in raw_rows(h) if r["decision"] == "deny"]


def test_a_bridged_call_carries_no_caller_so_the_caller_policy_hook_sees_the_model(
    op, meteo
) -> None:
    op.write_servers(env=meteo.env)
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        out, error = attempt(op, h)
        assert error is None
        rows = mcp_rows(h)
        policy = next(r for r in rows if r["plugin"] == "caller_policy")
        assert policy["reason"] == "caller_policy: model"  # no caller was supplied: 'model'
        assert all("caller" not in r["payload"] for r in rows)


def test_the_plugin_grant_model_does_not_reach_the_bridge(op, meteo) -> None:
    """`uses: tools:` grants and tool-access.yaml govern `plugin:<name>` callers of tools the
    plugin host mounted. A bridged tool is not mounted, so a probe plugin cannot even name it
    (GAP-M2); there is no `mcp:`-server-as-callee or plugin-as-caller of a bridge route."""
    from iris_harness.sdk import PluginAPI
    from iris_harness.testing import plugin

    holder = {}

    def setup(api: PluginAPI) -> None:
        holder["api"] = api

    probe = plugin(
        setup,
        manifest={"name": "probe", "version": "0", "uses": {"tools": [TOOL, MCP_ROUTE]}},
    )
    op.write_servers(env=meteo.env)
    with harness(plugins=[probe], env={ENV_KEY: str(op.servers_yaml)}) as h:
        make_bridge(op, h)
        for name in (TOOL, MCP_ROUTE):
            result = holder["api"].tools.call(name, {"location": "Lisbon"})
            assert not result.ok
        assert meteo.requests == []


# -- the one authorization the bridge has: (persona, server, tool) --------------------------
@pytest.fixture
def governed(op, meteo):
    op.write_servers(env=meteo.env, governance=GOV)
    return op


def test_no_persona_is_denied_by_the_allowlist(governed) -> None:
    with harness(env={ENV_KEY: str(governed.servers_yaml)}) as h:
        out, error = attempt(governed, h, persona=None)
        assert isinstance(error, PermissionError) and "blocked by governance" in str(error)
        (row,) = deny_rows(h)
        assert row["plugin"] == "mcp_allowlist" and "missing persona context" in row["reason"]
        assert row["hook"] == "pre_tool_use" and row["payload"]["tool_name"] == MCP_ROUTE


def test_a_persona_not_listed_is_denied(governed) -> None:
    with harness(env={ENV_KEY: str(governed.servers_yaml)}) as h:
        _out, error = attempt(governed, h, persona="analyst")
        assert isinstance(error, PermissionError)
        (row,) = deny_rows(h)
        assert "persona 'analyst' not listed" in row["reason"]
        assert row["payload"]["deny_reason"] == "persona_not_allowed"


def test_a_listed_persona_with_the_tool_outside_its_list_is_denied(governed) -> None:
    with harness(env={ENV_KEY: str(governed.servers_yaml)}) as h:
        _out, error = attempt(governed, h, persona="tester")
        assert isinstance(error, PermissionError)
        assert deny_rows(h)[0]["payload"]["deny_reason"] == "tool_not_allowed"


def test_a_granted_persona_runs(governed, meteo) -> None:
    with harness(env={ENV_KEY: str(governed.servers_yaml)}) as h:
        out, error = attempt(governed, h, persona="developer")
        assert error is None and meteo.requests


def test_the_loops_calls_have_no_persona_so_a_governed_server_is_unreachable_from_it(
    governed, meteo
) -> None:
    """The model's ReAct turn runs with persona None. Turn the only per-tool control on and
    the loop's call (through the adapter) is denied for lack of a persona concept."""
    adapter = Adapter(raise_on_failure=False)
    with harness(
        plugins=[adapter.plugin()],
        fake_model=LOOP_SCRIPT,
        env={ENV_KEY: str(governed.servers_yaml)},
    ) as h:
        adapter.bridge = make_bridge(governed, h)
        h.chat(ASK)
        assert meteo.requests == []
        denied = {r["plugin"] for r in deny_rows(h) if r["payload"]["tool_name"] == MCP_ROUTE}
        assert denied == {"mcp_allowlist"}


def test_an_ungoverned_server_is_allowed_with_a_warning_row(op, meteo) -> None:
    other = {"allowed_for_personas": [{"persona": "developer", "tools": ["*"]}]}
    op.write_servers(env=meteo.env)
    # a governance block for ANOTHER server name builds the map, so this one is 'ungoverned'
    doc = op.read_servers()
    doc["servers"].append({"name": "other", "enabled": False, "governance": other})
    import yaml

    op.servers_yaml.write_text(yaml.safe_dump(doc))
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        out, error = attempt(op, h)
        assert error is None
        row = next(r for r in mcp_rows(h) if r["plugin"] == "mcp_allowlist")
        assert row["decision"] == "allow" and row["severity"] == "warn"
        assert "no governance block" in row["reason"]


# -- the operator's global lists see the route as one more tool name --------------------------
def test_the_operators_blocklist_denies_the_route_name(op, meteo) -> None:
    op.write_servers(env=meteo.env)
    with harness(
        env={ENV_KEY: str(op.servers_yaml), "IRIS_GOVERNANCE_BLOCKED_TOOLS": MCP_ROUTE}
    ) as h:
        _out, error = attempt(op, h)
        assert isinstance(error, PermissionError)
        assert deny_rows(h)[0]["plugin"] == "tool_policy" and meteo.requests == []


# -- approval --------------------------------------------------------------------------------
def test_the_governor_requires_approval_for_every_call_and_nothing_can_ask(op, meteo, base) -> None:
    """`coding/mcp` is `requires_approval: true` in the shipped governor policy: a read-only
    forecast needs a boolean from the caller, and the only code that supplies it is the
    caller's own `approval_granted=True`. No approval is queued for the owner (contrast the
    in-process P1-4 queue); the kernel's PRE rows were already written as allow."""
    pidfile = base / "pids"
    op.write_servers(args=launch_args(), env={**meteo.env, "WEATHER_MCP_PIDFILE": str(pidfile)})
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        _out, error = attempt(op, h, approval=False)
        assert isinstance(error, MCPApprovalRequired)
        assert not pidfile.exists() and meteo.requests == []
        rows = mcp_rows(h)
        assert {r["decision"] for r in rows} == {"allow"}  # the kernel allowed it
        assert [r["hook"] for r in rows].count("post_tool_use") == 0
        # and no approval was queued anywhere the owner could answer it
        assert not [
            r for r in raw_rows(h) if "approval" in r["plugin"] and r["decision"] != "allow"
        ]
