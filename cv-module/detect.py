"""
SafeNet CV module — PPE and worker detection.

Runs the pretrained YOLOv8n PPE-detection model on a video, draws
annotated bounding boxes (green for compliant, red for violations),
overlays a "hazard zone" rectangle, and flags any frame where a person
or a PPE violation overlaps that zone.

This produces two things:
  1. An annotated video you can show judges directly (output/annotated.mp4)
  2. A JSON log of zone events (output/detections.json) that the backend
     can use to feed risk_engine.py's worker_near_hazard(zone) hook.

Run:
    python detect.py --video data/demo_video.mp4

The hazard zone defaults to the center-right of the frame — after your
first run, open output/annotated.mp4, check whether the dashed zone
rectangle actually covers where your worker walks, and adjust with
--zone-x1/--zone-y1/--zone-x2/--zone-y2 (as fractions of frame width/
height, 0.0-1.0) if it doesn't line up.
"""

import argparse
import json
import os

import cv2
from ultralytics import YOLO

# Classes from the pretrained model that count as "a person is present"
# or "a person is present without required PPE" for zone-risk purposes.
PERSON_CLASSES = {"Person"}
VIOLATION_CLASSES = {"NO-Hardhat", "NO-Safety Vest"}
COMPLIANT_CLASSES = {"Hardhat", "Safety Vest", "Mask"}

# BGR colors (OpenCV convention)
COLOR_VIOLATION = (0, 0, 255)     # red
COLOR_COMPLIANT = (0, 200, 0)     # green
COLOR_PERSON = (255, 200, 0)      # cyan-ish
COLOR_OTHER = (150, 150, 150)     # gray
COLOR_ZONE = (0, 165, 255)        # orange


def box_overlaps_zone(box, zone_px):
    """box and zone_px are both (x1, y1, x2, y2) in pixel coordinates."""
    bx1, by1, bx2, by2 = box
    zx1, zy1, zx2, zy2 = zone_px
    return not (bx2 < zx1 or bx1 > zx2 or by2 < zy1 or by1 > zy2)


def color_for_class(name):
    if name in VIOLATION_CLASSES:
        return COLOR_VIOLATION
    if name in COMPLIANT_CLASSES:
        return COLOR_COMPLIANT
    if name in PERSON_CLASSES:
        return COLOR_PERSON
    return COLOR_OTHER


def run(video_path, model_path, output_dir, zone_frac, conf_threshold=0.4):
    model = YOLO(model_path)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 15
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    zone_px = (
        int(zone_frac[0] * width), int(zone_frac[1] * height),
        int(zone_frac[2] * width), int(zone_frac[3] * height),
    )

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "annotated.mp4")

    # avc1 (H.264) is what browsers need for inline <video> playback.
    # mp4v (MPEG-4 Part 2) plays fine in VLC/native players but Chrome/
    # Firefox/Edge will reject it with MEDIA_ERR_SRC_NOT_SUPPORTED. Try
    # avc1 first — works out of the box on Windows via Media Foundation —
    # and fall back with a loud warning if this system's OpenCV build
    # can't encode it, so the failure is obvious immediately instead of
    # surfacing later as a silent black video in the browser.
    writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"avc1"), fps, (width, height))
    if not writer.isOpened():
        print("WARNING: H.264 (avc1) encoder unavailable on this system — "
              "falling back to mp4v. This video will NOT play inline in "
              "browsers (Chrome/Firefox/Edge); you'll need to transcode it "
              "with ffmpeg afterward: "
              "ffmpeg -i output/annotated.mp4 -c:v libx264 -pix_fmt yuv420p output/annotated_h264.mp4")
        writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))

    events = []
    frame_idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        results = model(frame, verbose=False, conf=conf_threshold)[0]

        zone_person_present = False
        zone_violation = None

        # Dashed zone rectangle (drawn as short segments so it reads as
        # "zone marker", not a real detection box).
        zx1, zy1, zx2, zy2 = zone_px
        for x in range(zx1, zx2, 20):
            cv2.line(frame, (x, zy1), (x + 10, zy1), COLOR_ZONE, 2)
            cv2.line(frame, (x, zy2), (x + 10, zy2), COLOR_ZONE, 2)
        for y in range(zy1, zy2, 20):
            cv2.line(frame, (zx1, y), (zx1, y + 10), COLOR_ZONE, 2)
            cv2.line(frame, (zx2, y), (zx2, y + 10), COLOR_ZONE, 2)
        cv2.putText(frame, "HAZARD ZONE", (zx1, zy1 - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_ZONE, 2)

        for box in results.boxes:
            cls_id = int(box.cls[0])
            name = model.names[cls_id]
            conf = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0])

            color = color_for_class(name)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, f"{name} {conf:.2f}", (x1, max(y1 - 6, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            if name in PERSON_CLASSES or name in VIOLATION_CLASSES:
                if box_overlaps_zone((x1, y1, x2, y2), zone_px):
                    zone_person_present = True
                    if name in VIOLATION_CLASSES:
                        zone_violation = name

        if zone_person_present:
            events.append({
                "frame": frame_idx,
                "time_sec": round(frame_idx / fps, 2),
                "worker_in_zone": True,
                "violation": zone_violation,
            })
            banner_color = COLOR_VIOLATION if zone_violation else COLOR_PERSON
            banner_text = "WORKER IN HAZARD ZONE" + (f" — {zone_violation}" if zone_violation else "")
            cv2.rectangle(frame, (0, 0), (width, 34), banner_color, -1)
            cv2.putText(frame, banner_text, (10, 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        writer.write(frame)
        frame_idx += 1

    cap.release()
    writer.release()

    events_path = os.path.join(output_dir, "detections.json")
    with open(events_path, "w") as f:
        json.dump({
            "video": video_path,
            "total_frames": frame_idx,
            "fps": fps,
            "zone_fraction": zone_frac,
            "events": events,
        }, f, indent=2)

    print(f"Processed {frame_idx} frames.")
    print(f"Annotated video: {out_path}")
    print(f"Detection log:   {events_path}")
    print(f"Worker-in-zone events: {len(events)}")
    if events:
        first = events[0]
        print(f"First zone entry at t={first['time_sec']}s (frame {first['frame']})")


def parse_args():
    p = argparse.ArgumentParser(description="SafeNet CV module — PPE + zone detection")
    p.add_argument("--video", default="data/demo_video.mp4")
    p.add_argument("--model", default="best.pt")
    p.add_argument("--output-dir", default="output")
    p.add_argument("--conf", type=float, default=0.4, help="detection confidence threshold")
    # Hazard zone as fractions of frame width/height (0.0-1.0), so it
    # works regardless of the video's actual resolution.
    p.add_argument("--zone-x1", type=float, default=0.35)
    p.add_argument("--zone-y1", type=float, default=0.15)
    p.add_argument("--zone-x2", type=float, default=0.95)
    p.add_argument("--zone-y2", type=float, default=0.95)
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        video_path=args.video,
        model_path=args.model,
        output_dir=args.output_dir,
        zone_frac=(args.zone_x1, args.zone_y1, args.zone_x2, args.zone_y2),
        conf_threshold=args.conf,
    )
