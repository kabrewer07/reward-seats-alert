"""seats.aero Partner API search with pagination and per-ID deduplication."""

from __future__ import annotations

import logging
from typing import Any

import requests

BASE_URL = "https://seats.aero/partnerapi/search"

log = logging.getLogger(__name__)


def _summarize_record(rec: dict[str, Any]) -> dict[str, Any]:
    route = rec.get("Route") or {}
    trips = rec.get("AvailabilityTrips") or []
    trip_n = len(trips) if isinstance(trips, list) else 0
    return {
        "ID": rec.get("ID"),
        "Date": rec.get("Date"),
        "Source": rec.get("Source"),
        "Origin": route.get("OriginAirport"),
        "Destination": route.get("DestinationAirport"),
        "YAvailable": rec.get("YAvailable"),
        "WAvailable": rec.get("WAvailable"),
        "JAvailable": rec.get("JAvailable"),
        "FAvailable": rec.get("FAvailable"),
        "availability_trips_count": trip_n,
    }


def search_flights(
    api_key: str,
    *,
    origins: list[str],
    destinations: list[str],
    start_date: str,
    end_date: str,
    programs: list[str],
    cabin: str,
    direct_only: bool,
    take: int = 500,
    timeout: int = 120,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Paginate until hasMore is false.
    First page: no skip/cursor. Later pages: skip += len(data), cursor from first response only.

    Returns (records, debug_info). debug_info is JSON-serializable (no secrets — API key is header-only).
    """
    params_base: dict[str, Any] = {
        "origin_airport": ",".join(origins),
        "destination_airport": ",".join(destinations),
        "start_date": start_date,
        "end_date": end_date,
        "sources": ",".join(programs),
        "cabins": cabin,
        "only_direct_flights": str(bool(direct_only)).lower(),
        "include_trips": "true",
        "include_filtered": "false",
        "take": min(max(take, 1), 1000),
    }
    headers = {"Partner-Authorization": api_key}

    by_id: dict[str, dict[str, Any]] = {}
    skip = 0
    cursor: int | None = None
    first_page = True

    session = requests.Session()
    pages: list[dict[str, Any]] = []

    while True:
        params = dict(params_base)
        if not first_page:
            params["skip"] = skip
            if cursor is not None:
                params["cursor"] = cursor

        req = requests.Request("GET", BASE_URL, params=params, headers=headers)
        prepared = session.prepare_request(req)
        url_logged = prepared.url
        log.info("seats.aero request GET %s", url_logged)

        resp = session.send(prepared, timeout=timeout)
        log.info(
            "seats.aero response status=%s url=%s",
            resp.status_code,
            url_logged,
        )
        resp.raise_for_status()
        body = resp.json()

        if first_page:
            c = body.get("cursor")
            if c is not None:
                cursor = int(c)
            first_page = False

        batch = body.get("data") or []
        if not isinstance(batch, list):
            batch = []

        page_info = {
            "request_url": url_logged,
            "http_status": resp.status_code,
            "body_count": body.get("count"),
            "hasMore": body.get("hasMore"),
            "cursor_in_response": body.get("cursor"),
            "batch_rows": len(batch),
        }
        pages.append(page_info)
        log.info(
            "seats.aero page batch_rows=%s hasMore=%s count=%s cursor=%s",
            len(batch),
            body.get("hasMore"),
            body.get("count"),
            body.get("cursor"),
        )

        for item in batch:
            if isinstance(item, dict) and item.get("ID") is not None:
                by_id[str(item["ID"])] = item

        skip += len(batch)

        if not body.get("hasMore"):
            break

    records = list(by_id.values())
    keys = list(by_id.keys())
    samples = [_summarize_record(by_id[k]) for k in keys[:8]]

    debug: dict[str, Any] = {
        "api_base": BASE_URL,
        "query_params_template": {k: v for k, v in params_base.items()},
        "pages_fetched": len(pages),
        "pages": pages,
        "unique_records_after_merge": len(records),
        "record_samples": samples,
    }
    log.info(
        "seats.aero search done: pages=%s unique_records=%s",
        len(pages),
        len(records),
    )
    return records, debug
