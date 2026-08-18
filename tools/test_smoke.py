"""Run fire/smoke detection on an image, video, or live stream."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse

import cv2
from ultralytics import YOLO


DEFAULT_MODEL = Path(
    r"E:\project\yw\model\烟火检测（分类）\runs\detect\train20_smoke_finetune\weights\best.pt"
)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
STREAM_SCHEMES = {"rtsp", "rtmp", "http", "https"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect fire and smoke in an image, video, or live stream."
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Image/video path or stream URL, such as rtsp://...",
    )
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="YOLO model path.")
    parser.add_argument(
        "--output",
        default=None,
        help="Output path. Images default to *_detected.<ext>; videos default to *_detected.mp4.",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.20,
        help="Detection confidence threshold. Lower values help find faint smoke.",
    )
    parser.add_argument("--imgsz", type=int, default=640, help="Inference image size.")
    parser.add_argument("--device", default="0", help="Inference device, for example 0 or cpu.")
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show annotated frames in a window. Recommended for live streams.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Stop after this many frames; 0 means process until the source ends.",
    )
    parser.add_argument(
        "--print-every",
        type=int,
        default=30,
        help="Print detection counts every N frames; 0 disables periodic output.",
    )
    return parser.parse_args()


def source_kind(source: str) -> str:
    parsed = urlparse(source)
    if parsed.scheme.lower() in STREAM_SCHEMES:
        return "stream"
    if Path(source).suffix.lower() in IMAGE_SUFFIXES:
        return "image"
    return "video"


def default_output(source: str, kind: str) -> Path | None:
    if kind == "stream":
        return None
    path = Path(source)
    suffix = path.suffix if kind == "image" else ".mp4"
    return path.with_name(f"{path.stem}_detected{suffix}")


def output_path(source: str, kind: str, requested: str | None) -> Path | None:
    if requested:
        return Path(requested)
    return default_output(source, kind)


def validate_args(args: argparse.Namespace, kind: str) -> None:
    if not Path(args.model).is_file():
        raise FileNotFoundError(f"Model not found: {args.model}")
    if not 0.0 <= args.conf <= 1.0:
        raise ValueError("--conf must be between 0 and 1")
    if args.max_frames < 0:
        raise ValueError("--max-frames must be 0 or greater")
    if args.print_every < 0:
        raise ValueError("--print-every must be 0 or greater")
    if kind != "stream" and not Path(args.source).is_file():
        raise FileNotFoundError(f"Source not found: {args.source}")
    if kind == "stream" and not args.show and not args.output:
        raise ValueError("A live stream needs --show or --output")


def detection_summary(result) -> str:
    counts: dict[str, int] = {}
    for box in result.boxes:
        class_id = int(box.cls[0])
        if isinstance(result.names, dict):
            label = str(result.names.get(class_id, class_id))
        else:
            label = str(result.names[class_id])
        counts[label] = counts.get(label, 0) + 1
    return ", ".join(f"{label}={count}" for label, count in sorted(counts.items())) or "none"


def run_image(model: YOLO, args: argparse.Namespace, output: Path | None) -> None:
    result = model.predict(
        source=args.source,
        conf=args.conf,
        imgsz=args.imgsz,
        device=args.device,
        verbose=False,
    )[0]
    annotated = result.plot(labels=True, conf=True, boxes=True)
    print(f"detections: {detection_summary(result)}")

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output), annotated):
            raise RuntimeError(f"Could not write output image: {output}")
        print(f"saved: {output}")
    if args.show:
        cv2.imshow("fire-smoke detection", annotated)
        cv2.waitKey(0)
        cv2.destroyAllWindows()


def run_video_or_stream(
    model: YOLO,
    args: argparse.Namespace,
    kind: str,
    output: Path | None,
) -> None:
    capture = cv2.VideoCapture(args.source)
    if not capture.isOpened():
        raise RuntimeError(f"Could not open source: {args.source}")

    fps = capture.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps != fps:
        fps = 25.0
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))

    writer = None

    processed = 0
    try:
        while args.max_frames == 0 or processed < args.max_frames:
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
            annotated = result.plot(labels=True, conf=True, boxes=True)

            if output and writer is None:
                height, width = annotated.shape[:2]
                output.parent.mkdir(parents=True, exist_ok=True)
                writer = cv2.VideoWriter(
                    str(output),
                    cv2.VideoWriter_fourcc(*"mp4v"),
                    fps,
                    (width, height),
                )
                if not writer.isOpened():
                    raise RuntimeError(f"Could not create output video: {output}")

            if writer:
                writer.write(annotated)
            if args.show:
                cv2.imshow("fire-smoke detection", annotated)
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    break

            processed += 1
            if args.print_every and processed % args.print_every == 0:
                total = f"/{frame_count}" if frame_count > 0 else ""
                print(f"frame {processed}{total}: {detection_summary(result)}")
    finally:
        capture.release()
        if writer:
            writer.release()
        if args.show:
            cv2.destroyAllWindows()

    if output and writer is None:
        raise RuntimeError(f"No frames were read from source: {args.source}")
    if output:
        print(f"saved: {output}")
    print(f"frames: {processed}")


def main() -> None:
    args = parse_args()
    kind = source_kind(args.source)
    validate_args(args, kind)
    output = output_path(args.source, kind, args.output)
    model = YOLO(args.model)

    print(f"source type: {kind}")
    print(f"model: {args.model}")
    print(f"confidence: {args.conf}")
    if kind == "image":
        run_image(model, args, output)
    else:
        run_video_or_stream(model, args, kind, output)


if __name__ == "__main__":
    main()
