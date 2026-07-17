// Supabase Edge Function: insights
//
// Reads all stored days and returns Whoop-style Recovery + Stress scores,
// computed baseline-relative (each metric vs. your own rolling 21-day average).
// Read-only. Protect with the same INGEST_API_KEY (sent as X-API-Key) so your
// health scores aren't world-readable.
//
// Deploy:  supabase functions deploy insights --no-verify-jwt

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const API_KEY = Deno.env.get("INGEST_API_KEY") ?? "";
const BASELINE_WINDOW_DAYS = Number(Deno.env.get("SRI_BASELINE_WINDOW") ?? "21");

const supabase = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

// --- scoring config (mirrors app/scoring.py) -------------------------------
const RECOVERY_METRICS: Record<string, { direction: number; weight: number }> = {
  hrv: { direction: +1, weight: 0.40 },
  resting_hr: { direction: -1, weight: 0.20 },
  sleep_hours: { direction: +1, weight: 0.20 },
  respiratory_rate: { direction: 0, weight: 0.10 },
  wrist_temperature: { direction: 0, weight: 0.10 },
};
const SLEEP_NEED_HOURS = 8.0;
const MIN_BASELINE_DAYS = 5;

type Day = Record<string, number | null> & { date: string };
type Baseline = { mean: number; std: number; n: number };

const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x));
const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
function pstdev(xs: number[]): number {
  const m = mean(xs);
  return Math.sqrt(xs.reduce((a, x) => a + (x - m) ** 2, 0) / xs.length);
}
const z = (b: Baseline, v: number) => (b.std <= 1e-9 ? 0 : (v - b.mean) / b.std);

function buildBaselines(history: Day[]): Record<string, Baseline> {
  const out: Record<string, Baseline> = {};
  const fields = [...Object.keys(RECOVERY_METRICS), "avg_daytime_hr"];
  for (const f of fields) {
    const vals = history.map((d) => d[f]).filter((v): v is number => v != null);
    if (vals.length >= 2) out[f] = { mean: mean(vals), std: pstdev(vals), n: vals.length };
    else if (vals.length === 1) out[f] = { mean: vals[0], std: 0, n: 1 };
  }
  return out;
}

function computeRecovery(day: Day, baselines: Record<string, Baseline>) {
  const contributions: Record<string, unknown> = {};
  let totalWeight = 0, weighted = 0;
  const baselineDays = Math.max(0, ...Object.values(baselines).map((b) => b.n));
  const ready = baselineDays >= MIN_BASELINE_DAYS;

  for (const [name, cfg] of Object.entries(RECOVERY_METRICS)) {
    const value = day[name];
    const base = baselines[name];
    if (value == null || !base) continue;
    const zz = z(base, value);
    let signal = cfg.direction === +1 ? zz : cfg.direction === -1 ? -zz : -Math.abs(zz);
    if (name === "sleep_hours") {
      const ratio = clamp(value / SLEEP_NEED_HOURS, 0, 1);
      signal = 0.5 * signal + 0.5 * (ratio * 2.5 - 1.5);
    }
    signal = clamp(signal, -3, 3);
    weighted += signal * cfg.weight;
    totalWeight += cfg.weight;
    contributions[name] = {
      value: Math.round(value * 100) / 100,
      baseline: Math.round(base.mean * 100) / 100,
      z: Math.round(zz * 100) / 100,
    };
  }

  if (totalWeight === 0) {
    return { score: null, state: "unknown", contributions, note: "No metrics for this day." };
  }
  const avg = weighted / totalWeight;
  let score = 100 / (1 + Math.exp(-1.1 * avg));
  score = Math.round(clamp(score, 1, 99) * 10) / 10;

  if (!ready) {
    return {
      score, state: "unknown", contributions,
      note: `Baseline still forming (${baselineDays}/${MIN_BASELINE_DAYS} days).`,
    };
  }
  const state = score >= 67 ? "green" : score >= 34 ? "yellow" : "red";
  return { score, state, contributions, note: "" };
}

function computeStress(day: Day, baselines: Record<string, Baseline>) {
  const comps: number[] = [];
  const hrvBase = baselines["hrv"];
  if (day.hrv != null && hrvBase && hrvBase.std > 1e-9) {
    comps.push(clamp(-z(hrvBase, day.hrv), 0, 3));
  }
  if (day.avg_daytime_hr != null && day.resting_hr != null && day.resting_hr > 0) {
    const elevation = (day.avg_daytime_hr - day.resting_hr) / day.resting_hr;
    comps.push(clamp((elevation - 0.05) * 8, 0, 3));
  }
  if (!comps.length) {
    return { score: null, level: "unknown", note: "Not enough daytime HR/HRV." };
  }
  let score = mean(comps);
  if (day.active_energy != null && day.active_energy > 500) score *= 0.85;
  score = Math.round(clamp(score, 0, 3) * 100) / 100;
  const level = score < 1 ? "low" : score < 2 ? "moderate" : "high";
  return { score, level, note: "" };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Headers": "x-api-key, authorization, content-type",
    },
  });

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return json({}, 204);

  if (API_KEY) {
    const supplied =
      req.headers.get("x-api-key") ??
      new URL(req.url).searchParams.get("key") ??
      "";
    if (supplied !== API_KEY) return json({ error: "Unauthorized" }, 401);
  }

  const { data, error } = await supabase
    .from("daily_metrics")
    .select("*")
    .order("date", { ascending: true });
  if (error) return json({ error: error.message }, 500);

  const days = (data ?? []) as Day[];
  const history = days.map((day, i) => {
    const window = days.slice(Math.max(0, i - BASELINE_WINDOW_DAYS), i);
    const baselines = buildBaselines(window);
    const recovery = computeRecovery(day, baselines);
    const stress = computeStress(day, baselines);
    return {
      date: day.date,
      recovery,
      stress,
      raw: {
        hrv: day.hrv, resting_hr: day.resting_hr, sleep_hours: day.sleep_hours,
        respiratory_rate: day.respiratory_rate, wrist_temperature: day.wrist_temperature,
        spo2: day.spo2,
      },
    };
  });

  return json({
    count: history.length,
    baseline_window_days: BASELINE_WINDOW_DAYS,
    latest: history.length ? history[history.length - 1] : null,
    history,
  });
});
