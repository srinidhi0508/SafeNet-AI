"""
SafeNet FastAPI backend.

This is the orchestration layer: it runs the scripted sensor scenario,
feeds every gas reading into the compound risk engine, and streams both
raw readings and alerts to the frontend over a single WebSocket connection.

Run with:
    uvicorn main:app --reload

Then connect a WebSocket client to:
    ws://localhost:8000/ws/stream

Query params (all optional):
    ramp_seconds  - total scenario duration (default 180)
    interval      - seconds between reading batches (default 1.0)
    zone          - which zone goes into danger (default zone_4)
"""

import asyncio
import json
import random
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from sensor_simulator import Reading, SAFE_RANGES, GAS_DANGER_THRESHOLD, ZONES, _now, _unit_for
from risk_engine import PermitLog, RiskEngine
import cv_timeline

# rag_agent.py lives in a sibling folder (../rag-agent), not installed as a
# package — add it to the path so we can import it directly.
RAG_AGENT_DIR = Path(__file__).resolve().parent.parent / "rag-agent"
if str(RAG_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(RAG_AGENT_DIR))

try:
    from rag_agent import generate_warning
    RAG_AVAILABLE = True
except Exception as _rag_import_error:  # missing deps, no ingested data, etc.
    RAG_AVAILABLE = False
    print(f"[SafeNet] RAG agent unavailable, alerts will skip historical "
          f"context: {_rag_import_error}")

app = FastAPI(title="SafeNet backend")

# Wide open for hackathon dev. Tighten allow_origins before any real deploy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the CV module's annotated video directly so the dashboard can
# play it in a <video> tag without copying the file into frontend/.
# no-cache headers matter here specifically: if you re-run detect.py and
# replace annotated.mp4, browsers will otherwise keep serving the old
# cached copy from the same URL indefinitely.
class NoCacheStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response

if cv_timeline.cv_available():
    app.mount("/media", NoCacheStaticFiles(directory=str(cv_timeline.CV_OUTPUT_DIR)), name="media")


async def async_run_scenario(danger_zone: str = "zone_4", ramp_seconds: int = 180,
                              interval: float = 1.0):
    """
    Async twin of sensor_simulator.run_scenario. Uses asyncio.sleep instead
    of time.sleep so it doesn't block the event loop while streaming over
    a WebSocket — otherwise every connected client would freeze the server
    for the sleep duration on every tick.

    Yields (Reading, progress) so the caller can hand progress straight to
    the risk engine's permit-window lookup without recomputing it.
    """
    start_gas, _ = SAFE_RANGES["gas"]
    ticks = int(ramp_seconds / interval)

    for tick in range(ticks + 1):
        progress = tick / ticks

        for zone in ZONES:
            if zone != danger_zone:
                for sensor_type in SAFE_RANGES:
                    low, high = SAFE_RANGES[sensor_type]
                    value = round(random.uniform(low, high), 1)
                    yield Reading(_now(), zone, sensor_type, value, _unit_for(sensor_type)), progress
                continue

            gas_value = start_gas + progress * (GAS_DANGER_THRESHOLD + 15 - start_gas)
            gas_value += random.uniform(-1.5, 1.5)
            yield Reading(_now(), zone, "gas", round(gas_value, 1), "ppm"), progress

            temp_low, temp_high = SAFE_RANGES["temperature"]
            temp_value = temp_high - 3 + progress * 8 + random.uniform(-0.5, 0.5)
            yield Reading(_now(), zone, "temperature", round(temp_value, 1), "C"), progress

            pres_low, pres_high = SAFE_RANGES["pressure"]
            pres_value = pres_high - 2 + progress * 6 + random.uniform(-0.3, 0.3)
            yield Reading(_now(), zone, "pressure", round(pres_value, 1), "kPa"), progress

        await asyncio.sleep(interval)


async def send_rag_context(websocket: WebSocket, alert: dict, lock: asyncio.Lock):
    """
    Runs in the background after a compound alert fires. Calls the RAG
    agent (which may block for several seconds, or longer if it's
    retrying a rate limit) in a thread so it never blocks the sensor
    stream, then sends the result as a follow-up message once ready.
    """
    situation = (
        f"Gas rising in {alert['zone']} ({alert['gas_ppm']} ppm, "
        f"trend {alert['trend_ppm_per_sec']} ppm/s). Reason flagged: {alert['reason']}."
    )
    loop = asyncio.get_event_loop()
    try:
        # retry_waits=() means one fast attempt only — a rate limit here
        # should skip the historical warning, not stall the live demo
        # for up to ~2 minutes waiting on retries.
        result = await loop.run_in_executor(
            None, lambda: generate_warning(situation, 3, ())
        )
    except Exception as e:
        result = {"warning": None, "sources": [], "error": str(e)}

    payload = {
        "type": "rag_context",
        "zone": alert["zone"],
        "warning": result["warning"],
        "sources": result["sources"],
        "error": result["error"],
    }
    async with lock:
        try:
            await websocket.send_text(json.dumps(payload))
        except Exception:
            pass  # connection likely already closed (scenario ended) — fine to drop


@app.get("/")
def health():
    return {
        "status": "ok",
        "service": "SafeNet backend",
        "rag_available": RAG_AVAILABLE,
        "cv_available": cv_timeline.cv_available(),
    }


@app.websocket("/ws/stream")
async def stream(websocket: WebSocket, ramp_seconds: int = 180,
                  interval: float = 1.0, zone: str = "zone_4"):
    await websocket.accept()

    permit_log = PermitLog()
    scenario_start = datetime.now(timezone.utc)

    def worker_near_hazard(check_zone: str) -> bool:
        # Only the danger zone has a camera in this demo. The dashboard's
        # video plays on loop, so we mirror that by looping the lookup
        # against elapsed wall-clock time since this connection started.
        if not cv_timeline.cv_available() or check_zone != zone:
            return False
        elapsed = (datetime.now(timezone.utc) - scenario_start).total_seconds()
        in_zone, _violation = cv_timeline.worker_status_at(elapsed)
        return in_zone

    engine = RiskEngine(permit_log, worker_near_hazard=worker_near_hazard)
    send_lock = asyncio.Lock()

    async def safe_send(payload: dict):
        async with send_lock:
            await websocket.send_text(json.dumps(payload))

    try:
        async for reading, progress in async_run_scenario(
            danger_zone=zone, ramp_seconds=ramp_seconds, interval=interval
        ):
            # Always forward the raw reading — the dashboard's live gauges
            # and charts consume this directly.
            await safe_send({"type": "reading", **asdict(reading)})

            if reading.sensor_type != "gas":
                continue

            ts = datetime.fromisoformat(reading.timestamp)
            alert = engine.ingest_gas_reading(reading.zone, reading.value, ts, progress)

            # Live CV status for the danger zone, sent alongside the risk
            # score so the dashboard can show "worker detected" in real
            # time without inspecting the video itself.
            if reading.zone == zone and cv_timeline.cv_available():
                elapsed = (datetime.now(timezone.utc) - scenario_start).total_seconds()
                in_zone, violation = cv_timeline.worker_status_at(elapsed)
                await safe_send({
                    "type": "cv_status",
                    "zone": reading.zone,
                    "worker_in_zone": in_zone,
                    "violation": violation,
                })

            # Risk score update every gas tick, for a live-moving gauge —
            # separate from the one-time alert event.
            await safe_send({
                "type": "risk_update",
                "zone": reading.zone,
                "risk_score": engine.risk_score(reading.zone, progress),
            })

            if alert:
                await safe_send(alert)
                if RAG_AVAILABLE:
                    # Fire and forget — the sensor stream keeps running
                    # while this resolves in the background.
                    asyncio.create_task(send_rag_context(websocket, alert, send_lock))

        await safe_send({"type": "scenario_complete"})

    except WebSocketDisconnect:
        # Client closed the tab/connection — nothing to clean up, the
        # generator and engine are local to this connection's coroutine.
        pass
