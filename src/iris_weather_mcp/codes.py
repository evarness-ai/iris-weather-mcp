"""WMO weather codes: the table behind the deterministic ``weather_code_meaning`` tool.

Open-Meteo reports conditions as WMO "weather interpretation" codes. The words here are
this plugin's own (not the service's), so a forecast's summaries are the same on every
run and testable without a network.
"""

from __future__ import annotations

# code -> (conditions, category). Category is one of clear, cloud, fog, drizzle, rain,
# snow, showers, storm: what a consumer needs to decide "umbrella or not".
WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("Clear sky", "clear"),
    1: ("Mainly clear", "clear"),
    2: ("Partly cloudy", "cloud"),
    3: ("Overcast", "cloud"),
    45: ("Fog", "fog"),
    48: ("Depositing rime fog", "fog"),
    51: ("Light drizzle", "drizzle"),
    53: ("Moderate drizzle", "drizzle"),
    55: ("Dense drizzle", "drizzle"),
    56: ("Light freezing drizzle", "drizzle"),
    57: ("Dense freezing drizzle", "drizzle"),
    61: ("Slight rain", "rain"),
    63: ("Moderate rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Light freezing rain", "rain"),
    67: ("Heavy freezing rain", "rain"),
    71: ("Slight snow fall", "snow"),
    73: ("Moderate snow fall", "snow"),
    75: ("Heavy snow fall", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Slight rain showers", "showers"),
    81: ("Moderate rain showers", "showers"),
    82: ("Violent rain showers", "showers"),
    85: ("Slight snow showers", "showers"),
    86: ("Heavy snow showers", "showers"),
    95: ("Thunderstorm", "storm"),
    96: ("Thunderstorm with slight hail", "storm"),
    99: ("Thunderstorm with heavy hail", "storm"),
}

UNKNOWN = "Unknown conditions"


def describe(code: int) -> str:
    """The conditions for a WMO code; a code outside the table is ``UNKNOWN``."""
    return WMO_CODES.get(code, (UNKNOWN, "unknown"))[0]


def category(code: int) -> str:
    """The coarse category of a WMO code (``unknown`` outside the table)."""
    return WMO_CODES.get(code, (UNKNOWN, "unknown"))[1]
