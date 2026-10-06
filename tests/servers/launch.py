"""Start the real server and record its pid first (so a test can see it spawned and died).

Stdlib only; ``runpy`` then runs ``iris_weather_mcp.server`` exactly as ``-m`` would.
"""

import os
import runpy

pidfile = os.environ.get("WEATHER_MCP_PIDFILE")
if pidfile:
    with open(pidfile, "a", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()}\n")
runpy.run_module("iris_weather_mcp.server", run_name="__main__")
