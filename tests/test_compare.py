"""The control arm: the SAME checks against the in-process weather plugin, in the same
harness, so each comparison in docs/COMPARISON.md is measured side by side, not remembered.

The in-process plugin (../iris-weather-plugin) is imported from its checkout and not
modified. Skipped when that checkout is not next to this repository.
"""

from __future__ import annotations

import json
import logging
import statistics
import sys
import time
from pathlib import Path

import pytest

PLUGIN_SRC = Path(__file__).resolve().parents[2] / "iris-weather-plugin" / "src"
if PLUGIN_SRC.exists() and str(PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SRC))

pytest.importorskip("iris_plugin_weather_now", reason="control arm: iris-weather-plugin absent")

import httpx  # noqa: E402
from iris_harness.sdk import PluginAPI  # noqa: E402
from iris_harness.testing import harness, plugin  # noqa: E402
from iris_plugin_weather_now import plugin as weather  # noqa: E402

from .support import (  # noqa: E402
    ASK,
    INJECTION,
    LOOP_SCRIPT,
    OWNER,
    TOOL,
    Adapter,
    make_bridge,
    raw_rows,
)
from .test_content import identity_source  # noqa: E402

MANIFEST = Path(weather.__file__).with_name("manifest.yaml")
ENV_KEY = "IRIS_MCP_SERVERS_CONFIG_PATH"


def meteo_transport(place: str = "Lisbon") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": place,
                            "latitude": 38.7,
                            "longitude": -9.1,
                            "country": "Portugal",
                            "admin1": "Lisboa",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "utc_offset_seconds": 0,
                "daily": {
                    "time": ["2026-10-06", "2026-10-07"],
                    "weather_code": [0, 61],
                    "temperature_2m_max": [24.4, 21.0],
                    "temperature_2m_min": [15.2, 14.0],
                    "precipitation_probability_max": [5, 70],
                    "wind_speed_10m_max": [12.3, 25.0],
                },
            },
        )

    return httpx.MockTransport(handler)


def in_process(place: str = "Lisbon"):
    transport = meteo_transport(place)
    return plugin(weather.make_setup(transport, transport), manifest=MANIFEST)


def tool_rows(h, route: str = TOOL):
    return [r for r in raw_rows(h, tool=route) if r["hook"] in ("pre_tool_use", "post_tool_use")]


def test_in_process_arm_pre_and_post_rows_with_the_model_as_caller() -> None:
    with harness(plugins=[in_process()], fake_model=LOOP_SCRIPT) as h:
        assert h.chat(ASK).answered
        rows = tool_rows(h)
        assert [r["hook"] for r in rows].count("pre_tool_use") == 10
        assert [r["hook"] for r in rows].count("post_tool_use") == 4
        assert {r["payload"]["caller"] for r in rows} == {"model:system"}
        assert {r["agent"] for r in rows} == {"system"}


def test_in_process_arm_logs_its_egress_inside_the_harness(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="iris.egress"):
        with harness(plugins=[in_process()], fake_model=LOOP_SCRIPT) as h:
            h.chat(ASK)
    lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "iris.egress" and r.getMessage().startswith("EGRESS service")
    ]
    assert len(lines) == 2 and all(" -> https://" in ln for ln in lines)
    assert any("geocoding-api.open-meteo.com" in ln for ln in lines)


def test_in_process_arm_plain_tool_result_is_not_masked() -> None:
    with harness(
        plugins=[identity_source(), in_process(place=f"Home of {OWNER}")], fake_model=LOOP_SCRIPT
    ) as h:
        h.chat(ASK)
        assert OWNER in h.model_calls()[-1].user


def test_in_process_arm_classifier_reads_the_result() -> None:
    with harness(plugins=[in_process()], fake_model=LOOP_SCRIPT) as h:
        h.chat(ASK)
        reasons = {r["reason"] for r in tool_rows(h) if r["plugin"] == "output_classifier"}
        assert reasons == {"output_classifier: weather_forecast output is public; run stays public"}


def test_in_process_arm_guard_reaches_the_classifier_and_fails_open() -> None:
    with harness(
        plugins=[in_process(place=INJECTION)],
        fake_model=LOOP_SCRIPT,
        env={"IRIS_GOVERNANCE_PROMPT_GUARD": "1"},
    ) as h:
        h.chat(ASK)
        scanned = [r for r in tool_rows(h) if r["plugin"] == "prompt_guard_retrieved"]
        assert {r["reason"] for r in scanned} == {"prompt_guard_retrieved: guard unavailable"}
        assert INJECTION in h.model_calls()[-1].user


def test_in_process_arm_a_raising_tool_still_gets_its_post_row() -> None:
    """Control for the crash case: in-process, PRE and POST are both written and the plugin is
    charged (graduation P0-6); over MCP a crash leaves PRE only (test_faults.py)."""

    def setup(api: PluginAPI) -> None:
        def boom(args):
            raise RuntimeError("kaboom")

        api.register_tool(TOOL, "boom", boom)

    boomer = plugin(
        setup,
        manifest={
            "name": "boomer",
            "version": "0",
            "provides": ["tool"],
            "tools": {TOOL: {"effect": "read", "content": "external"}},
        },
    )
    with harness(plugins=[boomer], fake_model=LOOP_SCRIPT) as h:
        assert h.chat(ASK).answered
        hooks = [r["hook"] for r in tool_rows(h)]
        assert hooks.count("pre_tool_use") >= 9 and hooks.count("post_tool_use") >= 3
        record = next(r for r in h._runtime.plugin_registry.plugins() if r.name == "boomer")
        assert record.status.value == "degraded" and record.failure_count >= 1


def test_latency_of_a_turn_through_each_arm(op, meteo) -> None:
    """One machine, one run: wall time of a whole scripted turn that makes one forecast call.
    Recorded for COMPARISON.md; asserts only that both arms answered."""

    def measure(h, n: int = 4) -> list[float]:
        times = []
        for i in range(n):
            t0 = time.perf_counter()
            assert h.chat(ASK, session_id=f"s{i}").answered
            times.append(time.perf_counter() - t0)
        return times

    with harness(plugins=[in_process()], fake_model=LOOP_SCRIPT) as h:
        inproc = measure(h)
    op.write_servers(env=meteo.env)
    adapter = Adapter()
    with harness(
        plugins=[adapter.plugin()], fake_model=LOOP_SCRIPT, env={ENV_KEY: str(op.servers_yaml)}
    ) as h:
        adapter.bridge = make_bridge(op, h)
        mcp = measure(h)
    out = Path(__file__).parent / "_out"
    out.mkdir(exist_ok=True)
    (out / "turn_latency.json").write_text(
        json.dumps(
            {
                "in_process_s": inproc,
                "mcp_s": mcp,
                "in_process_median_s": statistics.median(inproc),
                "mcp_median_s": statistics.median(mcp),
            }
        )
    )
