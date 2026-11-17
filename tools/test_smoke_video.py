"""Run YOLO fire/smoke detection on a local video and save annotated frames."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
from ultralytics import YOLO


DEFAULT_MODEL = Path(
    r"E:\project\yw\model\烟火检测（分类）\runs\detect\train20_smoke_finetune\weights\best.pt"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect fire and smoke in a video and draw labels, confidence, and boxes."
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Input video path, for example E:\\project\\yw\\video\\smoke.mp4",
    )
    parser.add_argument(
        "--model",
        default=str(DEFAULT_MODEL),
        help="YOLO model path.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output video path. Defaults to <source>_detected.mp4.",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.20,
        help="Detection confidence threshold. Lower values help find faint smoke.",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="Inference image size.",
    )
    parser.add_argument(
        "--device",
        default="0",
        help="Inference device, for example 0 or cpu.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show the annotated video while processing.",
    )
    return parser.parse_args()


def build_output_path(source: Path, output: str | None) -> Path:
    if output:
        return Path(output)
    return source.with_name(f"{source.stem}_detected.mp4")


def main() -> None:
    args = parse_args()
    source = Path(args.source)
    model_path = Path(args.model)
    output = build_output_path(source, args.output)

    if not source.is_file():
        raise FileNotFoundError(f"Input video not found: {source}")
    if not model_path.is_file():
        raise FileNotFoundError(f"Model not found: {model_path}")
    if not 0.0 <= args.conf <= 1.0:
        raise ValueError("--conf must be between 0 and 1")

    output.parent.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(model_path))
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open input video: {source}")

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = capture.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 25.0
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))

    writer = cv2.VideoWriter(
        str(output),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(f"Could not create output video: {output}")

    processed = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break

            result = model.predict(
                source=frame,
                conf=args.conf,
                imgsz=args.imgsz,
                device=args.device,
                verbose=False,
            )[0]

            # Ultralytics uses the model's class names and draws label + confidence.
            annotated = result.plot(labels=True, conf=True, boxes=True)
            writer.write(annotated)
            processed += 1

            if args.show:
                cv2.imshow("fire-smoke detection", annotated)
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    break

            if processed % 100 == 0:
                total = f"/{frame_count}" if frame_count > 0 else ""
                print(f"processed {processed}{total} frames")
    finally:
        capture.release()
        writer.release()
        if args.show:
            cv2.destroyAllWindows()

    print(f"saved: {output}")
    print(f"frames: {processed}")


if __name__ == "__main__":
    main()
