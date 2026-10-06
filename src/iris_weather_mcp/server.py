"""The MCP server: two tools over stdio, newline-delimited JSON-RPC, official SDK.

* ``weather_code_meaning``: deterministic lookup, no I/O.
* ``weather_forecast``: Open-Meteo forecast (the network-backed tool).

Nothing here imports ``iris_harness``; ``tests/test_isolation.py`` proves it three ways.
"""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from . import codes
from .forecast import MAX_DAYS, WeatherError, fetch, render

server = MCPServer("weather-mcp")


@server.tool(
    name="weather_code_meaning",
    description='Explain a WMO weather code in words. Args: {"code": int}.',
)
def weather_code_meaning(code: int) -> str:
    return json.dumps(
        {"code": code, "conditions": codes.describe(code), "category": codes.category(code)}
    )


@server.tool(
    name="weather_forecast",
    description=(
        "Get the daily weather forecast for a place from Open-Meteo. "
        f"Args: location (str), days (int 1-{MAX_DAYS}, default 3)."
    ),
)
def weather_forecast(location: str, days: int = 3) -> str:
    if not location.strip():
        return 'error: pass the place as {"location": "Lisbon"}'
    try:
        return render(fetch(location, days))
    except WeatherError as exc:
        return f"error: {exc}"


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
