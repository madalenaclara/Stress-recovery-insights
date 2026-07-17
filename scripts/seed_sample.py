"""Generate a realistic ~30-day Health Auto Export payload and load it.

Run from the project root:

    python scripts/seed_sample.py            # writes sample + seeds the DB
    python scripts/seed_sample.py --write-only   # only write the JSON file

The generated data has a gentle weekly rhythm plus a deliberate "hard training +
poor sleep" dip mid-month, so Recovery drops and Stress rises there — useful for
eyeballing that the scoring reacts sensibly. No randomness that needs a seed:
values are produced from deterministic wave functions.
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SAMPLE_PATH = os.path.join(ROOT, "sample_data", "health_auto_export_sample.json")

DAYS = 30
START = date(2026, 6, 17)  # fixed start so output is reproducible


def _wave(i: int, period: float, amp: float, phase: float = 0.0) -> float:
    return amp * math.sin(2 * math.pi * (i / period) + phase)


def build_payload() -> dict:
    hrv, rhr, resp, temp, spo2, sleep, day_hr, energy = ([] for _ in range(8))

    for i in range(DAYS):
        d = START + timedelta(days=i)
        stamp = f"{d.isoformat()} 07:00:00 +0000"

        # A mid-month slump (days 13-16): overtraining + short sleep.
        slump = 1.0 if 13 <= i <= 16 else 0.0

        base_hrv = 62 + _wave(i, 7, 6) - 18 * slump
        base_rhr = 54 - _wave(i, 7, 2) + 7 * slump
        base_resp = 14.5 + _wave(i, 9, 0.6) + 1.2 * slump
        base_temp = 33.6 + _wave(i, 11, 0.15) + 0.35 * slump
        base_sleep = 7.6 + _wave(i, 7, 0.6) - 2.2 * slump
        base_dayhr = 78 + _wave(i, 5, 4) + 10 * slump
        base_energy = 480 + _wave(i, 7, 120) + 500 * slump

        hrv.append({"date": stamp, "qty": round(base_hrv, 1), "source": "Apple Watch"})
        rhr.append({"date": stamp, "qty": round(base_rhr, 1), "source": "Apple Watch"})
        resp.append({"date": stamp, "qty": round(base_resp, 2), "source": "Apple Watch"})
        temp.append({"date": stamp, "qty": round(base_temp, 2), "source": "Apple Watch"})
        spo2.append({"date": stamp, "qty": round(97.5 - 0.8 * slump, 1), "source": "Apple Watch"})
        day_hr.append({"date": stamp, "qty": round(base_dayhr, 1), "source": "Apple Watch"})
        energy.append({"date": stamp, "qty": round(base_energy, 0), "source": "Apple Watch"})
        sleep.append({
            "date": stamp,
            "sleepEnd": stamp,
            "asleep": round(max(3.5, base_sleep), 2),
            "source": "Apple Watch",
        })

    return {
        "data": {
            "metrics": [
                {"name": "heart_rate_variability", "units": "ms", "data": hrv},
                {"name": "resting_heart_rate", "units": "bpm", "data": rhr},
                {"name": "respiratory_rate", "units": "count/min", "data": resp},
                {"name": "apple_sleeping_wrist_temperature", "units": "degC", "data": temp},
                {"name": "blood_oxygen_saturation", "units": "%", "data": spo2},
                {"name": "heart_rate", "units": "bpm", "data": day_hr},
                {"name": "active_energy", "units": "kcal", "data": energy},
                {"name": "sleep_analysis", "units": "hr", "data": sleep},
            ],
            "workouts": [],
        }
    }


def main() -> None:
    payload = build_payload()
    os.makedirs(os.path.dirname(SAMPLE_PATH), exist_ok=True)
    with open(SAMPLE_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote sample payload -> {SAMPLE_PATH}")

    if "--write-only" in sys.argv:
        return

    from app import db, ingest
    db.init_db()
    days = ingest.parse_payload(payload)
    for day in days:
        db.upsert_day(day)
    print(f"Seeded {len(days)} days into the database.")
    print("Start the server with:  uvicorn app.main:app --reload")


if __name__ == "__main__":
    main()
