#!/usr/bin/env bash
# Local gate for iris-weather-mcp (issue evarness-ai/iris-harness#82). Modeled on
# ../iris-weather-plugin/scripts/ci_local.sh. There is no hosted CI for this repository.
#
# TWO throwaway venvs, because the point of the experiment is that they never meet:
#
#   server venv  mcp + httpx + this package.  NO iris_harness.  Runs the MCP server process.
#   host venv    the iris-harness WHEEL + pytest + ruff + black.  Runs the tests, which drive
#                the harness and spawn the server with the SERVER venv's interpreter.
#
# Steps:
#   1. server venv: install, then PROVE the harness is absent (pip list; failing import)
#   2. host venv:   install the harness wheel and the test tools
#   3. lint:        ruff check . ; black --check .
#   4. isolation:   the server's source names no iris_harness module (AST scan)
#   5. tests:       python -m pytest -q   (loopback stubs only; model scripted; no external net)
#
# Usage:   scripts/ci_local.sh
#
# Environment (all optional):
#   IRIS_HARNESS_CLONE  clone to build the wheel from. Default: ../iris-harness
#   IRIS_WHEEL_DIR      prebuilt wheel directory. Default: ../wheelhouse
#   IRIS_USE_WHEEL=1    skip the build, install the newest wheel in IRIS_WHEEL_DIR.
#   PYTHON              interpreter for the venvs. Default: python3.12.
#
# WHERE THIS IS NOT A HOSTED CI, honestly:
#   1. It builds from your LOCAL clone (whatever commit/edits it has) or a prebuilt wheel.
#   2. The suite needs a package index or pip cache for `mcp` and the harness's dependencies.
#   3. "No external network" is true of the harness process (no_network()) and of the stubs:
#      the server is pointed at loopback by environment. It is NOT enforced on the server
#      process: that the child can reach the network is the finding (docs/COMPARISON.md E3).
#   4. tests/test_compare.py is the in-process control arm; it imports
#      ../iris-weather-plugin/src (not installed) and is skipped, loudly, when absent.
#   5. A few tests assert timing (a hang cut at its timeout); on a heavily loaded machine
#      raise the margins before reading a failure as a regression.
# Bash 3.2 compatible (macOS /bin/bash).

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CLONE="${IRIS_HARNESS_CLONE:-$ROOT/../iris-harness}"
WHEEL_DIR="${IRIS_WHEEL_DIR:-$ROOT/../wheelhouse}"
PYTHON="${PYTHON:-python3.12}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ci_local_mcp.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
SERVER_VENV="$WORK/server-venv"
HOST_VENV="$WORK/host-venv"
BUILT="$WORK/wheelhouse"

step() { printf '\n==> %s\n' "$*"; }
die() { echo "ci_local: $*" >&2; exit 1; }

command -v "$PYTHON" >/dev/null 2>&1 || die "$PYTHON not found (set PYTHON=...)"

step "server venv ($SERVER_VENV): the MCP server and nothing of IRIS"
"$PYTHON" -m venv "$SERVER_VENV" || die "server venv creation failed"
"$SERVER_VENV/bin/pip" install -q --upgrade pip || die "pip upgrade failed"
"$SERVER_VENV/bin/pip" install -q "$ROOT" || die "server install failed"

step "prove the server venv has no iris_harness (pip list, import)"
if "$SERVER_VENV/bin/pip" list 2>/dev/null | grep -i -E '^iris[-_](harness|personal)'; then
  die "the server venv holds the harness"
fi
if "$SERVER_VENV/bin/python" -c "import iris_harness" 2>/dev/null; then
  die "import iris_harness succeeded in the server venv"
fi
"$SERVER_VENV/bin/pip" list 2>/dev/null | grep -i -E '^(iris|mcp|httpx) ' || true
echo "ok: pip list shows no iris-harness; import iris_harness fails"

step "host venv ($HOST_VENV): the harness wheel and the test tools"
"$PYTHON" -m venv "$HOST_VENV" || die "host venv creation failed"
"$HOST_VENV/bin/pip" install -q --upgrade pip build || die "pip/build install failed"
if [ "${IRIS_USE_WHEEL:-0}" != "1" ] && [ -f "$CLONE/pyproject.toml" ]; then
  step "build iris-harness wheel from $CLONE"
  "$HOST_VENV/bin/python" -m build --wheel "$CLONE" --outdir "$BUILT" >/dev/null \
    || die "wheel build failed"
  WHEEL_SRC="$BUILT"
else
  step "use prebuilt wheel from $WHEEL_DIR"
  WHEEL_SRC="$WHEEL_DIR"
fi
WHEEL="$(ls -t "$WHEEL_SRC"/iris_harness-*.whl 2>/dev/null | head -1)"
[ -n "$WHEEL" ] || die "no iris_harness-*.whl in $WHEEL_SRC (set IRIS_HARNESS_CLONE or IRIS_WHEEL_DIR)"
"$HOST_VENV/bin/pip" install -q "$WHEEL" pytest pytest-asyncio ruff black \
  || die "host install failed"
echo "harness: $(basename "$WHEEL")"

step "lint: ruff"
"$HOST_VENV/bin/ruff" check . || die "ruff failed"
step "lint: black --check"
"$HOST_VENV/bin/black" --check . || die "black failed"

step "isolation: the server's source imports nothing from iris_harness"
"$SERVER_VENV/bin/python" - <<'PY' || die "the server source names the harness"
import ast
import sys
from pathlib import Path

bad = []
for path in Path("src").rglob("*.py"):
    for node in ast.walk(ast.parse(path.read_text())):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        bad += [(str(path), n) for n in names if n.split(".")[0].startswith(("iris_h", "iris_p"))]
for b in bad:
    print(b)
sys.exit(1 if bad else 0)
PY

step "tests (server venv interpreter: $SERVER_VENV/bin/python)"
WEATHER_MCP_PYTHON="$SERVER_VENV/bin/python" "$HOST_VENV/bin/python" -m pytest -q \
  -p no:cacheprovider --show-capture=no -rfE || die "pytest failed"

printf '\nci_local: all steps passed\n'
