"""Unit tests for scoring and ingestion. Run with:  python -m pytest -q

Kept dependency-light: these also run under plain `python tests/test_scoring.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ingest import parse_payload
from app.scoring import (
    DailyMetrics,
    build_baselines,
    compute_recovery,
    compute_stress,
)


def _stable_history(days=20, **overrides):
    """A run of near-identical good days, so baselines are tight."""
    out = []
    for i in range(days):
        d = DailyMetrics(
            date=f"2026-06-{i+1:02d}",
            hrv=60.0, resting_hr=54.0, sleep_hours=7.8,
            respiratory_rate=14.5, wrist_temperature=33.6,
            avg_daytime_hr=76.0, active_energy=450.0,
        )
        for k, v in overrides.items():
            setattr(d, k, v)
        out.append(d)
    return out


def test_good_day_scores_high():
    history = _stable_history()
    baselines = build_baselines(history)
    # a day matching baseline should land near the middle-high band
    day = DailyMetrics(date="2026-07-01", hrv=60, resting_hr=54, sleep_hours=7.8,
                       respiratory_rate=14.5, wrist_temperature=33.6)
    rec = compute_recovery(day, baselines)
    assert rec.score is not None
    assert 40 <= rec.score <= 65  # at-baseline ~ middle


def test_suppressed_hrv_lowers_recovery():
    history = _stable_history()
    baselines = build_baselines(history)
    good = compute_recovery(DailyMetrics("2026-07-01", hrv=70, resting_hr=50,
                            sleep_hours=8.2, respiratory_rate=14.5,
                            wrist_temperature=33.6), baselines)
    bad = compute_recovery(DailyMetrics("2026-07-02", hrv=38, resting_hr=64,
                           sleep_hours=5.0, respiratory_rate=16.5,
                           wrist_temperature=34.2), baselines)
    assert good.score > bad.score
    assert bad.state in ("red", "yellow")


def test_stress_rises_with_elevated_hr_and_low_hrv():
    history = _stable_history()
    baselines = build_baselines(history)
    calm = compute_stress(DailyMetrics("2026-07-01", hrv=64, resting_hr=54,
                          avg_daytime_hr=72), baselines)
    tense = compute_stress(DailyMetrics("2026-07-02", hrv=40, resting_hr=54,
                           avg_daytime_hr=95), baselines)
    assert calm.score is not None and tense.score is not None
    assert tense.score > calm.score


def test_baseline_not_ready_flag():
    history = _stable_history(days=2)
    baselines = build_baselines(history)
    rec = compute_recovery(DailyMetrics("2026-07-01", hrv=60, resting_hr=54,
                           sleep_hours=7.8), baselines)
    assert rec.baselines_ready is False
    assert rec.state == "unknown"


def test_parse_payload_roundtrip():
    payload = {"data": {"metrics": [
        {"name": "heart_rate_variability", "data": [
            {"date": "2026-06-17 07:00:00 +0000", "qty": 61.2}]},
        {"name": "resting_heart_rate", "data": [
            {"date": "2026-06-17 07:00:00 +0000", "qty": 53.0}]},
        {"name": "sleep_analysis", "data": [
            {"date": "2026-06-17 07:00:00 +0000", "sleepEnd": "2026-06-17 07:00:00 +0000",
             "asleep": 7.5}]},
    ]}}
    days = parse_payload(payload)
    assert len(days) == 1
    assert days[0].date == "2026-06-17"
    assert abs(days[0].hrv - 61.2) < 1e-6
    assert abs(days[0].sleep_hours - 7.5) < 1e-6


def test_intraday_hrv_is_averaged():
    payload = {"data": {"metrics": [
        {"name": "heart_rate_variability", "data": [
            {"date": "2026-06-17 02:00:00 +0000", "qty": 50.0},
            {"date": "2026-06-17 04:00:00 +0000", "qty": 70.0}]},
    ]}}
    days = parse_payload(payload)
    assert abs(days[0].hrv - 60.0) < 1e-6


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")
