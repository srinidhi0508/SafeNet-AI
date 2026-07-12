"""
CV detection timeline lookup.

Loads output/detections.json from the CV module (produced by
cv-module/detect.py) and exposes a function that answers "is a worker in
the hazard zone right now?" given elapsed seconds since the demo started.

The dashboard's <video> element plays the annotated clip on loop, so this
lookup also loops (modulo video duration) — keeping the backend's notion
of "worker in zone" in sync with what's visually playing on screen,
without needing frame-accurate video sync over the network.
"""

import json
from pathlib import Path

CV_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "cv-module" / "output"
DETECTIONS_PATH = CV_OUTPUT_DIR / "detections.json"

_cv_data = None
_event_by_frame = {}
_video_duration = 0.0
_fps = 1.0


def _load():
    global _cv_data, _event_by_frame, _video_duration, _fps
    if _cv_data is not None:
        return

    with open(DETECTIONS_PATH) as f:
        _cv_data = json.load(f)

    _fps = _cv_data["fps"]
    _video_duration = _cv_data["total_frames"] / _fps
    _event_by_frame = {e["frame"]: e["violation"] for e in _cv_data["events"]}


def cv_available() -> bool:
    return DETECTIONS_PATH.exists()


def video_duration_seconds() -> float:
    _load()
    return _video_duration


def worker_status_at(elapsed_seconds: float):
    """
    Returns (worker_in_zone: bool, violation: str|None) for the video
    position corresponding to elapsed_seconds since the demo started,
    looping back to the start once the video's duration is exceeded —
    matching the <video loop> behavior on the dashboard.
    """
    _load()
    if _video_duration <= 0:
        return False, None

    looped_time = elapsed_seconds % _video_duration
    frame = int(looped_time * _fps)

    if frame in _event_by_frame:
        return True, _event_by_frame[frame]
    return False, None
