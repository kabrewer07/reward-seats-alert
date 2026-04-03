# Seats Alert

Polls the [seats.aero](https://seats.aero) Partner API on your schedule, filters results per alert, and sends **Pushover** or **Telegram** when a trip matches. A **Flask** UI edits alerts and shows when each one last ran.

## Get running

```bash
cd /path/to/reward-seats-alert
python3 -m venv .venv && source .venv/bin/activate   # optional
pip install -r requirements.txt
cp .env.example .env
cp alerts.example.yaml alerts.yaml
```

1. Put your Partner API key in **`SEATS_API_KEY`** and set **`FLASK_SECRET`** to something random.
2. Configure Pushover or Telegram (see below).
3. Start the app: **`python app.py`**
4. Open **http://127.0.0.1:5000** (or **`FLASK_HOST`:`FLASK_PORT`** if you changed them).

`alerts.yaml` is gitignored; **`alerts.example.yaml`** is only a template. You can skip the copy and create everything in the UI (first save creates `alerts.yaml`).

**Optional `.env`:** `DASHBOARD_TIMEZONE` (IANA name) controls how “last checked / next run” times are shown on the dashboard; default is US Pacific.

## Day to day

- The monitor starts with the app: it runs **enabled** alerts on an interval, re-reads `alerts.yaml` each loop, and the UI calls **`reload_config()`** after saves so you don’t have to restart for YAML-only edits.
- **Restart** the process after changing **`.env`**, **`.py`**, or **templates**. No restart needed for `alerts.yaml` alone.
- **Check now** / **Check all** on the dashboard run immediately. CLI: **`python manual_check.py`** or **`python manual_check.py --name "Alert name"`** (exact name; works even if the alert is paused).

**Transfer partners** in the nav opens a reference table (ratios, alliances) backed by **`data/program_metadata.json`**. Edit that file if partners change; restart the app afterward (the catalog is cached in memory).

**Logs:** Requests and response summaries go to the console at INFO. Each alert card can expand **Last run** for the same JSON the monitor stored (handy when `trips_passing_all_filters` is 0 or `skipped_already_notified` is high).

## Notifications

- **`NOTIFIER=pushover`** (default): needs **`PUSHOVER_USER`** and **`PUSHOVER_TOKEN`**. Pushover delivers to whatever devices you’ve linked to that account.
- **`NOTIFIER=telegram`**: needs **`TELEGRAM_BOT_TOKEN`** and **`TELEGRAM_CHAT_ID`**.

Per alert, **Send push notifications** (or **`push_notifications`** in YAML) can be off: the run still completes and **Last run** shows matches, but nothing is sent and those trips are not written to **`seen_results.json`**, so you can turn pushes back on later without losing future alerts.

## Alert fields (YAML / form)

| Area | Notes |
|------|--------|
| **programs** | seats.aero source slugs (e.g. `united`, `aeroplan`). The form lists them A–Z by program name; bank/alliance checkboxes use **`data/program_metadata.json`**. |
| **cabins** | List of `economy`, `premium_economy`, `business`, `first` (comma-separated in the API). Legacy single **`cabin`** still loads. |
| **min_seats** | Optional; requires trip **`RemainingSeats`** ≥ N. |
| **max_points**, **direct_only**, **max_duration_hours**, **depart_after** / **depart_before** | Trip-level filters (depart window uses departure airport local time via IATA → timezone). |

Renaming an alert in the UI moves its row in **`alert_state.json`** and rewrites **`seen_results.json`** keys so history stays attached to the new name.

## Layout

| Path | Role |
|------|------|
| `app.py` | Flask app, alert CRUD |
| `monitor.py` | Background polling |
| `searcher.py` | Partner search + pagination |
| `filters.py` | Summary pre-checks + trip matching |
| `notifier.py` | Pushover / Telegram |
| `state.py` | `seen_results.json`, `alert_state.json` |
| `program_catalog.py` | Loads **`data/program_metadata.json`** for the UI |
| `manual_check.py` | One-shot checks from the shell |
| `alerts.yaml` | Your alerts (gitignored) |
| `alerts.example.yaml` | Example / starter |

## API behavior (short)

More detail (endpoints, concepts, limits): [Partner API — Getting Started](https://developers.seats.aero/reference/getting-started-p).

- Auth header **`Partner-Authorization`** with your API key.
- Pagination: first page without `skip`/`cursor`; later pages use **`skip`** and the **`cursor`** from the first response; rows merged by **`ID`**.
- **`cabins`** query param can be comma-separated when an alert selects more than one cabin.
- Times with a **`Z`** suffix are treated as **local at the airport**; this app uses **`airportsdata`** for IATA → IANA zones and applies depart windows in that local time. Unknown airports fall back to raw clock comparison.

**`max_points`** applies to each trip’s **`MileageCost`**, not only the summary row mileage fields.

## Tradeoffs

Concurrent alerts use separate threads; writes to **`alert_state.json`** are serialized with a lock so the file stays consistent.
