"""Open-Meteo client and renderer, with no harness types.

The same two upstream calls as ``iris_plugin_weather_now.openmeteo`` (geocode, forecast),
and the same text as that plugin's ``render``. The forecast is plain dicts here, not the
SDK's ``Forecast`` type: this process cannot import it.

Where the HTTP goes is an environment setting so a test can point the server at a loopback
stub (no external network): ``WEATHER_MCP_GEOCODING_URL`` / ``WEATHER_MCP_FORECAST_URL``.
The server logs each request it makes to ``WEATHER_MCP_EGRESS_LOG`` (a file, one JSON line
per request) when that is set. That log is the SERVER reporting on itself, as the in-process
plugin's ``log_egress`` is: nothing here is visible to the harness.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any

import httpx

from .codes import describe

DEFAULT_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
DEFAULT_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MAX_DAYS = 16
TIMEOUT_S = 10.0

_DAILY = (
    "weather_code,temperature_2m_max,temperature_2m_min,"
    "precipitation_probability_max,wind_speed_10m_max"
)


class WeatherError(Exception):
    """The forecast could not be had; the message is fit for the owner."""


def clamp_days(days: int) -> int:
    return max(1, min(int(days), MAX_DAYS))


def _log(url: str, purpose: str, status: int | str) -> None:
    path = os.environ.get("WEATHER_MCP_EGRESS_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"url": url, "purpose": purpose, "status": status}) + "\n")


def _get_json(http: httpx.Client, url: str, params: dict[str, Any], purpose: str) -> dict[str, Any]:
    response = http.get(url, params=params)
    _log(str(response.request.url.copy_with(query=None)), purpose, response.status_code)
    if response.status_code != 200:
        raise WeatherError(f"the weather service answered HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise WeatherError("the weather service returned something that is not JSON") from exc
    if not isinstance(body, dict):
        raise WeatherError("the weather service returned an unexpected shape")
    return body


def fetch(location: str, days: int = 3) -> dict[str, Any]:
    """{"place": str, "days": [{"date", "summary", "high_c", "low_c", "rain", "wind_kph"}]}."""
    geocoding = os.environ.get("WEATHER_MCP_GEOCODING_URL", DEFAULT_GEOCODING_URL)
    forecast = os.environ.get("WEATHER_MCP_FORECAST_URL", DEFAULT_FORECAST_URL)
    try:
        with httpx.Client(timeout=TIMEOUT_S) as http:
            geo = _get_json(
                http,
                geocoding,
                {"name": location.strip(), "count": 1, "language": "en", "format": "json"},
                "geocode",
            )
            results = geo.get("results") or []
            if not results:
                raise WeatherError(f"no place found called {location!r}")
            top = results[0]
            try:
                lat, lon = float(top["latitude"]), float(top["longitude"])
            except (KeyError, TypeError, ValueError) as exc:
                raise WeatherError("the geocoder returned a place without coordinates") from exc
            parts = [top.get("name"), top.get("admin1"), top.get("country")]
            place = ", ".join(dict.fromkeys(str(p) for p in parts if p)) or location.strip()
            body = _get_json(
                http,
                forecast,
                {
                    "latitude": lat,
                    "longitude": lon,
                    "daily": _DAILY,
                    "timezone": "auto",
                    "forecast_days": clamp_days(days),
                    "wind_speed_unit": "kmh",
                },
                "forecast",
            )
    except httpx.HTTPError as exc:
        raise WeatherError(
            f"the weather service could not be reached ({type(exc).__name__})"
        ) from exc
    return _parse(body, place)


def _parse(payload: dict[str, Any], place: str) -> dict[str, Any]:
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    if not times:
        raise WeatherError("the forecast service returned no days")

    def column(key: str) -> list[Any]:
        values = list(daily.get(key) or [])
        return values + [None] * (len(times) - len(values))

    codes, highs, lows = (
        column("weather_code"),
        column("temperature_2m_max"),
        column("temperature_2m_min"),
    )
    rain, wind = column("precipitation_probability_max"), column("wind_speed_10m_max")
    out = []
    for i, day in enumerate(times):
        if highs[i] is None:
            continue
        summary = describe(int(codes[i])) if codes[i] is not None else describe(-1)
        if lows[i] is not None:
            summary = f"{summary}, low {float(lows[i]):.0f} C"
        out.append(
            {
                "date": date.fromisoformat(day).isoformat(),
                "summary": summary,
                "high_c": float(highs[i]),
                "rain": None if rain[i] is None else float(rain[i]) / 100.0,
                "wind_kph": None if wind[i] is None else float(wind[i]),
            }
        )
    if not out:
        raise WeatherError("the forecast service returned no usable days")
    return {"place": place, "days": out}


def render(forecast: dict[str, Any]) -> str:
    """The forecast as the observation text (identical to the in-process plugin's)."""
    lines = [f"Forecast for {forecast['place']}:"]
    for d in forecast["days"]:
        rain = "rain chance n/a" if d["rain"] is None else f"rain chance {d['rain']:.0%}"
        wind = "" if d["wind_kph"] is None else f", wind up to {d['wind_kph']:.0f} km/h"
        lines.append(f"- {d['date']}: {d['summary']}, high {d['high_c']:.0f} C, {rain}{wind}")
    return "\n".join(lines)
