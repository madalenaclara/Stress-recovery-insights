"""Parse Health Auto Export JSON payloads into normalised daily metrics.

The Health Auto Export app (iOS) can POST a JSON body shaped like:

    {
      "data": {
        "metrics": [
          {
            "name": "heart_rate_variability",
            "units": "ms",
            "data": [
              {"date": "2026-07-01 00:00:00 +0000", "qty": 62.3, "source": "Apple Watch"}
            ]
          },
          ...
        ],
        "workouts": [...]
      }
    }

Metric `name`s vary slightly by app version / locale, so we match on a set of
known aliases and are tolerant of missing pieces. Sleep is a special case: it may
arrive either as an aggregated `sleep_analysis` metric or as duration fields.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable, Optional

from .scoring import DailyMetrics


# Map Health Auto Export metric names -> our internal field. Multiple source
# names can map to the same field.
METRIC_ALIASES: dict[str, str] = {
    "heart_rate_variability": "hrv",
    "heart_rate_variability_sdnn": "hrv",
    "resting_heart_rate": "resting_hr",
    "respiratory_rate": "respiratory_rate",
    "apple_sleeping_wrist_temperature": "wrist_temperature",
    "wrist_temperature": "wrist_temperature",
    "blood_oxygen_saturation": "spo2",
    "oxygen_saturation": "spo2",
    "active_energy": "active_energy",
    "active_energy_burned": "active_energy",
}

# Fields where we want the mean across a day's samples vs. a single daily value.
# Resting HR / sleeping wrist temp are effectively one-per-day already; taking
# the mean is harmless if several are present.


def _parse_date(raw: str) -> Optional[str]:
    """Return an ISO date (YYYY-MM-DD) from Health Auto Export's date strings."""
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    # Last resort: take the leading YYYY-MM-DD if present.
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    return None


def _sample_qty(sample: dict) -> Optional[float]:
    """Health Auto Export uses `qty` for scalars, or Avg/min/max for some types."""
    for key in ("qty", "Avg", "avg", "value"):
        if key in sample and sample[key] is not None:
            try:
                return float(sample[key])
            except (TypeError, ValueError):
                pass
    return None


def _sleep_hours_from_sample(sample: dict) -> Optional[float]:
    """Extract hours asleep from a sleep_analysis sample.

    Health Auto Export sleep samples expose several possible keys depending on
    version: `asleep` (hours), `totalSleep`, or core/deep/rem components. We take
    the most specific total available.
    """
    for key in ("asleep", "totalSleep", "total_sleep", "sleepDuration"):
        if key in sample and sample[key] is not None:
            try:
                return float(sample[key])
            except (TypeError, ValueError):
                pass
    # Sum of stages, if that's all we have.
    stages = [sample.get(k) for k in ("core", "deep", "rem")]
    stages = [float(s) for s in stages if s is not None]
    if stages:
        return sum(stages)
    # inBed minus awake, as a fallback.
    try:
        in_bed = float(sample.get("inBed"))
        awake = float(sample.get("awake", 0.0) or 0.0)
        return max(0.0, in_bed - awake)
    except (TypeError, ValueError):
        return None


def parse_payload(payload: dict) -> list[DailyMetrics]:
    """Turn a Health Auto Export payload into a list of per-day DailyMetrics."""
    data = payload.get("data", payload)  # tolerate payloads without the wrapper
    metrics = data.get("metrics", []) or []

    # Accumulate raw samples per (date, field) so we can average intraday values.
    buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for metric in metrics:
        name = str(metric.get("name", "")).strip().lower()
        samples = metric.get("data", []) or []

        if name in ("sleep_analysis", "sleep"):
            for s in samples:
                # Sleep can be dated at the *end* of the night; attribute it to
                # the date the person woke, using sleepEnd if available.
                date = _parse_date(s.get("sleepEnd") or s.get("date") or "")
                hours = _sleep_hours_from_sample(s)
                if date and hours is not None:
                    buckets[date]["sleep_hours"].append(hours)
            continue

        field = METRIC_ALIASES.get(name)
        if not field:
            continue  # metric we don't score on; ignore quietly

        for s in samples:
            date = _parse_date(s.get("date") or "")
            qty = _sample_qty(s)
            if date and qty is not None:
                buckets[date][field].append(qty)
                # Heart-rate samples during the day also feed the stress proxy.

    # Separately capture average daytime HR if a heart_rate metric is present.
    for metric in metrics:
        name = str(metric.get("name", "")).strip().lower()
        if name in ("heart_rate", "heart_rate_average"):
            for s in metric.get("data", []) or []:
                date = _parse_date(s.get("date") or "")
                qty = _sample_qty(s)
                if date and qty is not None:
                    buckets[date]["avg_daytime_hr"].append(qty)

    results: list[DailyMetrics] = []
    for date in sorted(buckets):
        b = buckets[date]

        def avg(field: str) -> Optional[float]:
            vals = b.get(field) or []
            return sum(vals) / len(vals) if vals else None

        def total(field: str) -> Optional[float]:
            vals = b.get(field) or []
            return sum(vals) if vals else None

        results.append(DailyMetrics(
            date=date,
            hrv=avg("hrv"),
            resting_hr=avg("resting_hr"),
            sleep_hours=max(b.get("sleep_hours", [0.0])) if b.get("sleep_hours") else None,
            respiratory_rate=avg("respiratory_rate"),
            wrist_temperature=avg("wrist_temperature"),
            spo2=avg("spo2"),
            avg_daytime_hr=avg("avg_daytime_hr"),
            active_energy=total("active_energy"),
        ))
    return results


def merge_daily(existing: DailyMetrics, incoming: DailyMetrics) -> DailyMetrics:
    """Prefer non-None incoming values over existing ones (idempotent re-sync)."""
    merged = DailyMetrics(date=existing.date)
    for f in ("hrv", "resting_hr", "sleep_hours", "respiratory_rate",
              "wrist_temperature", "spo2", "avg_daytime_hr", "active_energy"):
        new = getattr(incoming, f)
        setattr(merged, f, new if new is not None else getattr(existing, f))
    return merged
