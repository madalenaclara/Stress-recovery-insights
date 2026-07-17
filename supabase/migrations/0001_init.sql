-- Schema for Stress & Recovery Insights on Supabase.
-- Run this in the Supabase SQL Editor (or via `supabase db push`).

create table if not exists public.daily_metrics (
    date               date primary key,
    hrv                real,   -- HRV SDNN, ms
    resting_hr         real,   -- bpm
    sleep_hours        real,   -- hours asleep
    respiratory_rate   real,   -- breaths/min
    wrist_temperature  real,   -- deg C, sleeping wrist temp
    spo2               real,   -- %
    avg_daytime_hr     real,   -- bpm
    active_energy      real    -- kcal (motion proxy)
);

-- Idempotent merge upsert: incoming non-null values win, existing values are
-- preserved when the incoming field is null. Lets you re-send overlapping days
-- safely (e.g. a morning sync that only carries part of the previous day).
create or replace function public.upsert_daily(rows jsonb)
returns integer
language plpgsql
as $$
declare
    r jsonb;
    n integer := 0;
begin
    for r in select * from jsonb_array_elements(rows)
    loop
        insert into public.daily_metrics (
            date, hrv, resting_hr, sleep_hours, respiratory_rate,
            wrist_temperature, spo2, avg_daytime_hr, active_energy
        )
        values (
            (r->>'date')::date,
            (r->>'hrv')::real,
            (r->>'resting_hr')::real,
            (r->>'sleep_hours')::real,
            (r->>'respiratory_rate')::real,
            (r->>'wrist_temperature')::real,
            (r->>'spo2')::real,
            (r->>'avg_daytime_hr')::real,
            (r->>'active_energy')::real
        )
        on conflict (date) do update set
            hrv               = coalesce(excluded.hrv, public.daily_metrics.hrv),
            resting_hr        = coalesce(excluded.resting_hr, public.daily_metrics.resting_hr),
            sleep_hours       = coalesce(excluded.sleep_hours, public.daily_metrics.sleep_hours),
            respiratory_rate  = coalesce(excluded.respiratory_rate, public.daily_metrics.respiratory_rate),
            wrist_temperature = coalesce(excluded.wrist_temperature, public.daily_metrics.wrist_temperature),
            spo2              = coalesce(excluded.spo2, public.daily_metrics.spo2),
            avg_daytime_hr    = coalesce(excluded.avg_daytime_hr, public.daily_metrics.avg_daytime_hr),
            active_energy     = coalesce(excluded.active_energy, public.daily_metrics.active_energy);
        n := n + 1;
    end loop;
    return n;
end;
$$;

-- Row Level Security: lock the table down. Only the Edge Functions (which use
-- the service-role key) can read/write it; the public anon key cannot.
alter table public.daily_metrics enable row level security;
