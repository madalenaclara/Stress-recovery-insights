// Supabase Edge Function: ingest
//
// Receives a Health Auto Export JSON payload, normalises it into per-day rows,
// and merge-upserts them into the daily_metrics table via the upsert_daily()
// SQL function. Protect it with a secret: set INGEST_API_KEY and send it as an
// `X-API-Key` header (or `?key=` query param).
//
// Deploy with JWT verification OFF so Health Auto Export only needs the one
// header:  supabase functions deploy ingest --no-verify-jwt

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const API_KEY = Deno.env.get("INGEST_API_KEY") ?? "";

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

// --- Health Auto Export metric name -> our column --------------------------
const METRIC_ALIASES: Record<string, string> = {
  heart_rate_variability: "hrv",
  heart_rate_variability_sdnn: "hrv",
  resting_heart_rate: "resting_hr",
  respiratory_rate: "respiratory_rate",
  apple_sleeping_wrist_temperature: "wrist_temperature",
  wrist_temperature: "wrist_temperature",
  blood_oxygen_saturation: "spo2",
  oxygen_saturation: "spo2",
  active_energy: "active_energy",
  active_energy_burned: "active_energy",
};

function parseDate(raw: string): string | null {
  raw = (raw ?? "").trim();
  // Health Auto Export uses "YYYY-MM-DD HH:MM:SS +0000"; take the leading date.
  if (raw.length >= 10 && raw[4] === "-" && raw[7] === "-") return raw.slice(0, 10);
  const d = new Date(raw);
  return isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
}

function sampleQty(s: Record<string, unknown>): number | null {
  for (const k of ["qty", "Avg", "avg", "value"]) {
    const v = s[k];
    if (v !== undefined && v !== null && !isNaN(Number(v))) return Number(v);
  }
  return null;
}

function sleepHours(s: Record<string, unknown>): number | null {
  for (const k of ["asleep", "totalSleep", "total_sleep", "sleepDuration"]) {
    const v = s[k];
    if (v !== undefined && v !== null && !isNaN(Number(v))) return Number(v);
  }
  const stages = ["core", "deep", "rem"]
    .map((k) => Number(s[k]))
    .filter((x) => !isNaN(x));
  if (stages.length) return stages.reduce((a, b) => a + b, 0);
  const inBed = Number(s["inBed"]);
  const awake = Number(s["awake"] ?? 0) || 0;
  return isNaN(inBed) ? null : Math.max(0, inBed - awake);
}

type Bucket = Record<string, number[]>;

function parsePayload(payload: any): Record<string, unknown>[] {
  const data = payload?.data ?? payload ?? {};
  const metrics: any[] = data.metrics ?? [];
  const buckets: Record<string, Bucket> = {};
  const push = (date: string, field: string, val: number) => {
    (buckets[date] ??= {});
    (buckets[date][field] ??= []).push(val);
  };

  for (const metric of metrics) {
    const name = String(metric?.name ?? "").trim().toLowerCase();
    const samples: any[] = metric?.data ?? [];

    if (name === "sleep_analysis" || name === "sleep") {
      for (const s of samples) {
        const date = parseDate(s.sleepEnd ?? s.date ?? "");
        const hrs = sleepHours(s);
        if (date && hrs !== null) push(date, "sleep_hours", hrs);
      }
      continue;
    }
    if (name === "heart_rate" || name === "heart_rate_average") {
      for (const s of samples) {
        const date = parseDate(s.date ?? "");
        const q = sampleQty(s);
        if (date && q !== null) push(date, "avg_daytime_hr", q);
      }
      continue;
    }

    const field = METRIC_ALIASES[name];
    if (!field) continue;
    for (const s of samples) {
      const date = parseDate(s.date ?? "");
      const q = sampleQty(s);
      if (date && q !== null) push(date, field, q);
    }
  }

  const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
  const rows: Record<string, unknown>[] = [];
  for (const date of Object.keys(buckets).sort()) {
    const b = buckets[date];
    const avg = (f: string) => (b[f]?.length ? mean(b[f]) : null);
    rows.push({
      date,
      hrv: avg("hrv"),
      resting_hr: avg("resting_hr"),
      // sleep: take the longest sleep block reported for the day
      sleep_hours: b["sleep_hours"]?.length ? Math.max(...b["sleep_hours"]) : null,
      respiratory_rate: avg("respiratory_rate"),
      wrist_temperature: avg("wrist_temperature"),
      spo2: avg("spo2"),
      avg_daytime_hr: avg("avg_daytime_hr"),
      active_energy: b["active_energy"]?.length
        ? b["active_energy"].reduce((a, c) => a + c, 0)
        : null,
    });
  }
  return rows;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

Deno.serve(async (req) => {
  if (req.method !== "POST") return json({ error: "POST only" }, 405);

  if (API_KEY) {
    const supplied =
      req.headers.get("x-api-key") ??
      new URL(req.url).searchParams.get("key") ??
      "";
    if (supplied !== API_KEY) return json({ error: "Unauthorized" }, 401);
  }

  let payload: unknown;
  try {
    payload = await req.json();
  } catch {
    return json({ error: "Body must be valid JSON." }, 400);
  }

  const rows = parsePayload(payload);
  if (!rows.length) return json({ ingested_days: 0, dates: [] });

  const { error } = await supabase.rpc("upsert_daily", { rows });
  if (error) return json({ error: error.message }, 500);

  return json({ ingested_days: rows.length, dates: rows.map((r) => r.date) });
});
