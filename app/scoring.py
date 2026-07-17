"""Baseline-relative Recovery and Stress scoring.

The philosophy mirrors Whoop: there is no magic sensor. Every metric is compared
against *your own* rolling baseline (mean + standard deviation over a trailing
window), turned into a z-score, and combined with sensible weights.

- Recovery (0-100): a daily "readiness" number driven mostly by HRV, then resting
  heart rate, sleep, respiratory rate and wrist temperature.
- Stress (0.0-3.0): how far current physiology sits above the calm baseline,
  driven by HRV suppression and elevated heart rate, damped when you were moving
  (so a workout doesn't read as stress).

All functions are pure and dependency-free (standard library only) so they are
trivial to unit-test and reason about.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Optional


# --- Which raw metrics feed each score, and how they behave -----------------
#
# direction:  +1  -> higher is better for recovery (e.g. HRV)
#             -1  -> lower is better for recovery (e.g. resting HR)
#              0  -> closer to your baseline is better; any deviation is bad
#                    (e.g. respiratory rate, wrist temperature)
#
# weight:     relative importance in the recovery blend. HRV dominates, exactly
#             as it does for Whoop.

RECOVERY_METRICS: dict[str, dict] = {
    "hrv": {"direction": +1, "weight": 0.40},
    "resting_hr": {"direction": -1, "weight": 0.20},
    "sleep_hours": {"direction": +1, "weight": 0.20},
    "respiratory_rate": {"direction": 0, "weight": 0.10},
    "wrist_temperature": {"direction": 0, "weight": 0.10},
}

# Sleep is also judged against an absolute need, not only your baseline, because
# "consistently under-slept" should still score low.
DEFAULT_SLEEP_NEED_HOURS = 8.0

# Minimum days of history before a baseline is considered trustworthy.
MIN_BASELINE_DAYS = 5


@dataclass
class Baseline:
    """Rolling mean/std for a single metric over the trailing window."""

    mean: float
    std: float
    n: int

    def zscore(self, value: float) -> float:
        if self.std <= 1e-9:
            return 0.0
        return (value - self.mean) / self.std


@dataclass
class DailyMetrics:
    """One day's aggregated overnight/daytime values. Any field may be None."""

    date: str
    hrv: Optional[float] = None                # SDNN, ms
    resting_hr: Optional[float] = None          # bpm
    sleep_hours: Optional[float] = None         # hours asleep
    respiratory_rate: Optional[float] = None    # breaths/min
    wrist_temperature: Optional[float] = None   # deg C, sleeping wrist temp
    spo2: Optional[float] = None                # % (informational)
    # daytime signals used for the stress estimate
    avg_daytime_hr: Optional[float] = None      # bpm
    active_energy: Optional[float] = None       # kcal, a motion proxy

    def get(self, name: str) -> Optional[float]:
        return getattr(self, name, None)


@dataclass
class RecoveryResult:
    date: str
    score: Optional[float]                # 0-100, None if not enough data
    state: str                            # "green" / "yellow" / "red" / "unknown"
    contributions: dict = field(default_factory=dict)
    baselines_ready: bool = False
    note: str = ""


@dataclass
class StressResult:
    date: str
    score: Optional[float]                # 0.0-3.0
    level: str                            # "low"/"moderate"/"high"/"unknown"
    note: str = ""


def build_baselines(history: list[DailyMetrics]) -> dict[str, Baseline]:
    """Compute a rolling baseline per metric from prior days.

    `history` should already be filtered to the trailing window (e.g. the 14-30
    days *before* the day being scored). The current day is intentionally not
    part of its own baseline.
    """
    baselines: dict[str, Baseline] = {}
    fields = set(RECOVERY_METRICS) | {"avg_daytime_hr"}
    for name in fields:
        values = [d.get(name) for d in history]
        values = [v for v in values if v is not None]
        if len(values) >= 2:
            baselines[name] = Baseline(mean=mean(values), std=pstdev(values), n=len(values))
        elif len(values) == 1:
            baselines[name] = Baseline(mean=values[0], std=0.0, n=1)
    return baselines


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_recovery(
    day: DailyMetrics,
    baselines: dict[str, Baseline],
    sleep_need_hours: float = DEFAULT_SLEEP_NEED_HOURS,
) -> RecoveryResult:
    """Blend baseline-relative z-scores into a 0-100 recovery score."""
    contributions: dict[str, dict] = {}
    total_weight = 0.0
    weighted_signal = 0.0
    baseline_days = max((b.n for b in baselines.values()), default=0)
    baselines_ready = baseline_days >= MIN_BASELINE_DAYS

    for name, cfg in RECOVERY_METRICS.items():
        value = day.get(name)
        base = baselines.get(name)
        if value is None or base is None:
            continue

        z = base.zscore(value)
        direction = cfg["direction"]

        if direction == +1:
            signal = z
        elif direction == -1:
            signal = -z
        else:  # deviation in either direction is bad
            signal = -abs(z)

        # Sleep gets an extra absolute-need term so chronic under-sleep scores low.
        if name == "sleep_hours":
            need_ratio = _clamp(value / sleep_need_hours, 0.0, 1.0)
            # map ratio 0..1 -> roughly -1.5..+1.0 on the z-scale, then average
            signal = 0.5 * signal + 0.5 * (need_ratio * 2.5 - 1.5)

        signal = _clamp(signal, -3.0, 3.0)
        weight = cfg["weight"]
        weighted_signal += signal * weight
        total_weight += weight
        contributions[name] = {
            "value": round(value, 2),
            "baseline": round(base.mean, 2),
            "z": round(z, 2),
            "signal": round(signal, 2),
            "weight": weight,
        }

    if total_weight == 0:
        return RecoveryResult(day.date, None, "unknown",
                              note="No metrics available for this day.")

    # Normalise for whichever metrics were actually present.
    avg_signal = weighted_signal / total_weight
    # Logistic squashes the average z-signal into 0-100, centred at 50.
    score = 100.0 / (1.0 + math.exp(-1.1 * avg_signal))
    score = round(_clamp(score, 1.0, 99.0), 1)

    if not baselines_ready:
        state = "unknown"
        note = (f"Baseline still forming ({baseline_days}/{MIN_BASELINE_DAYS} days). "
                "Score shown for reference only.")
    else:
        state = "green" if score >= 67 else "yellow" if score >= 34 else "red"
        note = ""

    return RecoveryResult(day.date, score, state, contributions, baselines_ready, note)


def compute_stress(day: DailyMetrics, baselines: dict[str, Baseline]) -> StressResult:
    """Estimate a 0-3 physiological stress score from daytime HR + HRV.

    Whoop compares live HR/HRV to your baseline and subtracts motion so exertion
    doesn't count as stress. With daily aggregates we approximate that: elevated
    daytime heart rate above resting + suppressed HRV => stress, damped when the
    day involved a lot of active energy (movement).
    """
    hrv = day.hrv
    hrv_base = baselines.get("hrv")
    hr = day.avg_daytime_hr
    rhr = day.resting_hr

    components: list[float] = []

    # HRV suppression: HRV below baseline pushes stress up.
    if hrv is not None and hrv_base is not None and hrv_base.std > 1e-9:
        z = hrv_base.zscore(hrv)
        components.append(_clamp(-z, 0.0, 3.0))

    # Heart-rate elevation: daytime HR sitting well above resting => arousal.
    if hr is not None and rhr is not None and rhr > 0:
        elevation = (hr - rhr) / rhr  # fractional elevation over resting
        # ~10% over resting -> ~0.7; 40%+ over resting -> ~3
        components.append(_clamp((elevation - 0.05) * 8.0, 0.0, 3.0))

    if not components:
        return StressResult(day.date, None, "unknown",
                            note="Not enough daytime HR/HRV to estimate stress.")

    score = mean(components)

    # Motion damping: a physically active day inflates HR for non-stress reasons.
    if day.active_energy is not None and day.active_energy > 500:
        score *= 0.85

    score = round(_clamp(score, 0.0, 3.0), 2)
    level = "low" if score < 1.0 else "moderate" if score < 2.0 else "high"
    return StressResult(day.date, score, level)
