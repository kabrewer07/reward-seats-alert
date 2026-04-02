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

_CABIN_ORDER: tuple[str, ...] = ("economy", "premium_economy", "business", "first")


def normalize_cabin_key(s: Any) -> str:
    """Map trip/API cabin strings to internal keys (economy, premium_economy, …)."""
    if s is None:
        return ""
    x = str(s).strip().lower().replace(" ", "_").replace("-", "_")
    if x == "premium":
        return "premium_economy"
    return x


def sort_cabin_names(names: list[str]) -> list[str]:
    s = {n for n in names if n in CABIN_TO_PREFIX}
    return [c for c in _CABIN_ORDER if c in s]


def alert_cabin_names(alert: dict[str, Any]) -> list[str]:
    """Selected cabin classes for an alert; supports legacy single `cabin` key."""
    raw = alert.get("cabins")
    if isinstance(raw, list) and raw:
        out: list[str] = []
        for c in raw:
            s = str(c).strip().lower()
            if s in CABIN_TO_PREFIX:
                out.append(s)
        if out:
            return sort_cabin_names(out)
    c = alert.get("cabin")
    if c is not None and str(c).strip():
        s = str(c).strip().lower()
        if s in CABIN_TO_PREFIX:
            return [s]
    return ["economy"]


def _parse_hhmm(s: str) -> time | None:
    parts = s.strip().split(":")
    if len(parts) < 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
        return time(h, m)
    except ValueError:
        return None


def normalize_time_input(s: Any) -> str:
    """Format stored HH:MM (or H:M) for HTML input type=\"time\" (HH:MM)."""
    if s is None:
        return ""
    st = str(s).strip()
    if not st:
        return ""
    t = _parse_hhmm(st)
    if t:
        return f"{t.hour:02d}:{t.minute:02d}"
    return st


def record_precheck(alert: dict[str, Any], record: dict[str, Any]) -> bool:
    for cabin in alert_cabin_names(alert):
        prefix = CABIN_TO_PREFIX.get(cabin)
        if not prefix:
            continue
        if not record.get(f"{prefix}Available"):
            continue
        if alert.get("direct_only") and not record.get(f"{prefix}Direct"):
            continue
        return True
    return False


def trip_matches(alert: dict[str, Any], trip: dict[str, Any]) -> bool:
    allowed = set(alert_cabin_names(alert))
    tc = normalize_cabin_key(trip.get("Cabin"))
    if tc not in allowed:
        return False
    max_pts = alert.get("max_points")
    if max_pts is not None:
        cost = trip.get("MileageCost")
        if cost is None or int(cost) > int(max_pts):
            return False
    if alert.get("direct_only") and int(trip.get("Stops", 99)) != 0:
        return False
    min_seats = alert.get("min_seats")
    if min_seats is not None:
        rs = trip.get("RemainingSeats")
        try:
            if rs is None or int(rs) < int(min_seats):
                return False
        except (TypeError, ValueError):
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
