# Seats Alert

Python service that polls the [seats.aero](https://seats.aero) Partner API on schedules you define, matches award availability against per-alert filters, and notifies you via **Pushover** or **Telegram** when a trip qualifies. A small **Flask** dashboard edits `alerts.yaml` and shows last run state.

## Setup

```bash
cd /path/to/reward-seats-alert
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your `SEATS_API_KEY`, notifier credentials, and `FLASK_SECRET`.

### Where notifications go (Pushover / Telegram)

- **`NOTIFIER=pushover` (default):** This app POSTs to [Pushover’s Messages API](https://pushover.net/api#messages) at `https://api.pushover.net/1/messages.json`. Pushover’s servers deliver the notification to **your account**, identified by **`PUSHOVER_USER`** (your user key), using the app defined by **`PUSHOVER_TOKEN`** (your application API token). Whatever devices you’ve registered in the Pushover app (phone, desktop, tablet) receive it according to your Pushover settings. This project does not talk to APNs/FCM directly—Pushover does.
- **`NOTIFIER=telegram`:** Messages go to the Telegram chat **`TELEGRAM_CHAT_ID`** via the Bot API.

Per alert, **`push_notifications`** in `alerts.yaml` (checkbox **Send push notifications** on the form) controls whether a matching trip actually triggers Pushover/Telegram. If it’s off, the alert still runs and **Last run** debug still shows matches, but nothing is sent and those trips are **not** added to `seen_results.json`, so you can turn pushes back on later and still get alerts.

## Run

```bash
cd /path/to/reward-seats-alert
source .venv/bin/activate   # optional; use your venv if you created one
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) (or the port set in `FLASK_PORT`). The monitor thread starts with the app: on first launch it runs **all enabled** alerts once, then repeats each alert according to its `interval_minutes`, waking every 15 seconds to see what is due. `alerts.yaml` is re-read on every loop; the UI calls `reload_config()` after changes so you do not have to wait for the next sleep.

### Stop and restart

- **Stop:** In the terminal where the app is running, press **Ctrl+C**.
- **Start again:** Run `python app.py` from the project directory (with your venv activated if you use one).

### When you need a restart

- **Restart** after changing **Python code** (any `.py` file), **templates**, or **`.env`** — the app runs with the reloader off, so edits are not picked up automatically.
- **No restart** for edits to **`alerts.yaml`** only (the monitor reloads it on each loop, and the UI wakes the monitor after saves).

**Manual check:** On the dashboard use **Check now** (one alert) or **Check all enabled**. From the shell, run `python manual_check.py` (all enabled, synchronous) or `python manual_check.py --name "Exact alert name"` (one alert, even if paused).

**Debugging a check:** Each run logs every seats.aero **request URL** (no API key in the URL) and **response summaries** at **INFO** in the console. After a run, expand **Last run — API request & response summary** on an alert card to see the same payload: pages fetched, `filter_stats` (how many rows passed the summary pre-check vs trip-level filters), samples of API records, and up to 12 trips that matched filters (if any). If `trips_passing_all_filters` is 0 but `records_in_response` is high, tighten or loosen filters; if `skipped_already_notified` is high, matches were already in `seen_results.json`.

## Files

| File | Role |
|------|------|
| `alerts.yaml` | Alert definitions (safe to commit) |
| `seen_results.json` | Deduplication keys `alert_name::trip_id` (gitignored) |
| `alert_state.json` | Per-alert `last_checked`, `next_run`, `last_triggered`, `total_matches`, `error` (gitignored) |
| `searcher.py` | Partner search with pagination and ID deduplication |
| `airport_times.py` | IATA → IANA zone (`airportsdata`); local display and depart window |
| `filters.py` | Summary pre-checks and trip-level rules |
| `notifier.py` | Pushover / Telegram |
| `monitor.py` | Background loop and per-alert workers |
| `state.py` | JSON persistence helpers |
| `app.py` | Flask UI |

## API notes

- Auth: `Partner-Authorization: <api_key>`.
- Pagination: first page without `skip`/`cursor`; later pages send `skip` (total records so far) and the `cursor` from the **first** response. Records are merged by `ID` across pages.
- `DepartsAt` / `ArrivesAt` use a `Z` suffix but seats.aero treats the clock as **local at the airport**. This app maps **IATA → IANA timezone** with the **`airportsdata`** package (worldwide coverage, including multi-airport city codes like `NYC`), attaches the correct `zoneinfo` zone for display, and applies **`depart_after` / `depart_before` against the departure airport’s local wall clock**. Unknown IATA codes fall back to comparing the raw API clock without a zone. **`tzdata`** is listed for Windows; macOS/Linux usually use the OS zone database.
- Summary rows expose `{Y,W,J,F}MileageCostRaw` (miles for that cabin on that route/date/program aggregate). Each `AvailabilityTrips` entry has its own `MileageCost` for that specific itinerary. **`max_points` is enforced only on trip `MileageCost`**, so you never miss a cheaper itinerary that differs from the summary figure.

## Known tradeoffs

- **Concurrent alerts:** Each due alert runs in its own thread; alert state updates are serialized with a small lock so `alert_state.json` stays consistent.
