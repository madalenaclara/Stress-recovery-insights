# Deploying so your iPhone can reach it

Health Auto Export (Premium) POSTs your data to a URL over the internet, so the
server needs to be reachable from your phone. Two paths — start with A to test,
move to B for daily use.

## First: turn on the security token

`/ingest` accepts anonymous data unless you set a secret. Always set one before
exposing the server publicly:

```bash
export SRI_API_KEY="pick-a-long-random-string"
```

Then Health Auto Export must send it as a header `X-API-Key: <that string>`
(or `?key=<that string>` on the URL). Requests without it get 401.

---

## Path A — Quick test from your couch (free, 2 minutes)

Run the app locally and expose it with a tunnel. Nothing to deploy.

```bash
pip install -r requirements.txt
export SRI_API_KEY="pick-a-long-random-string"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In another terminal, open a tunnel (either tool works):

```bash
# Cloudflare (no signup):
cloudflared tunnel --url http://localhost:8000
# …or ngrok:
ngrok http 8000
```

Both print a public `https://…` URL. Use it as your ingest endpoint:
`https://<tunnel-host>/ingest`. Note: the tunnel URL changes each run and only
works while your machine is on — fine for testing, not for daily sync.

---

## Path B — Always-on host (recommended for daily use)

Any container host works. The app already reads `$PORT` and persists SQLite to
`/data`, so all you need is a mounted volume and two env vars.

**Render / Railway / Fly.io** (pick one):

1. Push this repo to GitHub (already done).
2. Create a new **Web Service** from the repo — it auto-detects the `Dockerfile`.
3. Add a **persistent disk / volume** mounted at `/data` (so history survives
   restarts). Even 1 GB is plenty.
4. Set environment variables:
   - `SRI_API_KEY` = your long random string
   - (optional) `SRI_BASELINE_WINDOW` = `21`
5. Deploy. You get a stable `https://<your-app>.onrender.com` URL.

Your ingest endpoint is then `https://<your-app>.onrender.com/ingest` and the
dashboard is the root URL.

### Run it yourself with Docker

```bash
docker build -t stress-recovery .
docker run -p 8000:8000 \
  -e SRI_API_KEY="pick-a-long-random-string" \
  -v "$PWD/sri-data:/data" \
  stress-recovery
```

---

## Configuring the Health Auto Export automation

1. Open **Health Auto Export** → **Automations** → **Add Automation**.
2. **Automation type:** REST API.
3. **URL:** your `…/ingest` endpoint (from Path A or B).
4. **Method:** POST.
5. **Headers:** add `X-API-Key` = your secret string. Content-Type is
   `application/json`.
6. **Data format:** JSON. Aggregate daily (or hourly if you want intraday HR).
7. **Metrics:** heart rate variability, resting heart rate, respiratory rate,
   sleeping wrist temperature, blood oxygen, heart rate, active energy, sleep
   analysis.
8. **Schedule:** daily (e.g. every morning) — or let background sync push
   automatically.
9. Save. Trigger it once manually, then open the dashboard: new days appear.

Re-sending overlapping days is safe — ingestion merges by date.
