"""Persistence for seen trip keys and per-alert monitor metadata."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

SEEN_RESULTS_PATH = Path(__file__).resolve().parent / "seen_results.json"
ALERT_STATE_PATH = Path(__file__).resolve().parent / "alert_state.json"

_lock = threading.Lock()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


def load_seen() -> set[str]:
    with _lock:
        raw = _read_json(SEEN_RESULTS_PATH, [])
    if isinstance(raw, list):
        return {str(x) for x in raw}
    return set()


def save_seen(seen: set[str]) -> None:
    with _lock:
        _write_json(SEEN_RESULTS_PATH, sorted(seen))


def add_seen(key: str, seen: set[str]) -> None:
    with _lock:
        data = _read_json(SEEN_RESULTS_PATH, [])
        if not isinstance(data, list):
            data = []
        if key not in data:
            data.append(key)
            _write_json(SEEN_RESULTS_PATH, data)
        seen.add(key)


def load_alert_state() -> dict[str, dict[str, Any]]:
    with _lock:
        raw = _read_json(ALERT_STATE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[str(k)] = dict(v)
    return out


def save_alert_state(state: dict[str, dict[str, Any]]) -> None:
    with _lock:
        _write_json(ALERT_STATE_PATH, state)


def update_alert_state_entry(name: str, updates: dict[str, Any]) -> None:
    with _lock:
        state = load_alert_state_unlocked()
        cur = dict(state.get(name, {}))
        cur.update(updates)
        state[name] = cur
        _write_json(ALERT_STATE_PATH, state)


def load_alert_state_unlocked() -> dict[str, dict[str, Any]]:
    raw = _read_json(ALERT_STATE_PATH, {})
    if not isinstance(raw, dict):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def iso_now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def remove_alert_from_state(name: str) -> None:
    with _lock:
        state = load_alert_state_unlocked()
        state.pop(name, None)
        _write_json(ALERT_STATE_PATH, state)


def prune_seen_for_alert(alert_name: str, seen: set[str]) -> None:
    prefix = f"{alert_name}::"
    with _lock:
        data = _read_json(SEEN_RESULTS_PATH, [])
        if not isinstance(data, list):
            data = []
        data = [x for x in data if not str(x).startswith(prefix)]
        _write_json(SEEN_RESULTS_PATH, data)
    seen.difference_update({k for k in seen if k.startswith(prefix)})
