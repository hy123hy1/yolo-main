"""Package one local OM for the existing model upload API; no hardware or DB required."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


COCO_CLASSES = (
    "person,bicycle,car,motorcycle,airplane,bus,train,truck,boat,traffic light,fire hydrant,"
    "stop sign,parking meter,bench,bird,cat,dog,horse,sheep,cow,elephant,bear,zebra,giraffe,"
    "backpack,umbrella,handbag,tie,suitcase,frisbee,skis,snowboard,sports ball,kite,baseball bat,"
    "baseball glove,skateboard,surfboard,tennis racket,bottle,wine glass,cup,fork,knife,spoon,"
    "bowl,banana,apple,sandwich,orange,broccoli,carrot,hot dog,pizza,donut,cake,chair,couch,"
    "potted plant,bed,dining table,toilet,tv,laptop,mouse,remote,keyboard,cell phone,microwave,"
    "oven,toaster,sink,refrigerator,book,clock,vase,scissors,teddy bear,hair drier,toothbrush"
).split(",")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    classes = parser.add_mutually_exclusive_group(required=True)
    classes.add_argument("--classes", type=Path, help="JSON array of class names in export order")
    classes.add_argument("--coco", action="store_true", help="Explicitly assume standard COCO-80 class order")
    parser.add_argument("--layout", choices=["channels_first", "candidates_first"], default="channels_first")
    parser.add_argument("--dtype", choices=["float32", "float16"], default="float32")
    args = parser.parse_args()
    if args.model.suffix.lower() != ".om" or not args.model.is_file():
        parser.error("--model must be an existing .om file")
    names = COCO_CLASSES if args.coco else json.loads(args.classes.read_text(encoding="utf-8"))
    if not isinstance(names, list) or not names or any(not isinstance(name, str) or not name.strip() for name in names):
        parser.error("--classes must contain a non-empty JSON array of names")
    with args.model.open("rb") as source:
        source_hash = hashlib.file_digest(source, "sha256").hexdigest()
    metadata = {
        "name": args.model.stem, "classes": names,
        "weights_path": f"weights/{args.model.name}", "model_format": "om",
        "inference": {"output_layout": args.layout, "input_dtype": args.dtype,
                      "iou_threshold": 0.7, "max_detections": 300},
        "source_sha256": source_hash,
        "hardware_validated": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "x", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("model.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        package.write(args.model, metadata["weights_path"])
    print(f"Created {args.output.resolve()}; classes={len(names)}, sha256={metadata['source_sha256']}")
    print("Packaging does NOT verify OM target SoC, class semantics or hardware execution.")


if __name__ == "__main__":
    main()
