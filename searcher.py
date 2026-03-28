"""seats.aero Partner API search with pagination and per-ID deduplication."""

from __future__ import annotations

from typing import Any

import requests

BASE_URL = "https://seats.aero/partnerapi/search"


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
) -> list[dict[str, Any]]:
    """
    Paginate until hasMore is false.
    First page: no skip/cursor. Later pages: skip += len(data), cursor from first response only.
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

    while True:
        params = dict(params_base)
        if not first_page:
            params["skip"] = skip
            if cursor is not None:
                params["cursor"] = cursor

        resp = session.get(BASE_URL, params=params, headers=headers, timeout=timeout)
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

        for item in batch:
            if isinstance(item, dict) and item.get("ID") is not None:
                by_id[str(item["ID"])] = item

        skip += len(batch)

        if not body.get("hasMore"):
            break

    return list(by_id.values())
