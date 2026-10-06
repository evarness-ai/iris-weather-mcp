"""Fault containment across the process boundary: the server crashes, hangs, lies.

Each case drives a misbehaving stdio server (``tests/servers/faulty.py``, standard library
only) through the real bridge and the harness's governance kernel, and records what the
caller, the ledger and the plugin registry see. ``xfail(strict)`` marks a behaviour the
criteria need and the bridge does not provide: it turns red the day it does.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from iris_harness.testing import harness

from .support import (
    ASK,
    LOOP_SCRIPT,
    SERVER,
    TOOL,
    Adapter,
    faulty_args,
    make_bridge,
    mcp_rows,
    pid_alive,
    raw_rows,
    text_of,
)

ENV_KEY = "IRIS_MCP_SERVERS_CONFIG_PATH"


class Call:
    """One bridge call against a faulty server, with a watchdog so a bridge that fails to
    honour its own timeout cannot hang the suite. The child is killed afterwards."""

    def __init__(self, op, base: Path, mode: str, *, timeout: float = 2.0, extra_env=None):
        self.pidfile = base / "pids"
        env = {"WEATHER_MCP_PIDFILE": str(self.pidfile), **(extra_env or {})}
        op.write_servers(args=faulty_args(mode), env=env, timeout=timeout)
        self.op, self.timeout = op, timeout
        self.result = None
        self.error: BaseException | None = None
        self.elapsed = 0.0
        self.finished = False

    def pids(self) -> list[int]:
        return [int(p) for p in self.pidfile.read_text().split()] if self.pidfile.exists() else []

    def run(self, h, arguments=None, *, grace: float = 6.0) -> Call:
        bridge = make_bridge(self.op, h)

        def go() -> None:
            t0 = time.monotonic()
            try:
                self.result = bridge.invoke_external_tool(
                    SERVER, TOOL, arguments or {"location": "x"}, approval_granted=True
                ).result
            except BaseException as exc:  # noqa: BLE001
                self.error = exc
            self.elapsed = time.monotonic() - t0
            self.finished = True

        thread = threading.Thread(target=go, daemon=True)
        thread.start()
        thread.join(self.timeout + grace)
        for pid in self.pids():  # never leave a fixture behind, whatever the bridge did
            if pid_alive(pid) and not self.finished:
                import os
                import signal

                os.kill(pid, signal.SIGKILL)
        thread.join(2)
        return self


def run(op, base, mode, *, timeout=2.0, arguments=None, extra_env=None, fake_model=None):
    call = Call(op, base, mode, timeout=timeout, extra_env=extra_env)
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        call.run(h, arguments)
        call.rows = mcp_rows(h)
        call.plugins = h.plugins()
    return call


# -- the server dies ---------------------------------------------------------------------
def test_a_crash_mid_call_is_an_exception_with_a_pre_row_and_no_post_row(op, base) -> None:
    call = run(op, base, "crash_on_call")
    assert isinstance(call.error, ValueError)
    assert "unexpected EOF while reading MCP stdio message" in str(call.error)
    hooks = [r["hook"] for r in call.rows]
    assert hooks.count("pre_tool_use") == 9 and hooks.count("post_tool_use") == 0
    # the harness does not know the server exists: no plugin record, no failure count
    assert SERVER not in call.plugins and "weather-mcp" not in call.plugins
    assert not any(pid_alive(p) for p in call.pids())


def test_a_crash_at_start_loses_the_servers_own_stderr(op, base) -> None:
    call = run(op, base, "crash_on_start")
    assert isinstance(call.error, ValueError)
    assert "dying at start" not in str(call.error)  # stderr is read only on a timeout


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M8: a stdio server that exits early gives 'unexpected EOF' and nothing of "
    "its stderr or exit code; _read_process_stderr is called only on the timeout path",
)
def test_a_crash_at_start_says_why(op, base) -> None:
    call = run(op, base, "crash_on_start")
    assert "dying at start" in str(call.error) or "exit" in str(call.error).lower()


# -- the server hangs --------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["hang_on_call", "hang_on_init"])
def test_a_hang_is_cut_at_the_timeout_and_the_child_is_killed(op, base, mode) -> None:
    call = run(op, base, mode, timeout=1.0)
    assert isinstance(call.error, ValueError)
    assert "timed out waiting for MCP stdio responses from server 'weather'" in str(call.error)
    assert 0.9 <= call.elapsed < 3.5
    assert wait_dead(call.pids())
    hooks = [r["hook"] for r in call.rows]
    assert hooks.count("post_tool_use") == 0  # same shape as a crash: PRE, then nothing


def wait_dead(pids) -> bool:
    end = time.monotonic() + 3
    while time.monotonic() < end:
        if not any(pid_alive(p) for p in pids):
            return True
        time.sleep(0.05)
    return False


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M6: _run_stdio_exchange checks the deadline with select() and then calls a "
    "blocking readline(); a server that sends half a line and stops blocks the caller past "
    "timeout_seconds until the process dies (observed: 7 s on a 1 s timeout, ended only by "
    "the test watchdog)",
)
def test_a_half_written_response_still_times_out(op, base) -> None:
    call = run(op, base, "partial_line", timeout=1.0)
    assert call.finished and call.elapsed < 3.5


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M7: select() on the pipe fd ignores bytes already in Python's read buffer; "
    "two responses delivered in ONE write time out although both arrived",
)
def test_two_responses_in_one_write_are_both_read(op, base) -> None:
    call = run(op, base, "batched", timeout=2.0)
    assert call.error is None and "Forecast for Fixture" in text_of(call.result)


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M7b: stderr is a pipe nobody drains while the call runs; a server logging "
    "more than the pipe buffer blocks on write and the call times out",
)
def test_a_chatty_server_stderr_does_not_stall_the_call(op, base) -> None:
    call = run(op, base, "stderr_flood", timeout=3.0)
    assert call.error is None and "Forecast for Fixture" in text_of(call.result)


# -- the server lies ---------------------------------------------------------------------
def test_a_non_json_line_is_a_contained_error(op, base) -> None:
    call = run(op, base, "garbage")
    assert call.error is not None and call.finished  # JSONDecodeError (a ValueError)
    assert isinstance(call.error, ValueError)


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M9: a server that prints one non-JSON line to stdout (a banner, a library's "
    "warning) sends the bridge into the Content-Length header branch, whose readline loop has "
    "no deadline: the call blocks until the server dies (observed: 9 s on a 3 s timeout, "
    "ended only by the test watchdog)",
)
def test_a_banner_line_on_stdout_is_ignored(op, base) -> None:
    call = run(op, base, "noise_banner")
    assert call.error is None


def test_a_response_with_an_unexpected_id_ends_the_wait_with_a_keyerror(op, base) -> None:
    """The wait loop stops when it holds as many id-bearing messages as it expects, not when
    it holds the expected ids: any extra message with an id (a server-initiated ping, a
    mis-id'd reply) stands in for the real answer and the lookup then raises KeyError, an
    exception type nothing upstream treats as a transport failure (GAP-M10)."""
    call = run(op, base, "wrong_id", timeout=1.0)
    assert isinstance(call.error, KeyError) and call.elapsed < 1.0


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M10: the stdio wait loop counts id-bearing messages instead of matching the "
    "expected ids, so a stray message ends the wait and the lookup raises a bare KeyError",
)
def test_a_response_with_an_unexpected_id_is_a_clean_transport_error(op, base) -> None:
    call = run(op, base, "wrong_id", timeout=1.0)
    assert isinstance(call.error, ValueError)


@pytest.mark.parametrize("mode", ["bad_str", "bad_noresult", "bad_content"])
def test_a_malformed_result_is_returned_unvalidated_and_passes_post_hooks(op, base, mode) -> None:
    """The bridge returns whatever JSON the server put under `result`, with no MCP result
    schema check; PostToolUse runs over it and allows."""
    call = run(op, base, mode)
    assert call.error is None  # not refused, not an error
    assert call.result is not None
    assert [r["hook"] for r in call.rows].count("post_tool_use") == 3
    assert {r["decision"] for r in call.rows if r["hook"] == "post_tool_use"} == {"allow"}


@pytest.mark.xfail(
    strict=True,
    reason="GAP-M9b: an MCP tool result with isError: true is indistinguishable from success "
    "to the bridge and the ledger (POST allow); in-process a raising tool is charged to its "
    "plugin and counted",
)
def test_an_is_error_result_is_not_recorded_as_a_success(op, base) -> None:
    call = run(op, base, "is_error")
    post = [r for r in call.rows if r["hook"] == "post_tool_use"]
    assert call.error is not None or any(r["decision"] != "allow" for r in post)


def test_a_three_megabyte_result_is_not_capped_by_the_bridge(op, base) -> None:
    call = run(op, base, "huge", timeout=5.0)
    assert call.error is None and len(text_of(call.result)) == 3_000_000


# -- through the governed loop -----------------------------------------------------------
def test_a_crash_inside_a_turn_is_contained_by_the_loop_not_by_the_bridge(op, base) -> None:
    op.write_servers(args=faulty_args("crash_on_call"), timeout=2.0)
    adapter = Adapter(raise_on_failure=True)
    with harness(
        plugins=[adapter.plugin()], fake_model=LOOP_SCRIPT, env={ENV_KEY: str(op.servers_yaml)}
    ) as h:
        adapter.bridge = make_bridge(op, h)
        result = h.chat(ASK)
        assert result.answered  # the turn survived
        loop = [r for r in raw_rows(h, tool=TOOL) if r["hook"] in ("pre_tool_use", "post_tool_use")]
        assert any(r["hook"] == "pre_tool_use" for r in loop)
        # Whatever the loop recorded, the MCP leg left PRE rows and no POST row.
        assert [r["hook"] for r in mcp_rows(h)].count("post_tool_use") == 0
        record = next(r for r in h._runtime.plugin_registry.plugins() if r.name == "mcp-adapter")
        # the failure is charged to the ADAPTER (an in-process plugin), not to the server:
        assert record.failure_count >= 1
        assert "unexpected EOF while reading MCP stdio message" in (record.last_error or "")
        assert SERVER not in {r.name for r in h._runtime.plugin_registry.plugins()}


def test_a_hang_inside_a_turn_blocks_the_turn_for_the_timeout(op, base) -> None:
    op.write_servers(args=faulty_args("hang_on_call"), timeout=1.5)
    adapter = Adapter(raise_on_failure=True)
    with harness(
        plugins=[adapter.plugin()], fake_model=LOOP_SCRIPT, env={ENV_KEY: str(op.servers_yaml)}
    ) as h:
        adapter.bridge = make_bridge(op, h)
        t0 = time.monotonic()
        result = h.chat(ASK)
        elapsed = time.monotonic() - t0
        assert result.answered and elapsed >= 1.5  # the sync bridge call held the turn
