"""Push notifications via Pushover or Telegram."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlencode

import requests

import airport_times
import filters

log = logging.getLogger(__name__)

CABIN_EMOJI = {
    "economy": "✈️",
    "premium_economy": "🅿️",
    "business": "💼",
    "first": "👑",
}

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

# https://pushover.net/api#limits
PUSHOVER_TITLE_MAX = 250
PUSHOVER_MESSAGE_MAX = 1024
PUSHOVER_URL_MAX = 512
PUSHOVER_URL_TITLE_MAX = 100


def _truncate(s: str, max_len: int, *, ellipsis: str = "…") -> str:
    if max_len <= 0:
        return ""
    if len(s) <= max_len:
        return s
    if max_len <= len(ellipsis):
        return s[:max_len]
    return s[: max_len - len(ellipsis)] + ellipsis


def _pushover_clip(title: str, message: str, url: str, url_title: str) -> tuple[str, str, str, str]:
    """Enforce Pushover size limits (400 Bad Request if exceeded)."""
    url_title = _truncate(url_title, PUSHOVER_URL_TITLE_MAX)
    if len(url) > PUSHOVER_URL_MAX:
        extra = f"\n\nLink: {url}"
        message = _truncate(message + extra, PUSHOVER_MESSAGE_MAX)
        url = "https://seats.aero/search"
    else:
        message = _truncate(message, PUSHOVER_MESSAGE_MAX)
    title = _truncate(title, PUSHOVER_TITLE_MAX)
    return title, message, url, url_title


def _cabin_for_search_url(cabin: str) -> str:
    if cabin == "premium_economy":
        return "premium"
    return cabin


def _deep_link(origin: str, dest: str, cabin: str, source: str) -> str:
    q = urlencode(
        {
            "origin": origin,
            "destination": dest,
            "cabin": _cabin_for_search_url(cabin),
            "source": source,
        }
    )
    return f"https://seats.aero/search?{q}"


def _format_body(trip: dict[str, Any], record: dict[str, Any]) -> str:
    origin = str(trip.get("OriginAirport") or record.get("Route", {}).get("OriginAirport", ""))
    dest = str(trip.get("DestinationAirport") or record.get("Route", {}).get("DestinationAirport", ""))
    dep = airport_times.format_flight_time(trip.get("DepartsAt"), origin or None)
    arr = airport_times.format_flight_time(trip.get("ArrivesAt"), dest or None)
    dur_min = trip.get("TotalDuration")
    stops = int(trip.get("Stops", 0))
    legs = "Nonstop" if stops == 0 else f"{stops} stop(s)"
    fn = trip.get("FlightNumbers", "")
    ac = trip.get("Aircraft")
    if isinstance(ac, list):
        ac_str = ", ".join(str(x) for x in ac)
    else:
        ac_str = str(ac or "")
    seats = trip.get("RemainingSeats")
    pts = trip.get("MileageCost")
    tax = trip.get("TotalTaxes")
    date = record.get("Date", "")
    lines = [
        f"Date: {date}",
        f"Departs (local): {dep or trip.get('DepartsAt', '')}",
        f"Arrives (local): {arr or trip.get('ArrivesAt', '')}",
    ]
    if dur_min is not None:
        h, m = divmod(int(dur_min), 60)
        lines.append(f"Duration: {h}h {m}m")
    lines.append(legs)
    if fn:
        lines.append(f"Flights: {fn}")
    if ac_str:
        lines.append(f"Aircraft: {ac_str}")
    if seats is not None:
        lines.append(f"Seats: {seats}")
    cost_parts = []
    if pts is not None:
        try:
            cost_parts.append(f"{int(pts):,} pts")
        except (TypeError, ValueError):
            cost_parts.append(f"{pts} pts")
    if tax is not None:
        try:
            cost_parts.append(f"+ {int(tax):,} taxes")
        except (TypeError, ValueError):
            cost_parts.append(f"+ {tax} taxes")
    if cost_parts:
        lines.append(" / ".join(cost_parts))
    return "\n".join(lines)


def notify_match(
    alert: dict[str, Any],
    trip: dict[str, Any],
    record: dict[str, Any],
) -> None:
    notifier = (os.environ.get("NOTIFIER") or "pushover").strip().lower()
    origin = str(trip.get("OriginAirport") or record.get("Route", {}).get("OriginAirport", ""))
    dest = str(
        trip.get("DestinationAirport") or record.get("Route", {}).get("DestinationAirport", "")
    )
    cabin = filters.normalize_cabin_key(trip.get("Cabin")) or "economy"
    program = str(trip.get("Source") or record.get("Source", ""))
    emoji = CABIN_EMOJI.get(cabin, "✈️")
    title = f"{emoji} {origin} → {dest} {cabin.replace('_', ' ').title()} | {program}"
    body = _format_body(trip, record)
    url = _deep_link(origin, dest, cabin, program)

    if notifier == "telegram":
        _send_telegram(title, body, url)
    else:
        _send_pushover(title, body, url)


def _send_pushover(title: str, message: str, url: str) -> None:
    token = os.environ.get("PUSHOVER_TOKEN", "")
    user = os.environ.get("PUSHOVER_USER", "")
    if not token or not user:
        raise RuntimeError("PUSHOVER_TOKEN and PUSHOVER_USER must be set for Pushover")
    url_title = "View on seats.aero"
    title, message, url, url_title = _pushover_clip(title, message, url, url_title)
    if not (message or "").strip():
        message = "Award availability matched your alert."
    data = {
        "token": token,
        "user": user,
        "title": title,
        "message": message,
        "url": url,
        "url_title": url_title,
        "priority": 1,
    }
    r = requests.post(PUSHOVER_URL, data=data, timeout=30)
    if not r.ok:
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        log.error("Pushover HTTP %s: %s", r.status_code, detail)
        raise RuntimeError(f"Pushover HTTP {r.status_code}: {detail}") from None
    j = r.json()
    if j.get("status") != 1:
        raise RuntimeError(f"Pushover error: {j}")


def _send_telegram(title: str, message: str, url: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set for Telegram")
    text = f"<b>{_tg_escape(title)}</b>\n\n{_tg_escape(message)}\n\n<a href=\"{_tg_escape(url)}\">View on seats.aero</a>"
    u = TELEGRAM_URL.format(token=token)
    r = requests.post(
        u,
        json={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    if not j.get("ok"):
        raise RuntimeError(f"Telegram error: {j}")


def _tg_escape(s: str) -> str:
    for a, b in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")):
        s = s.replace(a, b)
    return s
