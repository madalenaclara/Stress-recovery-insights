# Deploy on Supabase (free, always-on, persistent)

This runs the whole thing on Supabase's free tier: an `ingest` Edge Function
receives your Apple Watch data, Postgres stores it, an `insights` Edge Function
computes the scores, and a static dashboard reads them. No server to babysit, no
recurring cost, and your history is never wiped.

```
Apple Watch → iPhone Health → Health Auto Export (Premium)
                                     │  POST JSON + X-API-Key header
                                     ▼
                    Supabase Edge Function  ingest  ──►  Postgres (daily_metrics)
                                                              │
                    Supabase Edge Function  insights ◄───────┘  (scores it)
                                     ▲
                          docs/index.html dashboard  (local or GitHub Pages)
```

---

## Step 1 — Create the project (5 min)

1. Go to <https://supabase.com>, sign up (free), and **New project**.
2. Pick a name and a strong database password (you won't need it again here).
3. Wait for it to finish provisioning.

## Step 2 — Create the database

1. In the project, open **SQL Editor** → **New query**.
2. Paste the entire contents of [`supabase/migrations/0001_init.sql`](supabase/migrations/0001_init.sql).
3. Click **Run**. This creates the `daily_metrics` table and the `upsert_daily`
   merge function, and locks the table with Row Level Security.

## Step 3 — Pick a secret key

Generate a long random string — this protects your endpoints. For example:

```bash
openssl rand -hex 24
```

Keep it handy; you'll paste it in two places (function secret + the dashboard).

## Step 4 — Deploy the two functions

### Option A — Supabase CLI (recommended)

```bash
# install once: https://supabase.com/docs/guides/cli
supabase login
supabase link --project-ref YOUR_PROJECT_REF      # ref is in your project URL

# store the secret (SUPABASE_URL / SERVICE_ROLE_KEY are injected automatically)
supabase secrets set INGEST_API_KEY="paste-your-random-string"

# deploy both, with JWT verification off so Health Auto Export needs only X-API-Key
supabase functions deploy ingest   --no-verify-jwt
supabase functions deploy insights --no-verify-jwt
```

### Option B — Supabase dashboard (no CLI)

1. **Edge Functions** → **Create a function** → name it `ingest` → paste
   [`supabase/functions/ingest/index.ts`](supabase/functions/ingest/index.ts) → **Deploy**.
2. Repeat for `insights` using
   [`supabase/functions/insights/index.ts`](supabase/functions/insights/index.ts).
3. For **each** function, open its settings and turn **Enforce JWT verification
   OFF** (so only your `X-API-Key` is needed).
4. **Edge Functions → Secrets** → add `INGEST_API_KEY` = your random string.

## Step 5 — Note your endpoints

Your functions base URL is:

```
https://YOUR_PROJECT_REF.supabase.co/functions/v1
```

So ingest is `…/functions/v1/ingest` and insights is `…/functions/v1/insights`.

Quick smoke test from your computer:

```bash
curl -X POST "https://YOUR_REF.supabase.co/functions/v1/ingest" \
  -H "X-API-Key: your-random-string" -H "Content-Type: application/json" \
  --data @sample_data/health_auto_export_sample.json
# -> {"ingested_days":30,...}
```

## Step 6 — Point Health Auto Export at it

In **Health Auto Export → Automations → Add Automation**:

- **Type:** REST API
- **URL:** `https://YOUR_REF.supabase.co/functions/v1/ingest`
- **Method:** POST · **Format:** JSON · aggregate **daily** (or hourly for intraday HR)
- **Headers:** `X-API-Key` = your random string
- **Metrics:** heart rate variability, resting heart rate, respiratory rate,
  sleeping wrist temperature, blood oxygen, heart rate, active energy, sleep analysis
- **Schedule:** daily (e.g. each morning)

Save and **run it once** manually. It should return success.

## Step 7 — Open the dashboard

The dashboard is [`docs/index.html`](docs/index.html). Two ways to use it:

- **Locally (most private):** download that one file and open it in your browser.
- **GitHub Pages (shareable link):** repo **Settings → Pages → Source: Deploy
  from branch → `main` / `/docs`**. GitHub gives you a public URL.

Either way, click **⚙︎ Settings** in the dashboard and enter:

- **Functions base URL:** `https://YOUR_REF.supabase.co/functions/v1`
- **API key:** your random string

Your key is stored only in that browser (localStorage) — it's never committed to
the repo, so even a public GitHub Pages dashboard exposes no secret, and the
`insights` function refuses requests without the key.

## Done

After ~5 days of syncs the personal baseline kicks in and the scores become
meaningful. Before that the dashboard shows "baseline forming".

### Notes

- Free Supabase projects pause after ~1 week of inactivity; a daily sync keeps
  yours awake.
- Your health data lives in your Supabase Postgres, readable only via the
  service-role key held server-side by the functions — not by the public anon key.
- Scoring weights live in `supabase/functions/insights/index.ts`
  (`RECOVERY_METRICS`) — tune them to what correlates with how you feel, then
  redeploy `insights`.
