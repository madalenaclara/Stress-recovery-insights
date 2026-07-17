"""FastAPI backend: ingest Health Auto Export data, serve scored insights.

Endpoints
---------
POST /ingest         Receive a Health Auto Export JSON payload; store daily metrics.
GET  /api/insights   Return scored Recovery + Stress history (+ latest snapshot).
GET  /api/raw        Return the stored raw daily metrics.
GET  /health         Liveness probe.
GET  /               The dashboard (static HTML).

Point the Health Auto Export app's "REST API" automation at POST /ingest and it
will push new Apple Watch data on a schedule with no manual step.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, ingest
from .scoring import (
    DailyMetrics,
    build_baselines,
    compute_recovery,
    compute_stress,
)

BASELINE_WINDOW_DAYS = int(os.environ.get("SRI_BASELINE_WINDOW", "21"))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")

app = FastAPI(title="Stress & Recovery Insights", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/ingest")
async def ingest_endpoint(request: Request) -> JSONResponse:
    """Accept a Health Auto Export payload and upsert per-day metrics."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Body must be valid JSON."}, status_code=400)

    days = ingest.parse_payload(payload)
    for day in days:
        db.upsert_day(day)

    return JSONResponse({
        "ingested_days": len(days),
        "dates": [d.date for d in days],
    })


def _score_history() -> list[dict]:
    """Score every stored day against the days that preceded it."""
    all_days = db.get_all()
    out: list[dict] = []
    for i, day in enumerate(all_days):
        history = all_days[max(0, i - BASELINE_WINDOW_DAYS):i]
        baselines = build_baselines(history)
        rec = compute_recovery(day, baselines)
        stress = compute_stress(day, baselines)
        out.append({
            "date": day.date,
            "recovery": {
                "score": rec.score,
                "state": rec.state,
                "contributions": rec.contributions,
                "note": rec.note,
            },
            "stress": {
                "score": stress.score,
                "level": stress.level,
                "note": stress.note,
            },
            "raw": {
                "hrv": day.hrv,
                "resting_hr": day.resting_hr,
                "sleep_hours": day.sleep_hours,
                "respiratory_rate": day.respiratory_rate,
                "wrist_temperature": day.wrist_temperature,
                "spo2": day.spo2,
            },
        })
    return out


@app.get("/api/insights")
def insights() -> dict:
    history = _score_history()
    return {
        "count": len(history),
        "baseline_window_days": BASELINE_WINDOW_DAYS,
        "latest": history[-1] if history else None,
        "history": history,
    }


@app.get("/api/raw")
def raw() -> dict:
    return {"days": [d.__dict__ for d in db.get_all()]}


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# Serve any other static assets (kept minimal; the dashboard is self-contained).
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
