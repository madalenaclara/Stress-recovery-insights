// Supabase Edge Function: ingest
//
// Receives a Health Auto Export JSON payload, normalises it into per-day rows,
// and merge-upserts them into the daily_metrics table via the upsert_daily()
// SQL function. Auth is handled by Supabase's gateway (the project's anon key
// must be sent as an `apikey` header), so no extra app-level key is needed.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

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

// ---- workouts ----------------------------------------------------------
// Reads a number whether Health Auto Export sends a scalar or a {qty, units}.
function numOf(v: any): number | null {
  if (v == null) return null;
  if (typeof v === "number") return v;
  if (typeof v === "object" && v.qty != null && !isNaN(Number(v.qty))) return Number(v.qty);
  return isNaN(Number(v)) ? null : Number(v);
}

function parseTs(s: string): number | null {
  if (!s) return null;
  const m = String(s).trim().match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\s*([+-]\d{2}):?(\d{2})?/);
  const iso = m ? `${m[1]}T${m[2]}${m[3]}:${m[4] ?? "00"}` : s;
  const t = Date.parse(iso);
  return isNaN(t) ? null : t;
}

function parseWorkouts(payload: any): Record<string, unknown>[] {
  const data = payload?.data ?? payload ?? {};
  const list: any[] = data.workouts ?? [];
  const rows: Record<string, unknown>[] = [];
  for (const w of list) {
    const start = w.start ?? w.startDate ?? "";
    const id = String(start || w.id || "").trim();
    const date = parseDate(start);
    if (!id || !date) continue;
    const end = w.end ?? w.endDate ?? "";
    const ts0 = parseTs(start), ts1 = parseTs(end);
    let durMin: number | null = ts0 != null && ts1 != null ? (ts1 - ts0) / 60000 : null;
    if (durMin == null) {
      const d = numOf(w.duration);
      if (d != null) durMin = d > 600 ? d / 60 : d; // seconds vs minutes heuristic
    }
    rows.push({
      id,
      date,
      type: w.name ?? w.type ?? w.workoutActivityType ?? "Workout",
      start_at: start || null,
      end_at: end || null,
      duration_min: durMin != null ? Math.round(durMin * 10) / 10 : null,
      active_energy: numOf(w.activeEnergyBurned ?? w.activeEnergy ?? w.totalEnergy),
      avg_hr: numOf(w.avgHeartRate ?? w.averageHeartRate ?? w.heartRateAverage),
      max_hr: numOf(w.maxHeartRate ?? w.heartRateMax),
      distance: numOf(w.distance ?? w.totalDistance),
      source: w.source ?? "Apple Watch",
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

  let payload: unknown;
  try {
    payload = await req.json();
  } catch {
    return json({ error: "Body must be valid JSON." }, 400);
  }

  const rows = parsePayload(payload);
  const workouts = parseWorkouts(payload);

  if (rows.length) {
    const { error } = await supabase.rpc("upsert_daily", { rows });
    if (error) return json({ error: error.message }, 500);
  }
  if (workouts.length) {
    const { error } = await supabase.rpc("upsert_workouts", { rows: workouts });
    if (error) return json({ error: `workouts: ${error.message}` }, 500);
  }

  return json({
    ingested_days: rows.length,
    dates: rows.map((r) => r.date),
    ingested_workouts: workouts.length,
  });
});
