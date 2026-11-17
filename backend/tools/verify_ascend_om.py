"""Standalone target-host check before starting Workers; never writes the business DB."""
import argparse
import json
import logging
from pathlib import Path
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.inference.ascend_om import AscendOmDetector


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--conf", type=float, default=.25)
    parser.add_argument("--repeat", type=int, default=10)
    args = parser.parse_args()
    if args.repeat < 1 or args.repeat > 1000:
        parser.error("--repeat must be in [1,1000]")
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    frame = cv2.imdecode(np.fromfile(str(args.image), dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        parser.error("Cannot decode --image")
    detector = AscendOmDetector(args.model, metadata, args.device)
    try:
        detector.detect(frame, args.conf)
        started = time.perf_counter()
        for _ in range(args.repeat):
            detections = detector.detect(frame, args.conf)
        elapsed_ms = (time.perf_counter() - started) * 1000 / args.repeat
    finally:
        detector.close()
    for item in detections:
        x1, y1, x2, y2 = [int(item[key]) for key in ("x1", "y1", "x2", "y2")]
        cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 160, 0), 2)
        cv2.putText(frame, f"{item['class_name']} {item['conf']:.2f}", (x1, max(15, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 160, 0), 2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(args.output), frame):
        raise RuntimeError(f"Cannot write {args.output}")
    print(json.dumps({"device": args.device, "repeat": args.repeat, "mean_ms_including_prepost": elapsed_ms,
                      "detections": detections, "output": str(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
