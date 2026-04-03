"""Load program metadata (transfer partners, alliances) from data/program_metadata.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).resolve().parent / "data" / "program_metadata.json"

BANK_LABELS: dict[str, str] = {
    "amex": "American Express",
    "capital_one": "Capital One",
    "chase": "Chase",
    "citi": "Citi",
}

ALLIANCE_LABELS: dict[str, str] = {
    "star_alliance": "Star Alliance",
    "skyteam": "SkyTeam",
    "oneworld": "oneworld",
    "independent": "Non-alliance",
}


@lru_cache
def _entries_tuple() -> tuple[dict[str, Any], ...]:
    if not _DATA_PATH.exists():
        return ()
    try:
        raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ()
    if not isinstance(raw, list):
        return ()
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict) and item.get("source"):
            out.append(dict(item))
    return tuple(out)


def all_entries() -> list[dict[str, Any]]:
    return list(_entries_tuple())


def by_source() -> dict[str, dict[str, Any]]:
    return {str(e["source"]): e for e in _entries_tuple()}


def all_transfer_bank_keys() -> list[str]:
    keys: set[str] = set()
    for e in _entries_tuple():
        tr = e.get("transfer_ratio")
        if isinstance(tr, dict):
            keys.update(str(k) for k in tr.keys())
    return sorted(keys, key=lambda b: (BANK_LABELS.get(b, b).lower(), b))


def _entry_matches_alliance(entry: dict[str, Any], alliance_key: str) -> bool:
    if alliance_key == "independent":
        return entry.get("alliance") is None
    a = entry.get("alliance")
    if a == alliance_key:
        return True
    book = entry.get("bookable_partner_alliances") or []
    if isinstance(book, list):
        return alliance_key in book
    return False


def sources_for_bank(bank_key: str, supported: frozenset[str]) -> list[str]:
    found: list[str] = []
    for e in _entries_tuple():
        src = str(e.get("source", ""))
        if src not in supported:
            continue
        tr = e.get("transfer_ratio")
        if isinstance(tr, dict) and bank_key in tr:
            found.append(src)
    return sorted(found)


def sources_for_alliance(alliance_key: str, supported: frozenset[str]) -> list[str]:
    found: list[str] = []
    for e in _entries_tuple():
        src = str(e.get("source", ""))
        if src not in supported:
            continue
        if _entry_matches_alliance(e, alliance_key):
            found.append(src)
    return sorted(found)


def supported_program_rows(supported_programs: list[str]) -> list[dict[str, Any]]:
    """Per-source rows for the alert form (JS quick-pick + labels)."""
    sup = frozenset(supported_programs)
    by = by_source()
    rows: list[dict[str, Any]] = []
    for src in sorted(sup):
        e = by.get(src)
        if e:
            tr = e.get("transfer_ratio") if isinstance(e.get("transfer_ratio"), dict) else {}
            rows.append(
                {
                    "source": src,
                    "mileage_program": str(e.get("mileage_program") or src),
                    "airline_code": str(e.get("airline_code") or ""),
                    "banks": sorted(tr.keys()),
                    "alliance": e.get("alliance"),
                    "bookable": list(e.get("bookable_partner_alliances") or [])
                    if isinstance(e.get("bookable_partner_alliances"), list)
                    else [],
                    "in_metadata": True,
                }
            )
        else:
            rows.append(
                {
                    "source": src,
                    "mileage_program": src,
                    "airline_code": "",
                    "banks": [],
                    "alliance": None,
                    "bookable": [],
                    "in_metadata": False,
                }
            )
    return rows


def display_labels(supported_programs: list[str]) -> dict[str, str]:
    by = by_source()
    return {p: str(by[p].get("mileage_program") or p) if p in by else p for p in supported_programs}


def format_transfer_cell(tr: Any) -> str:
    if not isinstance(tr, dict) or not tr:
        return "—"
    parts = []
    for k in sorted(tr.keys(), key=lambda x: (BANK_LABELS.get(x, x).lower(), x)):
        label = BANK_LABELS.get(k, k.replace("_", " ").title())
        parts.append(f"{label}: {tr[k]}")
    return " · ".join(parts)


def alliance_display(entry: dict[str, Any]) -> str:
    a = entry.get("alliance")
    if a is None:
        return "—"
    return ALLIANCE_LABELS.get(str(a), str(a).replace("_", " ").title())
