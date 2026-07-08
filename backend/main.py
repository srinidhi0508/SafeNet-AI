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
from dataclasses import asdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from sensor_simulator import Reading, SAFE_RANGES, GAS_DANGER_THRESHOLD, ZONES, _now, _unit_for
from risk_engine import PermitLog, RiskEngine

app = FastAPI(title="SafeNet backend")

# Wide open for hackathon dev. Tighten allow_origins before any real deploy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/")
def health():
    return {"status": "ok", "service": "SafeNet backend"}


@app.websocket("/ws/stream")
async def stream(websocket: WebSocket, ramp_seconds: int = 180,
                  interval: float = 1.0, zone: str = "zone_4"):
    await websocket.accept()

    permit_log = PermitLog()
    engine = RiskEngine(permit_log)

    try:
        async for reading, progress in async_run_scenario(
            danger_zone=zone, ramp_seconds=ramp_seconds, interval=interval
        ):
            # Always forward the raw reading — the dashboard's live gauges
            # and charts consume this directly.
            await websocket.send_text(json.dumps({"type": "reading", **asdict(reading)}))

            if reading.sensor_type != "gas":
                continue

            from datetime import datetime
            ts = datetime.fromisoformat(reading.timestamp)
            alert = engine.ingest_gas_reading(reading.zone, reading.value, ts, progress)

            # Risk score update every gas tick, for a live-moving gauge —
            # separate from the one-time alert event.
            await websocket.send_text(json.dumps({
                "type": "risk_update",
                "zone": reading.zone,
                "risk_score": engine.risk_score(reading.zone, progress),
            }))

            if alert:
                await websocket.send_text(json.dumps(alert))

        await websocket.send_text(json.dumps({"type": "scenario_complete"}))

    except WebSocketDisconnect:
        # Client closed the tab/connection — nothing to clean up, the
        # generator and engine are local to this connection's coroutine.
        pass
