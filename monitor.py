"""Background monitor: polls seats.aero for due alerts and sends notifications."""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from dotenv import load_dotenv

import airport_times
import filters
import notifier
import searcher
import state

load_dotenv()

log = logging.getLogger(__name__)

ALERTS_PATH = Path(__file__).resolve().parent / "alerts.yaml"

_wake = threading.Event()
_monitor_thread: threading.Thread | None = None
_startup = True
_alert_state_lock = threading.Lock()


def reload_config() -> None:
    """Wake the monitor loop so YAML changes apply on the next iteration without waiting."""
    _wake.set()


def load_alerts_from_disk() -> list[dict]:
    if not ALERTS_PATH.exists():
        return []
    with ALERTS_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    alerts = data.get("alerts")
    if not isinstance(alerts, list):
        return []
    return [a for a in alerts if isinstance(a, dict)]


def _schedule_next_iso(interval_minutes: int) -> str:
    return (datetime.now() + timedelta(minutes=max(1, int(interval_minutes)))).replace(
        microsecond=0
    ).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _merge_alert_state(name: str, updates: dict) -> None:
    with _alert_state_lock:
        st = state.load_alert_state()
        cur = dict(st.get(name, {}))
        cur.update(updates)
        st[name] = cur
        state.save_alert_state(st)


def run_single_alert(alert: dict) -> None:
    name = str(alert.get("name", ""))
    api_key = (os.environ.get("SEATS_API_KEY") or "").strip()
    now_iso = state.iso_now()

    with _alert_state_lock:
        st = state.load_alert_state()
        prev_total = int(st.get(name, {}).get("total_matches") or 0)
        last_triggered = st.get(name, {}).get("last_triggered")

    _merge_alert_state(name, {"last_checked": now_iso, "error": None})

    interval_default = int(alert.get("interval_minutes") or 30)

    if not api_key:
        _merge_alert_state(
            name,
            {
                "error": "SEATS_API_KEY is not set",
                "next_run": _schedule_next_iso(interval_default),
            },
        )
        return

    origins = alert.get("origins") or []
    dests = alert.get("destinations") or []
    programs = alert.get("programs") or []
    dr = alert.get("date_range") or {}

    if not isinstance(origins, list) or not origins:
        _merge_alert_state(
            name,
            {"error": "No origins configured", "next_run": _schedule_next_iso(interval_default)},
        )
        return
    if not isinstance(dests, list) or not dests:
        _merge_alert_state(
            name,
            {"error": "No destinations configured", "next_run": _schedule_next_iso(interval_default)},
        )
        return
    if not isinstance(programs, list) or not programs:
        _merge_alert_state(
            name,
            {"error": "No programs configured", "next_run": _schedule_next_iso(interval_default)},
        )
        return

    start_d = dr.get("start")
    end_d = dr.get("end")
    if not start_d or not end_d:
        _merge_alert_state(
            name,
            {"error": "Invalid date_range", "next_run": _schedule_next_iso(interval_default)},
        )
        return

    seen = state.load_seen()
    new_count = 0
    do_push = alert.get("push_notifications", True)
    would_notify_if_push_on = 0
    search_debug: dict = {}
    stats: dict = {}
    records: list = []
    skipped_seen = 0
    trip_match_samples: list[dict] = []
    cabin_names = filters.alert_cabin_names(alert)

    try:
        records, search_debug = searcher.search_flights(
            api_key,
            origins=[str(x).upper() for x in origins],
            destinations=[str(x).upper() for x in dests],
            start_date=str(start_d),
            end_date=str(end_d),
            programs=[str(x).lower() for x in programs],
            cabins=cabin_names,
            direct_only=bool(alert.get("direct_only")),
        )
        stats = filters.match_stats(alert, records)
        log.info(
            "Alert %r filter stats: %s",
            name,
            stats,
        )

        for rec in records:
            for trip in filters.matching_trips(alert, rec):
                if len(trip_match_samples) < 12:
                    oa = str(
                        trip.get("OriginAirport") or (rec.get("Route") or {}).get("OriginAirport") or ""
                    )
                    da = str(
                        trip.get("DestinationAirport")
                        or (rec.get("Route") or {}).get("DestinationAirport")
                        or ""
                    )
                    trip_match_samples.append(
                        {
                            "record_date": rec.get("Date"),
                            "record_source": rec.get("Source"),
                            "route": (rec.get("Route") or {}),
                            "trip": {
                                "ID": trip.get("ID"),
                                "Cabin": trip.get("Cabin"),
                                "MileageCost": trip.get("MileageCost"),
                                "Stops": trip.get("Stops"),
                                "TotalDuration_min": trip.get("TotalDuration"),
                                "DepartsAt": trip.get("DepartsAt"),
                                "ArrivesAt": trip.get("ArrivesAt"),
                                "DepartsAt_local_display": airport_times.format_flight_time(
                                    trip.get("DepartsAt"), oa or None
                                ),
                                "ArrivesAt_local_display": airport_times.format_flight_time(
                                    trip.get("ArrivesAt"), da or None
                                ),
                                "FlightNumbers": trip.get("FlightNumbers"),
                                "RemainingSeats": trip.get("RemainingSeats"),
                            },
                        }
                    )
                tid = trip.get("ID")
                if tid is None:
                    continue
                key = f"{name}::{tid}"
                if key in seen:
                    skipped_seen += 1
                    continue
                if not do_push:
                    would_notify_if_push_on += 1
                    continue
                notifier.notify_match(alert, trip, rec)
                state.add_seen(key, seen)
                new_count += 1
                last_triggered = state.iso_now()

        interval = int(alert.get("interval_minutes") or 30)
        last_run_debug = {
            "at": state.iso_now(),
            "alert_cabins": cabin_names,
            "push_notifications_enabled": bool(do_push),
            "search": search_debug,
            "filter_stats": stats,
            "skipped_already_notified": skipped_seen,
            "new_notifications_sent": new_count,
            "would_have_notified_if_push_enabled": would_notify_if_push_on,
            "trips_matching_filters_sample": trip_match_samples,
        }
        log.info(
            "Alert %r check done: matching_trips=%s new_notifications=%s skipped_seen=%s push=%s would_if_off=%s",
            name,
            stats.get("trips_passing_all_filters"),
            new_count,
            skipped_seen,
            do_push,
            would_notify_if_push_on,
        )
        _merge_alert_state(
            name,
            {
                "next_run": _schedule_next_iso(interval),
                "last_triggered": last_triggered,
                "total_matches": prev_total + new_count,
                "error": None,
                "last_run_debug": last_run_debug,
            },
        )
    except Exception as e:
        log.exception("Alert %s failed", name)
        interval = int(alert.get("interval_minutes") or 30)
        last_run_debug = {
            "at": state.iso_now(),
            "alert_cabins": cabin_names,
            "error": str(e),
            "push_notifications_enabled": bool(alert.get("push_notifications", True)),
            "search": search_debug,
            "filter_stats": stats,
            "skipped_already_notified": skipped_seen,
            "new_notifications_sent": new_count,
            "would_have_notified_if_push_enabled": would_notify_if_push_on,
            "trips_matching_filters_sample": trip_match_samples,
        }
        _merge_alert_state(
            name,
            {
                "error": str(e),
                "next_run": _schedule_next_iso(interval),
                "last_triggered": last_triggered,
                "total_matches": prev_total,
                "last_run_debug": last_run_debug,
            },
        )


def start_manual_check(alert_name: str | None = None) -> bool:
    """
    Run `run_single_alert` in background thread(s).

    If ``alert_name`` is set, runs that alert only (even if paused).
    If ``None``, runs every **enabled** alert.

    Returns False if nothing was scheduled (unknown name, or no enabled alerts).
    """
    alerts = load_alerts_from_disk()
    if alert_name is not None:
        key = str(alert_name)
        found = next((a for a in alerts if str(a.get("name")) == key), None)
        if found is None:
            return False
        threading.Thread(
            target=run_single_alert,
            args=(found,),
            name="manual-check",
            daemon=True,
        ).start()
        return True
    started = False
    for a in alerts:
        if a.get("enabled", True):
            threading.Thread(
                target=run_single_alert,
                args=(a,),
                name="manual-check-all",
                daemon=True,
            ).start()
            started = True
    return started


def _alert_due(alert: dict, st: dict, startup: bool) -> bool:
    if not alert.get("enabled", True):
        return False
    if startup:
        return True
    name = str(alert.get("name", ""))
    row = st.get(name) or {}
    nr = row.get("next_run")
    if not nr:
        return True
    t = _parse_iso(str(nr))
    if t is None:
        return True
    return datetime.now() >= t


def _loop() -> None:
    global _startup
    while True:
        alerts = load_alerts_from_disk()
        st = state.load_alert_state()
        due: list[dict] = []
        startup = _startup
        for a in alerts:
            if _alert_due(a, st, startup):
                due.append(a)

        for a in due:
            threading.Thread(target=run_single_alert, args=(a,), daemon=True).start()

        _startup = False

        if _wake.wait(timeout=15):
            _wake.clear()


def start_monitor_daemon() -> None:
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        return
    t = threading.Thread(target=_loop, name="seats-monitor", daemon=True)
    _monitor_thread = t
    t.start()
