"""
SafeNet compound risk detection engine.

This is the core novelty of the project: it doesn't just check "is gas above
X ppm" (a hard threshold, the way a traditional single-sensor alarm works).
Instead it watches for a *combination* of signals in the same zone —
rising gas trend + an active maintenance permit (and later, a worker
detected nearby via the CV module) — and fires an alert well before any
single sensor would trip its own alarm.

Run this file directly to see it consume the scripted demo scenario from
sensor_simulator.py and print both the compound alert and the traditional
hard-threshold breach, with the time gap between them — that gap is your
"detected N minutes before threshold breach" number, and it's real because
both timestamps come from the same deterministic run.
"""

import json
from collections import deque, defaultdict
from dataclasses import dataclass
from datetime import datetime

from sensor_simulator import run_scenario, GAS_DANGER_THRESHOLD

# --- Mock permit log -------------------------------------------------------
# Stands in for the real permit-log system (Day 7 on the original plan).
# Same interface a real system would expose, so swapping it out later
# doesn't require touching the risk engine itself.

class PermitLog:
    def __init__(self):
        # (zone, task, active_from_progress, active_to_progress) as a
        # fraction of the scenario's total ramp — easy to reason about
        # without hardcoding real timestamps.
        self._permits = [
            ("zone_4", "maintenance", 0.15, 0.90),
        ]

    def is_active(self, zone: str, progress: float) -> bool:
        return any(
            z == zone and start <= progress <= end
            for z, _task, start, end in self._permits
        )

    def active_task(self, zone: str, progress: float) -> str | None:
        for z, task, start, end in self._permits:
            if z == zone and start <= progress <= end:
                return task
        return None


# --- Per-zone gas trend tracking -------------------------------------------

@dataclass
class GasSample:
    timestamp: datetime
    value: float


class ZoneState:
    """Keeps a short rolling window of gas readings to compute a trend."""

    def __init__(self, window_seconds: float = 20.0):
        self.window_seconds = window_seconds
        self.samples: deque[GasSample] = deque()

    def add(self, timestamp: datetime, value: float):
        self.samples.append(GasSample(timestamp, value))
        cutoff = timestamp.timestamp() - self.window_seconds
        while self.samples and self.samples[0].timestamp.timestamp() < cutoff:
            self.samples.popleft()

    def trend_ppm_per_sec(self) -> float:
        """Rate of change over the current window. 0 if not enough data."""
        if len(self.samples) < 2:
            return 0.0
        first, last = self.samples[0], self.samples[-1]
        elapsed = last.timestamp.timestamp() - first.timestamp.timestamp()
        if elapsed <= 0:
            return 0.0
        return (last.value - first.value) / elapsed

    def latest_value(self) -> float:
        return self.samples[-1].value if self.samples else 0.0


# --- Risk engine -------------------------------------------------------------

class RiskEngine:
    def __init__(self, permit_log: PermitLog, worker_near_hazard=None):
        self.permit_log = permit_log
        # Optional callback: worker_near_hazard(zone) -> bool.
        # Left as None until the CV module is wired in; the compound
        # logic already accounts for it once it's available.
        self.worker_near_hazard = worker_near_hazard or (lambda zone: False)
        self.zone_states: dict[str, ZoneState] = defaultdict(ZoneState)

        self.compound_alert_fired = False
        self.compound_alert_timestamp: datetime | None = None
        self.hard_breach_timestamp: datetime | None = None

    def risk_score(self, zone: str, progress: float) -> float:
        """0-100 composite score for the dashboard gauge."""
        state = self.zone_states[zone]
        level_component = min(state.latest_value() / GAS_DANGER_THRESHOLD, 1.0) * 60
        trend_component = min(max(state.trend_ppm_per_sec(), 0) / 0.5, 1.0) * 15
        permit_component = 25 if self.permit_log.is_active(zone, progress) else 0
        return round(level_component + trend_component + permit_component, 1)

    def ingest_gas_reading(self, zone: str, value: float, timestamp: datetime,
                            progress: float):
        state = self.zone_states[zone]
        state.add(timestamp, value)

        # Traditional hard-threshold check (for comparison only).
        if value >= GAS_DANGER_THRESHOLD and self.hard_breach_timestamp is None:
            self.hard_breach_timestamp = timestamp

        # Compound check: rising gas + active permit in the same zone.
        # (worker_near_hazard folds in automatically once the CV module
        # provides real detections.)
        trending_up = state.trend_ppm_per_sec() > 0.3
        permit_active = self.permit_log.is_active(zone, progress)
        worker_present = self.worker_near_hazard(zone)

        is_compound_risk = (
            state.latest_value() > 30
            and trending_up
            and (permit_active or worker_present)
        )

        if is_compound_risk and not self.compound_alert_fired:
            self.compound_alert_fired = True
            self.compound_alert_timestamp = timestamp
            reason_parts = ["gas rising"]
            if permit_active:
                reason_parts.append(f"active permit ({self.permit_log.active_task(zone, progress)})")
            if worker_present:
                reason_parts.append("worker detected nearby")
            return {
                "type": "compound_risk_alert",
                "zone": zone,
                "gas_ppm": state.latest_value(),
                "trend_ppm_per_sec": round(state.trend_ppm_per_sec(), 3),
                "reason": " + ".join(reason_parts),
                "risk_score": self.risk_score(zone, progress),
                "timestamp": timestamp.isoformat(),
            }
        return None


def main():
    permit_log = PermitLog()
    engine = RiskEngine(permit_log)

    ramp_seconds = 180
    interval = 0.05  # fast for testing; matches the run_scenario call below
    total_ticks = int(ramp_seconds / interval)

    tick = 0
    for reading in run_scenario(danger_zone="zone_4", ramp_seconds=ramp_seconds,
                                 interval=interval):
        if reading.sensor_type != "gas":
            continue

        # zone_4 is the only zone with a real ramp; track progress by tick
        # count against total ticks purely for permit-window lookup.
        progress = min(tick / total_ticks, 1.0)
        ts = datetime.fromisoformat(reading.timestamp)

        alert = engine.ingest_gas_reading(reading.zone, reading.value, ts, progress)
        if alert:
            print(json.dumps(alert))

        if reading.zone == "zone_4":
            tick += 1

        if engine.compound_alert_fired and engine.hard_breach_timestamp:
            break

    print("\n--- Summary ---")
    if engine.compound_alert_timestamp and engine.hard_breach_timestamp:
        lead = (engine.hard_breach_timestamp - engine.compound_alert_timestamp).total_seconds()
        print(f"Compound alert fired at:   {engine.compound_alert_timestamp.isoformat()}")
        print(f"Hard threshold breach at:  {engine.hard_breach_timestamp.isoformat()}")
        print(f"Lead time: {lead:.1f} seconds before the traditional alarm would have fired")
    else:
        print("Scenario ended before both events occurred — try a longer ramp_seconds.")


if __name__ == "__main__":
    main()
