"""Flask web UI for managing award flight alerts."""

from __future__ import annotations

import copy
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, url_for

import filters as alert_filters
import program_catalog
import state
from jinja2.runtime import Undefined
from monitor import (
    ALERTS_PATH,
    load_alerts_from_disk,
    reload_config,
    start_manual_check,
    start_monitor_daemon,
)

load_dotenv()

SUPPORTED_PROGRAMS = sorted(
    [
        "american",
        "united",
        "aeroplan",
        "delta",
        "alaska",
        "virginatlantic",
        "flyingblue",
        "emirates",
        "etihad",
        "singapore",
        "qantas",
        "turkish",
        "lufthansa",
        "finnair",
        "eurobonus",
        "velocity",
        "jetblue",
        "qatar",
        "aeromexico",
        "connectmiles",
        "smiles",
        "azul",
        "ethiopian",
        "saudia",
    ]
)

CABINS = ["economy", "premium_economy", "business", "first"]

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-insecure-change-me")

# Dashboard schedule times: US Pacific (PST/PDT). ISO strings from state are treated as
# that zone if naive (matches typical single-user Mac setup); Z-suffixed values as UTC.
_DASHBOARD_TZ = ZoneInfo(os.environ.get("DASHBOARD_TIMEZONE", "America/Los_Angeles"))


def format_dashboard_time(iso_str: str | None) -> str | None:
    if iso_str is None:
        return None
    s = str(iso_str).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(s)
    except ValueError:
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_DASHBOARD_TZ)
    else:
        dt = dt.astimezone(_DASHBOARD_TZ)
    return dt.strftime("%Y-%m-%d %I:%M %p %Z")


@app.template_filter("time_input")
def time_input_filter(s):
    if isinstance(s, Undefined):
        return ""
    return alert_filters.normalize_time_input(s)


@app.template_filter("alert_cabins")
def alert_cabins_filter(alert):
    if isinstance(alert, Undefined):
        return []
    return alert_filters.alert_cabin_names(alert)


@app.template_filter("dashboard_time")
def dashboard_time_filter(iso_str: str | None) -> str:
    return format_dashboard_time(iso_str) or "—"


@app.template_filter("transfer_ratios_cell")
def transfer_ratios_cell_filter(tr):
    return program_catalog.format_transfer_cell(tr)


@app.template_filter("alliance_cell")
def alliance_cell_filter(entry):
    if not isinstance(entry, dict):
        return "—"
    return program_catalog.alliance_display(entry)


def save_alerts(alerts: list[dict]) -> None:
    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ALERTS_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(
            {"alerts": alerts},
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )


def _split_airports(s: str) -> list[str]:
    parts = re.split(r"[\s,]+", (s or "").strip().upper())
    return [p for p in parts if p]


def _alert_name_taken(
    alerts: list[dict], name: str, *, editing_original: str | None = None
) -> bool:
    for a in alerts:
        n = str(a.get("name"))
        if n != name:
            continue
        if editing_original is not None and n == editing_original:
            continue
        return True
    return False


def _next_duplicate_name(alerts: list[dict], original_name: str) -> str:
    base = f"{original_name} (copy)"
    if not _alert_name_taken(alerts, base):
        return base
    n = 2
    while True:
        candidate = f"{original_name} (copy {n})"
        if not _alert_name_taken(alerts, candidate):
            return candidate
        n += 1


def _parse_alert_from_form() -> tuple[dict | None, str | None]:
    name = (request.form.get("name") or "").strip()
    if not name:
        return None, "Name is required."

    origins = _split_airports(request.form.get("origins_text", ""))
    destinations = _split_airports(request.form.get("destinations_text", ""))
    programs = request.form.getlist("programs")
    programs = [p.strip().lower() for p in programs if p.strip()]

    start = (request.form.get("date_start") or "").strip()
    end = (request.form.get("date_end") or "").strip()
    try:
        if start:
            datetime.strptime(start, "%Y-%m-%d")
        if end:
            datetime.strptime(end, "%Y-%m-%d")
    except ValueError:
        return None, "Dates must be YYYY-MM-DD."

    if not origins:
        return None, "At least one origin airport is required."
    if not destinations:
        return None, "At least one destination airport is required."
    if not programs:
        return None, "Select at least one mileage program."

    raw_cabins = [c.strip().lower() for c in request.form.getlist("cabins") if c.strip()]
    for c in raw_cabins:
        if c not in CABINS:
            return None, "Invalid cabin class."
    if not raw_cabins:
        return None, "Select at least one cabin class."

    try:
        interval_minutes = int(request.form.get("interval_minutes") or 30)
    except ValueError:
        return None, "Check interval must be a number."
    if interval_minutes < 1:
        return None, "Check interval must be at least 1 minute."

    alert: dict = {
        "name": name,
        "enabled": request.form.get("enabled") == "on",
        "push_notifications": request.form.get("push_notifications") == "on",
        "interval_minutes": interval_minutes,
        "origins": origins,
        "destinations": destinations,
        "date_range": {"start": start, "end": end},
        "programs": programs,
        "cabins": alert_filters.sort_cabin_names(raw_cabins),
    }

    max_points = (request.form.get("max_points") or "").strip()
    if max_points:
        try:
            alert["max_points"] = int(max_points)
        except ValueError:
            return None, "Max points must be a number."

    if request.form.get("direct_only") == "on":
        alert["direct_only"] = True

    max_dur = (request.form.get("max_duration_hours") or "").strip()
    if max_dur:
        try:
            alert["max_duration_hours"] = float(max_dur)
        except ValueError:
            return None, "Max duration must be a number."

    da = (request.form.get("depart_after") or "").strip()
    if da:
        alert["depart_after"] = da
    db = (request.form.get("depart_before") or "").strip()
    if db:
        alert["depart_before"] = db

    min_seats = (request.form.get("min_seats") or "").strip()
    if min_seats:
        try:
            ms = int(min_seats)
            if ms < 1:
                return None, "Minimum seats must be at least 1."
            alert["min_seats"] = ms
        except ValueError:
            return None, "Minimum seats must be a whole number."

    return alert, None


@app.context_processor
def inject_constants():
    fsup = frozenset(SUPPORTED_PROGRAMS)
    bank_quick = [
        (k, program_catalog.BANK_LABELS.get(k, k.replace("_", " ").title()))
        for k in program_catalog.all_transfer_bank_keys()
        if program_catalog.sources_for_bank(k, fsup)
    ]
    alliance_quick = [
        (k, program_catalog.ALLIANCE_LABELS[k])
        for k in ("star_alliance", "skyteam", "oneworld", "independent")
        if program_catalog.sources_for_alliance(k, fsup)
    ]
    _pl = program_catalog.display_labels(SUPPORTED_PROGRAMS)
    programs_alpha = sorted(
        SUPPORTED_PROGRAMS, key=lambda p: (_pl.get(p, p).lower(), str(p).lower())
    )
    return {
        "supported_programs": programs_alpha,
        "cabins": CABINS,
        "program_form_meta": program_catalog.supported_program_rows(SUPPORTED_PROGRAMS),
        "program_display_labels": program_catalog.display_labels(SUPPORTED_PROGRAMS),
        "bank_quick_picks": bank_quick,
        "alliance_quick_picks": alliance_quick,
    }


@app.route("/transfer-partners")
def transfer_partners():
    banks = program_catalog.all_transfer_bank_keys()
    bank_option_labels = {
        k: program_catalog.BANK_LABELS.get(k, k.replace("_", " ").title()) for k in banks
    }
    return render_template(
        "transfer_reference.html",
        entries=sorted(
            program_catalog.all_entries(),
            key=lambda e: (str(e.get("mileage_program") or e.get("source") or "")).lower(),
        ),
        filter_banks=banks,
        bank_option_labels=bank_option_labels,
    )


@app.route("/")
def index():
    alerts = load_alerts_from_disk()
    alerts = sorted(
        alerts,
        key=lambda a: (not bool(a.get("enabled", True)), str(a.get("name", "")).lower()),
    )
    st = state.load_alert_state()
    return render_template("dashboard.html", alerts=alerts, alert_state=st)


@app.route("/alerts/check-now/<path:name>", methods=["POST"])
def alert_check_now(name):
    if not start_manual_check(name):
        flash("Unknown alert.", "error")
    else:
        flash(f"Check started for “{name}”. Refresh in a moment for status.", "ok")
    return redirect(url_for("index"))


@app.route("/alerts/check-now-all", methods=["POST"])
def alerts_check_now_all():
    if not start_manual_check(None):
        flash("No enabled alerts to check.", "error")
    else:
        flash("Check started for all enabled alerts.", "ok")
    return redirect(url_for("index"))


@app.route("/alerts/new", methods=["GET", "POST"])
def alert_new():
    if request.method == "POST":
        alert, err = _parse_alert_from_form()
        if err:
            flash(err, "error")
            return render_template("alert_form.html", af=None, fill=request.form, editing=False), 400
        current = load_alerts_from_disk()
        if _alert_name_taken(current, str(alert.get("name"))):
            flash("An alert with this name already exists.", "error")
            return render_template("alert_form.html", af=None, fill=request.form, editing=False), 400
        current.append(alert)
        save_alerts(current)
        reload_config()
        flash("Alert created.", "ok")
        return redirect(url_for("index"))
    return render_template("alert_form.html", af=None, fill=None, editing=False)


@app.route("/alerts/edit/<path:name>", methods=["GET", "POST"])
def alert_edit(name):
    current = load_alerts_from_disk()
    existing = next((a for a in current if str(a.get("name")) == name), None)
    if existing is None:
        abort(404)
    if request.method == "POST":
        alert, err = _parse_alert_from_form()
        if err or alert is None:
            flash(err or "Invalid form.", "error")
            return render_template("alert_form.html", af=existing, fill=request.form, editing=True), 400
        new_name = str(alert.get("name"))
        if _alert_name_taken(current, new_name, editing_original=name):
            flash("An alert with this name already exists.", "error")
            return render_template("alert_form.html", af=existing, fill=request.form, editing=True), 400
        if new_name != name:
            state.rename_alert_state(name, new_name)
            state.rename_seen_alert_prefix(name, new_name)
        updated = []
        for a in current:
            if str(a.get("name")) == name:
                updated.append(alert)
            else:
                updated.append(a)
        save_alerts(updated)
        reload_config()
        flash("Alert updated.", "ok")
        return redirect(url_for("index"))
    return render_template("alert_form.html", af=existing, fill=None, editing=True)


@app.route("/alerts/duplicate/<path:name>", methods=["POST"])
def alert_duplicate(name):
    current = load_alerts_from_disk()
    source = next((a for a in current if str(a.get("name")) == name), None)
    if source is None:
        abort(404)
    dup = copy.deepcopy(source)
    dup["name"] = _next_duplicate_name(current, str(source.get("name", "")))
    current.append(dup)
    save_alerts(current)
    reload_config()
    flash(f"Duplicated as “{dup['name']}”.", "ok")
    return redirect(url_for("index"))


@app.route("/alerts/toggle/<path:name>", methods=["POST"])
def alert_toggle(name):
    current = load_alerts_from_disk()
    found = False
    for a in current:
        if str(a.get("name")) == name:
            a["enabled"] = not bool(a.get("enabled", True))
            found = True
            break
    if not found:
        abort(404)
    save_alerts(current)
    reload_config()
    flash("Alert toggled.", "ok")
    return redirect(url_for("index"))


@app.route("/alerts/delete/<path:name>", methods=["POST"])
def alert_delete(name):
    current = load_alerts_from_disk()
    new_list = [a for a in current if str(a.get("name")) != name]
    if len(new_list) == len(current):
        abort(404)
    save_alerts(new_list)
    state.remove_alert_from_state(name)
    state.prune_seen_for_alert(name, set())
    reload_config()
    flash("Alert deleted.", "ok")
    return redirect(url_for("index"))


def main() -> None:
    import logging

    logging.basicConfig(level=logging.INFO)
    start_monitor_daemon()
    port = int(os.environ.get("FLASK_PORT", "5000"))
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
