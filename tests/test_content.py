"""Result masking and external-content treatment across the process boundary.

In-process, the owner's identity is masked in a CAPABILITY result (a copy), a plain tool's
result is not, and `content: external` reaches the opt-in injection guard. Over the bridge:
what survives, and what the result's shape does to the checks.
"""

from __future__ import annotations

import json

import pytest
from iris_harness.sdk import PluginAPI
from iris_harness.testing import harness, plugin

from .support import (
    ASK,
    INJECTION,
    LOOP_SCRIPT,
    OWNER,
    SERVER,
    TOOL,
    Adapter,
    faulty_args,
    make_bridge,
    mcp_rows,
    raw_rows,
    text_of,
)

ENV_KEY = "IRIS_MCP_SERVERS_CONFIG_PATH"


def identity_source():
    def setup(api: PluginAPI) -> None:
        api.register_owner_identity_source(lambda: {"email": [OWNER]})

    return plugin(setup, manifest={"name": "idsrc", "identity": {"provides": ["email"]}})


def direct(op, h):
    return make_bridge(op, h).invoke_external_tool(
        SERVER, TOOL, {"location": "x"}, approval_granted=True
    )


def test_the_owners_email_in_a_bridged_result_is_not_masked(op) -> None:
    op.write_servers(args=faulty_args("owner_email"), env={"FAULT_OWNER": OWNER})
    with harness(plugins=[identity_source()], env={ENV_KEY: str(op.servers_yaml)}) as h:
        out = direct(op, h)
        assert OWNER in text_of(out.result)
        redaction = [r for r in mcp_rows(h) if r["plugin"] == "capability_redaction"]
        assert [r["reason"] for r in redaction] == ["capability_redaction: not a capability"]
        blob = json.dumps([(r["payload"], r["reason"]) for r in raw_rows(h)], default=str)
        assert OWNER not in blob  # the ledger holds digests only


def test_masking_reaches_the_model_neither_in_process_nor_over_mcp(op) -> None:
    """Both arms hand a plain tool's text, owner email intact, to the model. (The in-process
    arm is test_compare.py; the capability path that DOES mask has no MCP counterpart: a
    capability provider is an in-process object, `api.provide`.)"""
    op.write_servers(args=faulty_args("owner_email"), env={"FAULT_OWNER": OWNER})
    adapter = Adapter()
    with harness(
        plugins=[identity_source(), adapter.plugin()],
        fake_model=LOOP_SCRIPT,
        env={ENV_KEY: str(op.servers_yaml)},
    ) as h:
        adapter.bridge = make_bridge(op, h)
        h.chat(ASK)
        assert OWNER in h.model_calls()[-1].user


def test_the_output_classifier_does_not_read_a_bridged_result(op) -> None:
    """The kernel's output classifier reads a result that is text. An MCP `tools/call` result
    is a dict (`{"content": [{"type": "text", ...}]}`), which the bridge passes to PostToolUse
    unchanged, so the classifier reports `tool returned no text` and cannot raise the run's
    data class (GAP-M11). In the in-process arm the same string is classified."""
    op.write_servers(args=faulty_args("owner_email"), env={"FAULT_OWNER": OWNER})
    with harness(plugins=[identity_source()], env={ENV_KEY: str(op.servers_yaml)}) as h:
        direct(op, h)
        reasons = [r["reason"] for r in mcp_rows(h) if r["plugin"] == "output_classifier"]
        assert reasons == ["output_classifier: tool returned no text"]


def test_declared_effect_and_content_are_fixed_by_the_bridge_not_the_server(op) -> None:
    """PostToolUse is stamped `effect=None, content="external"` for every MCP call
    (`_fire_governance_post_tool`): the server cannot declare a tool read-only or internal,
    and the operator cannot either: the registration has no such field."""
    import inspect

    from iris_harness.tools import mcp_bridge

    src = inspect.getsource(mcp_bridge.MCPBridge._fire_governance_post_tool)
    assert 'effect=None, content="external"' in src.replace("\n", " ").replace("  ", "")
    assert "effect" not in set(mcp_bridge.MCPServerConfig.model_fields)


def _guarded(op):
    return harness(
        env={ENV_KEY: str(op.servers_yaml), "IRIS_GOVERNANCE_PROMPT_GUARD": "1"},
        plugins=[],
    )


def test_with_the_guard_on_the_bridged_result_is_not_even_read_by_it(op) -> None:
    """The guard reads a string result, or a dict whose `output|content|text|result|body` is a
    string (`prompt_guard.py::_extract_result_text`). An MCP result's `content` is a LIST of
    typed blocks, so the guard reports `no scannable text` and the injected instruction is
    never submitted to the classifier, with or without weights (GAP-M11). The in-process arm
    at least reaches the classifier (`guard unavailable` offline, test_compare.py)."""
    op.write_servers(args=faulty_args("inject"))
    with _guarded(op) as h:
        out = direct(op, h)
        scanned = [r for r in mcp_rows(h) if r["plugin"] == "prompt_guard_retrieved"]
        assert [r["reason"] for r in scanned] == ["prompt_guard_retrieved: no scannable text"]
        assert INJECTION in text_of(out.result)


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M11: the bridge hands PostToolUse the raw MCP result ({'content': [blocks]}); "
    "the injection guard and the output classifier read strings, so a bridged result is "
    "neither scanned nor classified. The bridge should pass the text of the content blocks",
)
def test_a_bridged_result_reaches_the_injection_classifier(op) -> None:
    op.write_servers(args=faulty_args("inject"))
    with _guarded(op) as h:
        direct(op, h)
        scanned = [r for r in mcp_rows(h) if r["plugin"] == "prompt_guard_retrieved"]
        assert [r["reason"] for r in scanned] != ["prompt_guard_retrieved: no scannable text"]


def test_with_the_default_configuration_the_bridged_result_is_not_scanned(op) -> None:
    op.write_servers(args=faulty_args("inject"))
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        out = direct(op, h)
        assert not [r for r in mcp_rows(h) if r["plugin"] == "prompt_guard_retrieved"]
        assert INJECTION in text_of(out.result)
