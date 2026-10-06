"""The only shipped surface that reaches the bridge: the IRIS API's /api/v1/mcp router.

`server/iris_api/mcp_server.py` mounts the bridge's routes and grants no approval (the HTTP
API cannot ask the owner). With the shipped governor policy (`coding/mcp` is
`requires_approval: true`), a call can therefore never succeed over HTTP.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from iris_harness.server.iris_api.mcp_server import create_mcp_router
from iris_harness.testing import harness

from .support import SERVER, TOOL, make_bridge

ENV_KEY = "IRIS_MCP_SERVERS_CONFIG_PATH"


def test_every_http_call_to_a_registered_server_is_refused_for_want_of_approval(op, meteo) -> None:
    op.write_servers(env=meteo.env)
    with harness(env={ENV_KEY: str(op.servers_yaml)}) as h:
        app = FastAPI()
        app.include_router(create_mcp_router(make_bridge(op, h)))
        client = TestClient(app)
        config = client.get("/api/v1/mcp/config").json()
        assert config["enabled"] is True and config["configured_servers"][0]["name"] == SERVER
        session = client.post(f"/api/v1/mcp/servers/{SERVER}/session", json={})
        assert session.status_code == 200  # a decision, not an error: allowed is false
        assert session.json()["decision"]["allowed"] is False
        assert session.json()["decision"]["requires_approval"] is True
        for path, body in (
            (f"/api/v1/mcp/servers/{SERVER}/tools/list", {}),
            (f"/api/v1/mcp/servers/{SERVER}/tools/{TOOL}/call", {"arguments": {"location": "x"}}),
        ):
            response = client.post(path, json=body)
            assert response.status_code == 403, (path, response.text)
            assert "approval required" in response.json()["detail"]
        assert meteo.requests == []  # the server was never reached
