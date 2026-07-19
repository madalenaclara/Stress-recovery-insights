-- Workouts support. Run this in the Supabase SQL Editor (after 0001_init.sql).

create table if not exists public.workouts (
    id             text primary key,   -- stable id (workout start timestamp)
    date           date not null,
    type           text,
    start_at       text,
    end_at         text,
    duration_min   real,
    active_energy  real,   -- kcal
    avg_hr         real,   -- bpm
    max_hr         real,   -- bpm
    distance       real,   -- km (as reported)
    source         text
);
create index if not exists workouts_date_idx on public.workouts (date desc);

alter table public.workouts enable row level security;

-- Idempotent upsert: re-sending the same workout (same start id) updates it.
create or replace function public.upsert_workouts(rows jsonb)
returns integer
language plpgsql
as $$
declare r jsonb; n integer := 0;
begin
    for r in select * from jsonb_array_elements(rows)
    loop
        insert into public.workouts (
            id, date, type, start_at, end_at, duration_min,
            active_energy, avg_hr, max_hr, distance, source
        )
        values (
            r->>'id', (r->>'date')::date, r->>'type', r->>'start_at', r->>'end_at',
            (r->>'duration_min')::real, (r->>'active_energy')::real,
            (r->>'avg_hr')::real, (r->>'max_hr')::real, (r->>'distance')::real, r->>'source'
        )
        on conflict (id) do update set
            date = excluded.date, type = excluded.type,
            start_at = excluded.start_at, end_at = excluded.end_at,
            duration_min = coalesce(excluded.duration_min, public.workouts.duration_min),
            active_energy = coalesce(excluded.active_energy, public.workouts.active_energy),
            avg_hr = coalesce(excluded.avg_hr, public.workouts.avg_hr),
            max_hr = coalesce(excluded.max_hr, public.workouts.max_hr),
            distance = coalesce(excluded.distance, public.workouts.distance),
            source = coalesce(excluded.source, public.workouts.source);
        n := n + 1;
    end loop;
    return n;
end;
$$;
