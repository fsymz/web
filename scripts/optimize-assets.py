#!/usr/bin/env python3
"""Build deterministic, package-sized floor maps."""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
import sys

from PIL import Image


FLOOR_NAMES = tuple(f"{floor}F" for floor in range(1, 14))


class AssetOptimizationError(RuntimeError):
    """Raised when a source cannot satisfy the production asset contract."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-floor-dir", type=Path, required=True)
    parser.add_argument("--output-assets-dir", type=Path, required=True)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--quality", type=int, default=65)
    parser.add_argument("--min-quality", type=int, default=55)
    parser.add_argument("--max-quality", type=int, default=75)
    parser.add_argument("--min-width", type=int, default=800)
    parser.add_argument("--max-image-kib", type=float, default=90)
    return parser


def validate_options(
    *,
    width: int,
    quality: int,
    min_quality: int,
    max_quality: int,
    min_width: int,
    max_bytes: int,
) -> None:
    if width < min_width or min_width < 1:
        raise AssetOptimizationError("width must be at least min-width")
    if not 1 <= min_quality <= quality <= max_quality <= 95:
        raise AssetOptimizationError("quality must be inside min-quality and max-quality")
    if max_bytes < 1:
        raise AssetOptimizationError("max-image-kib must be positive")


def candidate_widths(width: int, min_width: int) -> list[int]:
    widths = list(range(width, min_width - 1, -20))
    if widths[-1] != min_width:
        widths.append(min_width)
    return widths


def candidate_qualities(quality: int, min_quality: int, max_quality: int) -> list[int]:
    return (
        list(range(max_quality, quality - 1, -1))
        + list(range(quality - 1, min_quality - 1, -1))
    )


def encode_jpeg(image: Image.Image, width: int, quality: int) -> bytes:
    height = max(1, round(image.height * width / image.width))
    resized = image.resize((width, height), Image.Resampling.LANCZOS)
    output = BytesIO()
    resized.save(
        output,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling=2,
    )
    return output.getvalue()


def optimize_floor_map(
    source: Path,
    destination: Path,
    *,
    width: int,
    quality: int,
    min_quality: int,
    max_quality: int,
    min_width: int,
    max_bytes: int,
) -> dict[str, int]:
    validate_options(
        width=width,
        quality=quality,
        min_quality=min_quality,
        max_quality=max_quality,
        min_width=min_width,
        max_bytes=max_bytes,
    )
    if not source.is_file():
        raise AssetOptimizationError(f"missing floor map: {source}")

    with Image.open(source) as opened:
        image = opened.convert("RGB")
    if image.width < min_width:
        raise AssetOptimizationError(f"source floor map is narrower than min-width: {source}")

    target_width = min(width, image.width)
    for candidate_width in candidate_widths(target_width, min_width):
        for candidate_quality in candidate_qualities(quality, min_quality, max_quality):
            encoded = encode_jpeg(image, candidate_width, candidate_quality)
            if len(encoded) > max_bytes:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(encoded)
            with Image.open(destination) as verified:
                verified.load()
                if verified.mode != "RGB" or verified.width < min_width:
                    raise AssetOptimizationError(f"invalid optimized floor map: {destination}")
                progressive = verified.info.get("progressive") or verified.info.get("progression")
                if not progressive:
                    raise AssetOptimizationError(f"floor map is not progressive: {destination}")
            return {
                "width": candidate_width,
                "height": round(image.height * candidate_width / image.width),
                "quality": candidate_quality,
                "bytes": len(encoded),
            }

    raise AssetOptimizationError(
        f"floor map cannot meet size limit without crossing minimum width: {source}"
    )


def exact_sources(directory: Path, names: tuple[str, ...], suffix: str, kind: str) -> dict[str, Path]:
    if not directory.is_dir():
        raise AssetOptimizationError(f"missing {kind} source directory: {directory}")
    expected = {f"{name}{suffix}" for name in names}
    actual = {path.name for path in directory.iterdir() if path.is_file() and path.suffix.lower() == suffix}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise AssetOptimizationError(f"unexpected {kind} source set; missing={missing}, extra={extra}")
    return {name: directory / f"{name}{suffix}" for name in names}


def remove_old_outputs(directory: Path, suffix: str) -> None:
    if not directory.exists():
        return
    for path in directory.glob(f"*{suffix}"):
        if path.is_file():
            path.unlink()


def run(args: argparse.Namespace) -> None:
    max_bytes = round(args.max_image_kib * 1024)
    validate_options(
        width=args.width,
        quality=args.quality,
        min_quality=args.min_quality,
        max_quality=args.max_quality,
        min_width=args.min_width,
        max_bytes=max_bytes,
    )
    floor_sources = exact_sources(args.source_floor_dir, FLOOR_NAMES, ".jpg", "floor map")
    floor_output = args.output_assets_dir / "floor-maps"
    remove_old_outputs(floor_output, ".jpg")

    for name in FLOOR_NAMES:
        destination = floor_output / f"{name}.jpg"
        result = optimize_floor_map(
            floor_sources[name],
            destination,
            width=args.width,
            quality=args.quality,
            min_quality=args.min_quality,
            max_quality=args.max_quality,
            min_width=args.min_width,
            max_bytes=max_bytes,
        )
        print(
            f"map {name}: {result['width']}x{result['height']} "
            f"quality={result['quality']} bytes={result['bytes']}"
        )


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        run(args)
    except (AssetOptimizationError, OSError, ValueError) as error:
        print(f"asset optimization failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
