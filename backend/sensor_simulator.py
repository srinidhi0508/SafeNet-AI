"""
SafeNet sensor simulator.

Generates gas, temperature, and pressure readings for multiple plant zones.
Two modes:
  - "random": continuous, mostly-safe readings with small noise. Good for
    general testing of the backend/frontend pipeline.
  - "scenario": a scripted, reproducible sequence where gas in one zone
    climbs steadily toward a danger threshold. This is what the live demo
    should run, since it's dramatic and repeatable on demand.

Every reading is a single JSON object. Run this file directly to stream
readings to stdout (one JSON object per line), or import `run_scenario`
from your FastAPI backend to push readings over a WebSocket.
"""

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

ZONES = ["zone_1", "zone_2", "zone_3", "zone_4", "zone_5"]

# Safe operating ranges per sensor type. Used both to generate believable
# noise and as the reference the risk engine's "hard threshold" line is
# drawn from.
SAFE_RANGES = {
    "gas": (5, 25),          # ppm
    "temperature": (28, 42), # Celsius
    "pressure": (95, 105),   # kPa
}

GAS_DANGER_THRESHOLD = 80  # ppm — traditional single-sensor alarm point


@dataclass
class Reading:
    timestamp: str
    zone: str
    sensor_type: str
    value: float
    unit: str

    def to_json(self) -> str:
        return json.dumps(asdict(self))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unit_for(sensor_type: str) -> str:
    return {"gas": "ppm", "temperature": "C", "pressure": "kPa"}[sensor_type]


def random_reading(zone: str, sensor_type: str) -> Reading:
    """A single safe-range reading with small random noise."""
    low, high = SAFE_RANGES[sensor_type]
    value = round(random.uniform(low, high), 1)
    return Reading(_now(), zone, sensor_type, value, _unit_for(sensor_type))


def run_random(interval: float = 1.0, limit: int | None = None):
    """
    Continuous stream of mostly-safe readings across all zones.
    Yields one Reading at a time. Use for general pipeline testing,
    not for the live demo (nothing dangerous ever happens here).
    """
    count = 0
    while limit is None or count < limit:
        for zone in ZONES:
            for sensor_type in SAFE_RANGES:
                yield random_reading(zone, sensor_type)
                count += 1
        time.sleep(interval)


def run_scenario(danger_zone: str = "zone_4", ramp_seconds: int = 180,
                  interval: float = 1.0):
    """
    Scripted demo scenario.

    All zones stay in safe ranges except `danger_zone`, where gas climbs
    linearly from a safe baseline to just past GAS_DANGER_THRESHOLD over
    `ramp_seconds`. Temperature and pressure in that zone drift up slightly
    too, since a real gas leak usually isn't isolated to one reading type.

    This is what makes the "detected X minutes before threshold breach"
    number honest and reproducible: the risk engine's compound-condition
    alert timestamp minus this ramp's fixed breach timestamp is your number,
    every time you run the demo the same way.

    Yields one Reading per tick, in real time (respecting `interval`).
    """
    start_gas, _ = SAFE_RANGES["gas"]
    ticks = int(ramp_seconds / interval)

    for tick in range(ticks + 1):
        progress = tick / ticks  # 0.0 -> 1.0 over the ramp

        for zone in ZONES:
            if zone != danger_zone:
                # unaffected zones: normal noise throughout
                for sensor_type in SAFE_RANGES:
                    yield random_reading(zone, sensor_type)
                continue

            # danger zone: gas ramps linearly with a little noise
            gas_value = start_gas + progress * (GAS_DANGER_THRESHOLD + 15 - start_gas)
            gas_value += random.uniform(-1.5, 1.5)
            yield Reading(_now(), zone, "gas", round(gas_value, 1), "ppm")

            # temperature and pressure drift up mildly, correlated with gas
            temp_low, temp_high = SAFE_RANGES["temperature"]
            temp_value = temp_high - 3 + progress * 8 + random.uniform(-0.5, 0.5)
            yield Reading(_now(), zone, "temperature", round(temp_value, 1), "C")

            pres_low, pres_high = SAFE_RANGES["pressure"]
            pres_value = pres_high - 2 + progress * 6 + random.uniform(-0.3, 0.3)
            yield Reading(_now(), zone, "pressure", round(pres_value, 1), "kPa")

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="SafeNet sensor simulator")
    parser.add_argument("--mode", choices=["random", "scenario"], default="scenario")
    parser.add_argument("--interval", type=float, default=1.0,
                         help="seconds between reading batches")
    parser.add_argument("--ramp-seconds", type=int, default=180,
                         help="scenario mode only: total ramp duration")
    parser.add_argument("--zone", default="zone_4",
                         help="scenario mode only: which zone goes into danger")
    parser.add_argument("--limit", type=int, default=None,
                         help="random mode only: stop after N readings")
    args = parser.parse_args()

    stream = (
        run_scenario(danger_zone=args.zone, ramp_seconds=args.ramp_seconds,
                     interval=args.interval)
        if args.mode == "scenario"
        else run_random(interval=args.interval, limit=args.limit)
    )

    try:
        for reading in stream:
            print(reading.to_json())
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
