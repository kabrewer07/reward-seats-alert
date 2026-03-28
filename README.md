# Seats Alert

Python service that polls the [seats.aero](https://seats.aero) Partner API on schedules you define, matches award availability against per-alert filters, and notifies you via **Pushover** or **Telegram** when a trip qualifies. A small **Flask** dashboard edits `alerts.yaml` and shows last run state.

## Setup

```bash
cd /path/to/reward-seats-alert
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your `SEATS_API_KEY`, notifier credentials, and `FLASK_SECRET`.

## Run

```bash
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) (or the port set in `FLASK_PORT`). The monitor thread starts with the app: on first launch it runs **all enabled** alerts once, then repeats each alert according to its `interval_minutes`, waking every 15 seconds to see what is due. `alerts.yaml` is re-read on every loop; the UI calls `reload_config()` after changes so you do not have to wait for the next sleep.

## Files

| File | Role |
|------|------|
| `alerts.yaml` | Alert definitions (safe to commit) |
| `seen_results.json` | Deduplication keys `alert_name::trip_id` (gitignored) |
| `alert_state.json` | Per-alert `last_checked`, `next_run`, `last_triggered`, `total_matches`, `error` (gitignored) |
| `searcher.py` | Partner search with pagination and ID deduplication |
| `filters.py` | Summary pre-checks and trip-level rules |
| `notifier.py` | Pushover / Telegram |
| `monitor.py` | Background loop and per-alert workers |
| `state.py` | JSON persistence helpers |
| `app.py` | Flask UI |

## API notes

- Auth: `Partner-Authorization: <api_key>`.
- Pagination: first page without `skip`/`cursor`; later pages send `skip` (total records so far) and the `cursor` from the **first** response. Records are merged by `ID` across pages.
- `DepartsAt` / `ArrivesAt` use a `Z` suffix but are **local airport times**; the code parses them as **naive** datetimes (no timezone math), per seats.aero behavior.
- Summary rows expose `{Y,W,J,F}MileageCostRaw` (miles for that cabin on that route/date/program aggregate). Each `AvailabilityTrips` entry has its own `MileageCost` for that specific itinerary. **`max_points` is enforced only on trip `MileageCost`**, so you never miss a cheaper itinerary that differs from the summary figure.

## Known tradeoffs

- **Concurrent alerts:** Each due alert runs in its own thread; alert state updates are serialized with a small lock so `alert_state.json` stays consistent.
