"""Map IATA codes to IANA timezones and interpret seats.aero clock fields as airport-local."""

from __future__ import annotations

from datetime import datetime, time
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo


@lru_cache(maxsize=1)
def _iata_table() -> dict[str, dict[str, Any]]:
    import airportsdata

    return airportsdata.load("IATA")


@lru_cache(maxsize=1)
def _iata_macs() -> dict[str, Any]:
    import airportsdata

    return airportsdata.load_iata_macs()


def timezone_for_iata(iata: str | None) -> str | None:
    """
    IANA zone name for an airport (e.g. SFO -> America/Los_Angeles).
    Handles multi-airport city codes (e.g. NYC) via airportsdata MAC table.
    """
    if not iata:
        return None
    code = str(iata).strip().upper()
    if len(code) != 3:
        return None
    row = _iata_table().get(code)
    if row and row.get("tz"):
        return str(row["tz"])
    mac = _iata_macs().get(code)
    if mac:
        for ap in (mac.get("airports") or {}).values():
            if isinstance(ap, dict) and ap.get("tz"):
                return str(ap["tz"])
    return None


def parse_iso_wall_at_airport(iso_str: str | None, iata_for_tz: str | None) -> datetime | None:
    """
    seats.aero uses a trailing Z but the clock is local at the relevant airport.
    Interpret the numeric wall time in that airport's IANA timezone when known.
    """
    if not iso_str:
        return None
    s = str(iso_str).strip()
    if s.endswith("Z"):
        s = s[:-1]
    try:
        naive = datetime.fromisoformat(s)
    except ValueError:
        return None
    tz_name = timezone_for_iata(iata_for_tz)
    if tz_name:
        try:
            return naive.replace(tzinfo=ZoneInfo(tz_name))
        except Exception:
            pass
    return naive


def local_wall_time(dt: datetime | None) -> time | None:
    """Clock time at the airport (for comparing depart_after / depart_before)."""
    if dt is None:
        return None
    return time(dt.hour, dt.minute, dt.second, dt.microsecond)


def format_flight_time(iso_str: str | None, iata: str | None) -> str:
    """Human-readable local time with zone abbreviation when known."""
    if not iso_str:
        return ""
    dt = parse_iso_wall_at_airport(iso_str, iata)
    if dt is None:
        return str(iso_str)
    ap = (iata or "").strip().upper()
    if dt.tzinfo:
        abbr = (dt.strftime("%Z") or "").strip()
        base = dt.strftime("%Y-%m-%d %H:%M")
        if abbr:
            return f"{base} {abbr} ({ap})" if ap else f"{base} {abbr}"
        return f"{base} ({ap})" if ap else base
    if ap:
        return f"{dt.strftime('%Y-%m-%d %H:%M')} (no TZ data for {ap})"
    return dt.strftime("%Y-%m-%d %H:%M")
