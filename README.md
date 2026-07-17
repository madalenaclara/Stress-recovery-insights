# Stress & Recovery Insights

Replicate the two insights you actually care about from Whoop — **Recovery** and
**Stress** — using only the data your **Apple Watch** already collects, sent to an
app you own. No $99/yr Apple Developer account required.

## How it works

```
Apple Watch ──auto──► iPhone Health app (HealthKit)
                          │
              Health Auto Export app  (iOS, ~$5, no dev account)
                          │  POST JSON on a schedule
                          ▼
              This backend  ── /ingest ──►  SQLite
                          │
                          ├─ baseline-relative scoring (scoring.py)
                          └─ /  dashboard (Recovery %, Stress 0–3, trends)
```

Everything Whoop needs is measured by Apple Watch and exposed via HealthKit: HRV
(SDNN), resting heart rate, sleep, respiratory rate, wrist temperature and SpO₂.
The scores here are **baseline-relative** — each metric is compared against *your
own* rolling 21-day average, exactly like Whoop — so absolute differences from
Whoop's numbers don't matter; the trends track.

### The scores

- **Recovery (0–100%)** — a daily readiness number. Weighted blend of z-scores:
  HRV (40%, higher = better), resting HR (20%, lower = better), sleep (20%),
  respiratory rate (10%, deviation is bad), wrist temperature (10%, deviation is
  bad). Squashed to 0–100 with a logistic curve. Green ≥ 67, yellow ≥ 34, red below.
- **Stress (0.0–3.0)** — how far current physiology sits above your calm baseline:
  HRV suppression + daytime heart rate elevated over resting. Damped on
  high-movement days so a workout isn't mistaken for stress (Whoop does the same
  with motion).

## Quick start

```bash
pip install -r requirements.txt

# Load a realistic demo month so you can see it working immediately:
python scripts/seed_sample.py

# Run it:
uvicorn app.main:app --reload
# open http://127.0.0.1:8000
```

Run the tests:

```bash
python -m pytest -q          # or:  python tests/test_scoring.py
```

## Connecting your real Apple Watch data

1. Install **Health Auto Export – JSON+CSV** on your iPhone.
2. Create an **Automation** → destination **REST API**.
3. Point it at your running server: `POST http://<your-host>:8000/ingest`.
4. Select the metrics: heart rate variability, resting heart rate, respiratory
   rate, sleeping wrist temperature, blood oxygen, heart rate, active energy,
   sleep analysis.
5. Set it to run daily (e.g. each morning). Done — data flows in with no manual step.

Re-sending overlapping days is safe: ingestion merges by date and prefers the
newest non-null values.

To reach the server from your phone (tunnel for testing, or an always-on host)
and to secure `/ingest` with a token, see **[DEPLOY.md](DEPLOY.md)**.

## API

| Method | Path            | Purpose                                            |
|--------|-----------------|----------------------------------------------------|
| POST   | `/ingest`       | Receive a Health Auto Export JSON payload          |
| GET    | `/api/insights` | Scored Recovery + Stress history and latest snapshot |
| GET    | `/api/raw`      | Stored raw daily metrics                            |
| GET    | `/health`       | Liveness probe                                      |
| GET    | `/`             | Dashboard                                           |

## Project layout

```
app/
  scoring.py   # baseline-relative Recovery & Stress math (pure, tested)
  ingest.py    # Health Auto Export JSON -> normalised daily metrics
  db.py        # SQLite persistence (one row per day, idempotent upsert)
  main.py      # FastAPI endpoints + dashboard
static/
  index.html   # self-contained dashboard (Chart.js)
scripts/
  seed_sample.py  # generate + load a demo month
tests/
  test_scoring.py
```

## Notes & limitations

- Apple exposes HRV as **SDNN**; Whoop uses **RMSSD/lnRMSSD**. Different metric,
  both valid — we baseline against whatever Apple provides, so it's self-consistent.
- Apple samples HRV less densely than Whoop, so expect smoother/less granular HRV.
- The weights in `scoring.py` are sensible defaults, not medical truth. Tune them
  to what correlates with how *you* feel.
- Not a medical device. For insight and self-experimentation only.
