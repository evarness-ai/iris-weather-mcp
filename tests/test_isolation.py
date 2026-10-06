"""The plugin process holds no iris_harness: proved four ways (issue #82's hard requirement)."""

from __future__ import annotations

import ast
import json
import subprocess

from .support import REPO, SERVER_PYTHON


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(SERVER_PYTHON), *argv], capture_output=True, text=True, timeout=60, check=False
    )


def test_pip_list_of_the_server_venv_has_no_harness() -> None:
    done = _run("-m", "pip", "list", "--format=json")
    names = {p["name"].lower().replace("_", "-") for p in json.loads(done.stdout)}
    assert not {n for n in names if n.startswith("iris")} - {"iris-weather-mcp"}
    assert "iris-harness" not in names and "iris-personal" not in names
    # and what it does hold, for the record: the MCP SDK and its web stack
    assert {"mcp", "httpx"} <= names


def test_importing_the_harness_fails_in_the_server_venv() -> None:
    done = _run("-c", "import iris_harness")
    assert done.returncode != 0
    assert "ModuleNotFoundError: No module named 'iris_harness'" in done.stderr


def test_the_server_imports_and_runs_with_no_iris_module_loaded() -> None:
    probe = (
        "import sys, iris_weather_mcp.server as s, iris_weather_mcp.forecast;"
        "bad=[m for m in sys.modules if m.split('.')[0].startswith('iris_') "
        "and not m.startswith('iris_weather_mcp')];"
        "print('LOADED', bad)"
    )
    done = _run("-c", probe)
    assert done.returncode == 0, done.stderr
    assert "LOADED []" in done.stdout


def test_no_source_file_of_the_server_names_the_harness() -> None:
    offenders = []
    for path in (REPO / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            offenders += [(path.name, n) for n in names if n.split(".")[0].startswith("iris_h")]
            offenders += [(path.name, n) for n in names if n.split(".")[0] == "iris_personal"]
    assert offenders == []
