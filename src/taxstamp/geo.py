"""Geodesy for movement plausibility checks.

Coordinates are stored as integer degrees times 1e7, so no floating-point value is ever
persisted; conversion to radians happens only inside the distance calculation.
"""

from __future__ import annotations

import datetime as dt
from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0088


def distance_km(*, lat_e7_a: int, lon_e7_a: int, lat_e7_b: int, lon_e7_b: int) -> float:
    """Great-circle distance between two e7 coordinates, in kilometres."""
    lat_a, lon_a = radians(lat_e7_a / 1e7), radians(lon_e7_a / 1e7)
    lat_b, lon_b = radians(lat_e7_b / 1e7), radians(lon_e7_b / 1e7)
    delta_lat = lat_b - lat_a
    delta_lon = lon_b - lon_a
    haversine = sin(delta_lat / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin(delta_lon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(min(1.0, sqrt(haversine)))


def implied_speed_kmh(*, distance: float, elapsed: dt.timedelta) -> float | None:
    """Speed the movement implies, or None when no time elapsed at the same place.

    Two observations at the same location with no elapsed time are not a contradiction;
    two observations far apart with no elapsed time are, and return infinity so the
    caller's threshold rejects them.
    """
    seconds = elapsed.total_seconds()
    if seconds < 0:
        raise ValueError("elapsed time must not be negative")
    if seconds == 0:
        return None if distance == 0 else float("inf")
    return distance / (seconds / 3600.0)
