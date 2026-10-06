"""Fixtures for the comparison suite. The server venv (no harness) comes from
``WEATHER_MCP_PYTHON``; ``scripts/ci_local.sh`` builds it."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from .stub_meteo import Sink, StubMeteo
from .support import SERVER_PYTHON, Operator, new_operator, tmp_base


def pytest_report_header(config):
    return f"weather-mcp: server interpreter {SERVER_PYTHON} (exists={SERVER_PYTHON.exists()})"


@pytest.fixture(autouse=True, scope="session")
def _server_venv_exists() -> None:
    assert SERVER_PYTHON.exists(), f"no server venv at {SERVER_PYTHON}; run scripts/ci_local.sh"


@pytest.fixture
def base() -> Iterator[Path]:
    path = tmp_base()
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def op(base: Path) -> Operator:
    return new_operator(base)


@pytest.fixture
def meteo() -> Iterator[StubMeteo]:
    stub = StubMeteo()
    yield stub
    stub.close()


@pytest.fixture
def sink() -> Iterator[Sink]:
    s = Sink()
    yield s
    s.close()
