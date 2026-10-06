"""A loopback stand-in for Open-Meteo, and a sink that records who connects.

Nothing here leaves the machine. The stub records every request it serves so a test can
say exactly what the server process sent.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

FORECAST = {
    "utc_offset_seconds": 3600,
    "daily": {
        "time": ["2026-10-06", "2026-10-07"],
        "weather_code": [0, 61],
        "temperature_2m_max": [24.4, 21.0],
        "temperature_2m_min": [15.2, 14.0],
        "precipitation_probability_max": [5, 70],
        "wind_speed_10m_max": [12.3, 25.0],
    },
}


class StubMeteo:
    def __init__(self, place: str = "Lisbon") -> None:
        self.place = place
        self.requests: list[tuple[str, dict[str, list[str]]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                url = urlparse(self.path)
                outer.requests.append((url.path, parse_qs(url.query)))
                if url.path == "/geocode":
                    body = {
                        "results": [
                            {
                                "name": outer.place,
                                "latitude": 38.7,
                                "longitude": -9.1,
                                "country": "Portugal",
                                "admin1": "Lisboa",
                            }
                        ]
                    }
                elif url.path == "/forecast":
                    body = FORECAST
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:  # silence
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def env(self) -> dict[str, str]:
        base = f"http://127.0.0.1:{self.port}"
        return {
            "WEATHER_MCP_GEOCODING_URL": f"{base}/geocode",
            "WEATHER_MCP_FORECAST_URL": f"{base}/forecast",
        }

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


class Sink:
    """A TCP listener that records the connections (and first bytes) it gets."""

    def __init__(self) -> None:
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self.received: list[bytes] = []
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                self.received.append(conn.recv(4096))

    def close(self) -> None:
        self._sock.close()
