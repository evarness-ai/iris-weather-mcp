# iris-weather-mcp

The weather plugin's forecast tool as a standalone MCP server process, run through the
harness's signed MCP bridge, with **no import of `iris_harness` in the plugin process**.
This is the experiment of
[iris-harness#82](https://github.com/evarness-ai/iris-harness/issues/82): comparison data for
the trust-ladder / isolation question, not a production proposal. The server itself is
standalone; the comparison tests are built against iris_harness's stable tier where possible and
record in `docs/GAPS.md` (GAP-M1) the places where they had to import non-stable modules (the
MCP bridge and its signing package), because the stable tier offers no MCP surface.

* `src/iris_weather_mcp/`: the server (official `mcp` SDK, stdio). Dependencies: `mcp`, `httpx`.
* `tests/`: the comparison suite. It runs in a SECOND venv that holds the harness wheel and
  spawns the server with the first venv's interpreter. Fault fixtures are stdlib-only servers
  in `tests/servers/`; the upstream is a loopback stub.
* `docs/COMPARISON.md`: in-process vs MCP, per graduation property, with evidence.
* `docs/GAPS.md`: what the bridge and SDK could not do, as precise findings.
* `scripts/ci_local.sh`: the local gate (two fresh venvs, lint, isolation proof, tests).

The in-process plugin (`../iris-weather-plugin`) is the control arm and is not modified.

## Running the tests

iris-harness is not on a package index yet (iris-harness#108). `scripts/ci_local.sh` is the
gate: it builds two throwaway venvs (the server's, with no harness; the test host's, with an
iris-harness wheel built from the clone given by `IRIS_HARNESS_CLONE`, default
`../iris-harness`, or a prebuilt wheel in `IRIS_WHEEL_DIR`), lints, scans the server source,
and runs the suite. The control arm (`tests/test_compare.py`) imports a sibling checkout of
[iris-weather-plugin](https://github.com/evarness-ai/iris-weather-plugin) at
`../iris-weather-plugin`; it is skipped, loudly, when that is absent. There is no hosted CI for
this repository.

## License and contributing

Apache-2.0, the same license as iris-harness (see `LICENSE`). Contributions follow
[CONTRIBUTING.md](CONTRIBUTING.md).
