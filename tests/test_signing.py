"""Signed registration: what refuses an unsigned or tampered server, and what does not.

Operator tooling is the shipped CLI (`iris mcp keygen|sign|verify`) run in a subprocess;
enforcement is `MCPBridge._enforce_signature`, reached through a real call. A refusal must
happen BEFORE the server process starts: the launcher records its pid, so "no pidfile" is
"never spawned".
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import pytest
import yaml
from iris_harness.kernel.governance.mcp_signing import MCPSigningConfig  # noqa: F401
from iris_harness.testing import harness
from iris_harness.tools.mcp_bridge import MCPSignatureError

from .support import (
    SERVER,
    TOOL,
    Operator,
    launch_args,
    make_bridge,
    mcp_rows,
    raw_rows,
    text_of,
)

ENV_KEY = "IRIS_MCP_SERVERS_CONFIG_PATH"


@pytest.fixture(scope="module")
def signed_template(tmp_path_factory) -> Path:
    """An operator root with a key, a trust store and the weather server signed once."""
    from .support import new_operator

    base = tmp_path_factory.mktemp("signed")
    op = new_operator(base)
    op.write_servers(
        args=launch_args(),
        env={
            "WEATHER_MCP_PIDFILE": "x",
            "WEATHER_MCP_GEOCODING_URL": "x",
            "WEATHER_MCP_FORECAST_URL": "x",
        },
    )
    op.keygen("ops-test")
    op.sign("ops-test")
    return base


@pytest.fixture
def sop(signed_template: Path, base: Path) -> Operator:
    """A function-scoped copy of the signed operator root, with its own pidfile."""
    root = base / "copy"
    shutil.copytree(signed_template, root)
    op = Operator(root=root / "operator", keys=root / "keys")
    doc = op.read_servers()
    doc["servers"][0]["env"]["WEATHER_MCP_PIDFILE"] = str(base / "pids")
    # the signature covers env KEY NAMES, so pointing the value at this test's file is fine
    op.servers_yaml.write_text(yaml.safe_dump(doc))
    op.pidfile = base / "pids"  # type: ignore[attr-defined]
    return op


def call(op: Operator, **kwargs):
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        try:
            out = make_bridge(op, h).invoke_external_tool(
                SERVER, TOOL, {"location": "Lisbon"}, approval_granted=True, **kwargs
            )
            return out, None, h, mcp_rows(h)
        except BaseException as exc:  # noqa: BLE001
            return None, exc, h, mcp_rows(h)


def spawned(op) -> bool:
    return op.pidfile.exists()


# -- operator tooling ------------------------------------------------------------------------
def test_the_cli_signs_and_verifies_and_the_signature_is_in_the_config(sop) -> None:
    done = sop.verify()
    assert done.returncode == 0, done.stdout + done.stderr
    entry = sop.read_servers()["servers"][0]
    assert entry["signed_by"] == "ops-test" and entry["signature"]


def test_the_cli_verify_exits_nonzero_on_an_unsigned_server(sop) -> None:
    doc = sop.read_servers()
    doc["servers"][0].pop("signature")
    sop.servers_yaml.write_text(yaml.safe_dump(doc))
    assert sop.verify().returncode != 0


def test_the_mcp_cli_has_no_remove_or_revoke_command(sop) -> None:
    helptext = sop.iris("mcp", "--help").stdout
    for name in ("serve", "keygen", "sign", "verify"):
        assert name in helptext
    assert "revoke" not in helptext and "remove" not in helptext and "list" not in helptext


# -- the policy ladder -----------------------------------------------------------------------
def test_with_no_signing_file_an_unsigned_server_runs_silently(op, meteo, base, caplog) -> None:
    """The shipped default (config/governance/mcp-signing.yaml says enabled: true, shadow) is
    NOT what a bridge reads when the operator root has no such file: absent means disabled."""
    op.write_servers(args=launch_args(), env={**meteo.env, "WEATHER_MCP_PIDFILE": str(base / "p")})
    op.pidfile = base / "p"  # type: ignore[attr-defined]
    with caplog.at_level(logging.WARNING, logger="iris_harness.tools.mcp_bridge"):
        out, error, _h, _rows = call(op)
    assert error is None and text_of(out.result).startswith("Forecast")
    assert not [r for r in caplog.records if "mcp-signing" in r.getMessage()]


def test_shadow_mode_warns_and_runs_a_tampered_server(sop, meteo) -> None:
    sop.write_signing("shadow", "deny")
    sop.edit_server(args=["-c", "print(1)"])  # nothing like the signed spec
    out, error, _h, _rows = call(sop)
    # it ran (shadow never blocks); the process had no MCP behaviour so the call failed on
    # the transport, not on the signature
    assert not isinstance(error, MCPSignatureError)


def test_enforce_refuses_an_unsigned_server_before_spawning_it(sop) -> None:
    sop.write_signing("enforce", "deny")
    doc = sop.read_servers()
    doc["servers"][0].pop("signature")
    doc["servers"][0].pop("signed_by")
    sop.servers_yaml.write_text(yaml.safe_dump(doc))
    _out, error, h, rows = call(sop)
    assert isinstance(error, MCPSignatureError) and "unsigned" in str(error)
    assert not spawned(sop)
    # the failure is a RuntimeError: not the PermissionError every other refusal is, so the
    # HTTP route maps it to 400 instead of 403 (server/iris_api/mcp_server.py)
    assert not isinstance(error, PermissionError)
    # ledger: nine PRE allows (the kernel ran before the transport) and no POST, no deny row
    assert [r["hook"] for r in rows].count("pre_tool_use") == 9
    assert {r["decision"] for r in rows} == {"allow"}


def test_enforce_runs_the_signed_server(sop, meteo) -> None:
    sop.write_signing("enforce", "deny")
    doc = sop.read_servers()
    doc["servers"][0]["env"].update(meteo.env)  # env values are not signed (see below)
    sop.servers_yaml.write_text(yaml.safe_dump(doc))
    out, error, _h, _rows = call(sop)
    assert error is None and text_of(out.result).startswith("Forecast")
    assert spawned(sop)


@pytest.mark.parametrize(
    ("field", "value"),
    [("args", ["-c", "print('swapped')"]), ("command", "/bin/echo"), ("name", "weather2")],
)
def test_enforce_refuses_a_signed_server_whose_spec_changed(sop, field, value) -> None:
    sop.write_signing("enforce", "deny")
    sop.edit_server(**{field: value})
    _out, error, _h, _rows = call(sop) if field != "name" else _call_named(sop)
    assert isinstance(error, MCPSignatureError) and "invalid" in str(error)
    assert not spawned(sop)


def _call_named(op):
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        try:
            make_bridge(op, h).invoke_external_tool(
                "weather2", TOOL, {"location": "x"}, approval_granted=True
            )
            return None, None, h, []
        except BaseException as exc:  # noqa: BLE001
            return None, exc, h, []


def test_enforce_refuses_a_forged_signature_without_crashing(sop) -> None:
    sop.write_signing("enforce", "deny")
    sop.edit_server(signature="AAAA")
    _out, error, _h, _rows = call(sop)
    assert isinstance(error, MCPSignatureError) and "invalid" in str(error)
    assert not spawned(sop)


def test_enforce_refuses_an_unknown_or_revoked_signer(sop) -> None:
    sop.write_signing("enforce", "deny")
    sop.trust_yaml.write_text(yaml.safe_dump({"version": 1, "keys": []}))
    _out, error, _h, _rows = call(sop)
    assert isinstance(error, MCPSignatureError) and "untrusted" in str(error)
    assert not spawned(sop)


def test_a_revoked_key_is_refused(sop) -> None:
    sop.write_signing("enforce", "deny")
    trust = yaml.safe_load(sop.trust_yaml.read_text())
    trust["revoked_key_ids"] = ["ops-test"]
    sop.trust_yaml.write_text(yaml.safe_dump(trust))
    _out, error, _h, _rows = call(sop)
    assert isinstance(error, MCPSignatureError) and "revoked" in str(error)


# -- what the signature does not cover ----------------------------------------------------
def test_the_signed_spec_names_the_interpreter_not_the_code_it_runs(sop, base) -> None:
    """`package_sha256` hashes `command` when it is a file. For `python -m server` that is the
    interpreter. The server's own source is not covered."""
    from iris_harness.tools.mcp_bridge import build_server_spec, load_mcp_bridge_config

    spec = build_server_spec(load_mcp_bridge_config(sop.root).servers[0])
    assert spec.command and spec.package_sha256 and spec.package_sha256.startswith("sha256:")
    assert spec.args[0].endswith("launch.py")  # the script the interpreter runs: only its PATH
    assert not hasattr(spec, "args_sha256") and not hasattr(spec, "source_sha256")


def _tamper_source(sop, base) -> Path:
    """Prepend a marker write to the server's launcher, in place, after signing."""
    script = Path(sop.read_servers()["servers"][0]["args"][0])
    marker = base / "tampered"
    original = script.read_text()
    script.write_text(f"open({str(marker)!r}, 'w').write('ran')\n" + original)
    sop.restore = lambda: script.write_text(original)  # type: ignore[attr-defined]
    return marker


def test_edited_server_source_still_verifies_and_runs_under_enforce(sop, base, meteo) -> None:
    """Evidence for GAP-M4a: the code the signed command line runs is not in the signature."""
    sop.write_signing("enforce", "deny")
    marker = _tamper_source(sop, base)
    try:
        assert sop.verify().returncode == 0  # the shipped verifier calls it good
        _out, error, _h, _rows = call(sop)
    finally:
        sop.restore()  # type: ignore[attr-defined]
    assert not isinstance(error, MCPSignatureError)
    assert marker.exists()  # the attacker's line ran, in the server process


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M4a: the signature covers command, args, url, env KEY names and a hash of the "
    "command file only. Editing the server's source (the script `args` names) leaves the "
    "signature 'verified', so a signed registration pins a launch line, not the code",
)
def test_changing_the_servers_source_after_signing_is_refused(sop, base) -> None:
    sop.write_signing("enforce", "deny")
    _tamper_source(sop, base)
    try:
        _out, error, _h, _rows = call(sop)
    finally:
        sop.restore()  # type: ignore[attr-defined]
    assert isinstance(error, MCPSignatureError)


def _repoint_egress(sop, meteo, sink) -> None:
    doc = sop.read_servers()
    env = doc["servers"][0]["env"]
    env["WEATHER_MCP_GEOCODING_URL"] = meteo.env["WEATHER_MCP_GEOCODING_URL"]
    env["WEATHER_MCP_FORECAST_URL"] = f"http://127.0.0.1:{sink.port}/forecast"
    sop.servers_yaml.write_text(yaml.safe_dump(doc))


def test_a_repointed_env_value_still_verifies_and_the_traffic_goes_there(sop, meteo, sink) -> None:
    """Evidence for GAP-M4b: env KEY names are signed, VALUES are not."""
    sop.write_signing("enforce", "deny")
    _repoint_egress(sop, meteo, sink)
    assert sop.verify().returncode == 0
    _out, error, _h, _rows = call(sop)
    assert not isinstance(error, MCPSignatureError)
    assert wait_for(lambda: sink.received)  # the signed server contacted the new host


def wait_for(predicate, seconds: float = 3.0) -> bool:
    from .support import wait_until

    return wait_until(predicate, seconds)


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M4b: env VALUES are not signed (only the key names). Re-pointing "
    "WEATHER_MCP_FORECAST_URL at another host after signing keeps the signature 'verified', "
    "so the operator cannot pin where a signed server sends its traffic",
)
def test_changing_an_env_value_after_signing_is_refused(sop, meteo, sink) -> None:
    sop.write_signing("enforce", "deny")
    _repoint_egress(sop, meteo, sink)
    _out, error, _h, _rows = call(sop)
    assert isinstance(error, MCPSignatureError)


def _executor_runs(sop) -> list[int]:
    with harness(env={ENV_KEY: str(sop.servers_yaml)}) as h:
        ran: list[int] = []
        try:
            make_bridge(sop, h).invoke_external_tool(
                SERVER,
                TOOL,
                {},
                approval_granted=True,
                executor=lambda s, t, a: ran.append(1) or {},
            )
        except MCPSignatureError:
            pass
        return ran


def test_the_executor_path_runs_an_unverified_server_under_enforce(sop) -> None:
    sop.write_signing("enforce", "deny")
    sop.edit_server(args=["-c", "print(1)"])  # tampered
    assert _executor_runs(sop) == [1]


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M4c: invoke_external_tool(executor=...) skips _invoke_transport and with it "
    "_enforce_signature, so an unsigned or tampered server's executor runs under enforce/deny",
)
def test_the_executor_path_is_signature_checked_too(sop) -> None:
    sop.write_signing("enforce", "deny")
    sop.edit_server(args=["-c", "print(1)"])
    assert _executor_runs(sop) == []


def test_the_signing_verdict_is_logged_but_never_written_to_the_ledger(sop) -> None:
    sop.write_signing("enforce", "deny")
    out, error, h, rows = call(sop)  # verified, runs (or fails on transport: irrelevant)
    everything = json.dumps([r["payload"] for r in raw_rows(h)]) + " ".join(
        r["reason"] for r in raw_rows(h)
    )
    for word in ("signature", "signed_by", "ops-test", "verified", "key_id"):
        assert word not in everything
