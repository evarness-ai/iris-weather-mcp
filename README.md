# iris-weather-mcp

The weather plugin's forecast tool as a standalone MCP server process, run through the
harness's signed MCP bridge, with **no import of `iris_harness` in the plugin process**.
This is the experiment of evarness-ai/iris-harness#82: comparison data for the
trust-ladder / isolation question, not a production proposal. Nothing here was pushed.

* `src/iris_weather_mcp/`: the server (official `mcp` SDK, stdio). Dependencies: `mcp`, `httpx`.
* `tests/`: the comparison suite. It runs in a SECOND venv that holds the harness wheel and
  spawns the server with the first venv's interpreter. Fault fixtures are stdlib-only servers
  in `tests/servers/`; the upstream is a loopback stub.
* `docs/COMPARISON.md`: in-process vs MCP, per graduation property, with evidence.
* `docs/GAPS.md`: what the bridge and SDK could not do, as precise findings.
* `scripts/ci_local.sh`: the local gate (two fresh venvs, lint, isolation proof, tests).

The in-process plugin (`../iris-weather-plugin`) is the control arm and is not modified.
