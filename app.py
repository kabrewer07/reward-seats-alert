"""Flask web UI for managing award flight alerts."""

from __future__ import annotations

import os
import re
from datetime import datetime

import yaml
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, url_for

import state
from monitor import ALERTS_PATH, load_alerts_from_disk, reload_config, start_monitor_daemon

load_dotenv()

SUPPORTED_PROGRAMS = [
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

CABINS = ["economy", "premium_economy", "business", "first"]

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-insecure-change-me")


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


def _parse_alert_from_form(readonly_name: str | None) -> tuple[dict | None, str | None]:
    name = (request.form.get("name") or "").strip()
    if readonly_name is not None:
        name = readonly_name
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

    cabin = (request.form.get("cabin") or "economy").strip().lower()
    if cabin not in CABINS:
        return None, "Invalid cabin."

    try:
        interval_minutes = int(request.form.get("interval_minutes") or 30)
    except ValueError:
        return None, "Check interval must be a number."
    if interval_minutes < 1:
        return None, "Check interval must be at least 1 minute."

    alert: dict = {
        "name": name,
        "enabled": request.form.get("enabled") == "on",
        "interval_minutes": interval_minutes,
        "origins": origins,
        "destinations": destinations,
        "date_range": {"start": start, "end": end},
        "programs": programs,
        "cabin": cabin,
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

    return alert, None


@app.context_processor
def inject_constants():
    return {
        "supported_programs": SUPPORTED_PROGRAMS,
        "cabins": CABINS,
    }


@app.route("/")
def index():
    alerts = load_alerts_from_disk()
    st = state.load_alert_state()
    return render_template("dashboard.html", alerts=alerts, alert_state=st)


@app.route("/alerts/new", methods=["GET", "POST"])
def alert_new():
    if request.method == "POST":
        alert, err = _parse_alert_from_form(None)
        if err:
            flash(err, "error")
            return render_template("alert_form.html", alert=None, fill=request.form, editing=False), 400
        current = load_alerts_from_disk()
        if any(str(a.get("name")) == str(alert.get("name")) for a in current):
            flash("An alert with this name already exists.", "error")
            return render_template("alert_form.html", alert=None, fill=request.form, editing=False), 400
        current.append(alert)
        save_alerts(current)
        reload_config()
        flash("Alert created.", "ok")
        return redirect(url_for("index"))
    return render_template("alert_form.html", alert=None, fill=None, editing=False)


@app.route("/alerts/edit/<path:name>", methods=["GET", "POST"])
def alert_edit(name):
    current = load_alerts_from_disk()
    existing = next((a for a in current if str(a.get("name")) == name), None)
    if existing is None:
        abort(404)
    if request.method == "POST":
        alert, err = _parse_alert_from_form(name)
        if err or alert is None:
            flash(err or "Invalid form.", "error")
            return render_template("alert_form.html", alert=existing, fill=request.form, editing=True), 400
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
    return render_template("alert_form.html", alert=existing, fill=None, editing=True)


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
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
