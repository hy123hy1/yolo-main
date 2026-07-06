#!/usr/bin/env python3
"""Split a YOLO images/labels dataset into train/valid/test folders."""

from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Randomly split a YOLO dataset containing images/ and labels/."
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Source split directory, e.g. Fire-Detection-1/train.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory. Defaults to <dataset_root>/split_80_10_10.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--valid-ratio", type=float, default=0.1)
    parser.add_argument(
        "--move",
        action="store_true",
        help="Move files instead of copying them.",
    )
    return parser.parse_args()


def collect_pairs(source: Path) -> list[tuple[Path, Path]]:
    images_dir = source / "images"
    labels_dir = source / "labels"
    if not images_dir.is_dir():
        raise FileNotFoundError(f"Missing images directory: {images_dir}")
    if not labels_dir.is_dir():
        raise FileNotFoundError(f"Missing labels directory: {labels_dir}")

    pairs: list[tuple[Path, Path]] = []
    for image in sorted(images_dir.iterdir()):
        if not image.is_file() or image.suffix.lower() not in IMAGE_EXTS:
            continue
        label = labels_dir / f"{image.stem}.txt"
        if not label.is_file():
            raise FileNotFoundError(f"Missing label for image: {image}")
        pairs.append((image, label))
    if not pairs:
        raise RuntimeError(f"No image/label pairs found in: {source}")
    return pairs


def prepare_output(output: Path) -> None:
    for split in ("train", "valid", "test"):
        (output / split / "images").mkdir(parents=True, exist_ok=True)
        (output / split / "labels").mkdir(parents=True, exist_ok=True)


def copy_or_move(src: Path, dst: Path, move: bool) -> None:
    if move:
        shutil.move(str(src), str(dst))
    else:
        shutil.copy2(src, dst)


def main() -> None:
    args = parse_args()
    source = args.source.resolve()
    output = args.output or source.parent / "split_80_10_10"
    output = output.resolve()

    test_ratio = 1.0 - args.train_ratio - args.valid_ratio
    if args.train_ratio <= 0 or args.valid_ratio < 0 or test_ratio < 0:
        raise ValueError("Ratios must be valid and sum to no more than 1.")

    pairs = collect_pairs(source)
    random.Random(args.seed).shuffle(pairs)

    train_count = int(len(pairs) * args.train_ratio)
    valid_count = int(len(pairs) * args.valid_ratio)
    split_ranges = {
        "train": pairs[:train_count],
        "valid": pairs[train_count : train_count + valid_count],
        "test": pairs[train_count + valid_count :],
    }

    prepare_output(output)
    for split, split_pairs in split_ranges.items():
        for image, label in split_pairs:
            copy_or_move(image, output / split / "images" / image.name, args.move)
            copy_or_move(label, output / split / "labels" / label.name, args.move)

    for split, split_pairs in split_ranges.items():
        print(f"{split}: {len(split_pairs)} images, {len(split_pairs)} labels")
    print(f"output: {output}")


if __name__ == "__main__":
    main()
