"""Alert filtering: summary pre-checks and definitive trip-level rules."""

from __future__ import annotations

from datetime import time
from typing import Any

import airport_times

CABIN_TO_PREFIX: dict[str, str] = {
    "economy": "Y",
    "premium_economy": "W",
    "business": "J",
    "first": "F",
}


def _parse_hhmm(s: str) -> time | None:
    parts = s.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
        return time(h, m)
    except ValueError:
        return None


def record_precheck(alert: dict[str, Any], record: dict[str, Any]) -> bool:
    cabin = alert.get("cabin")
    prefix = CABIN_TO_PREFIX.get(str(cabin) if cabin is not None else "")
    if not prefix:
        return False
    if not record.get(f"{prefix}Available"):
        return False
    if alert.get("direct_only") and not record.get(f"{prefix}Direct"):
        return False
    return True


def trip_matches(alert: dict[str, Any], trip: dict[str, Any]) -> bool:
    ac = str(alert.get("cabin", "")).lower()
    tc = str(trip.get("Cabin", "")).lower()
    if tc != ac:
        return False
    max_pts = alert.get("max_points")
    if max_pts is not None:
        cost = trip.get("MileageCost")
        if cost is None or int(cost) > int(max_pts):
            return False
    if alert.get("direct_only") and int(trip.get("Stops", 99)) != 0:
        return False
    max_dur = alert.get("max_duration_hours")
    if max_dur is not None:
        total_min = trip.get("TotalDuration")
        if total_min is None:
            return False
        if float(total_min) / 60.0 > float(max_dur):
            return False
    origin = str(
        trip.get("OriginAirport") or (record.get("Route") or {}).get("OriginAirport") or ""
    ).strip()
    dep = airport_times.parse_iso_wall_at_airport(trip.get("DepartsAt"), origin or None)
    if dep is None:
        return False
    dep_clock = airport_times.local_wall_time(dep)
    if dep_clock is None:
        return False
    da = alert.get("depart_after")
    if da:
        t = _parse_hhmm(str(da))
        if t and dep_clock < t:
            return False
    db = alert.get("depart_before")
    if db:
        t = _parse_hhmm(str(db))
        if t and dep_clock > t:
            return False
    return True


def matching_trips(alert: dict[str, Any], record: dict[str, Any]) -> list[dict[str, Any]]:
    if not record_precheck(alert, record):
        return []
    trips = record.get("AvailabilityTrips") or []
    if not isinstance(trips, list):
        return []
    return [t for t in trips if isinstance(t, dict) and trip_matches(alert, t)]


def match_stats(alert: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Single-pass counts for logging / dashboard (how much data returned vs how strict filters are)."""
    n = sum(1 for r in records if isinstance(r, dict))
    precheck_pass = 0
    trips_ok = 0
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if not record_precheck(alert, rec):
            continue
        precheck_pass += 1
        trips = rec.get("AvailabilityTrips") or []
        if not isinstance(trips, list):
            continue
        for t in trips:
            if isinstance(t, dict) and trip_matches(alert, t):
                trips_ok += 1
    return {
        "records_in_response": n,
        "records_passing_summary_precheck": precheck_pass,
        "trips_passing_all_filters": trips_ok,
    }
