"""Shared, side-effect-free helpers for offline navigation audit CLIs."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Iterable


SCRIPT_VERSION = "1.0.0"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FLOOR_DIR = (
    PROJECT_ROOT.parent
    / "放入院内导航页面目录下"
    / "放入images文件夹"
    / "floor-maps"
)
WALL_RGB = (0x66, 0x5B, 0x5D)


def resolve_path(value: str | Path | None, default: Path) -> Path:
    candidate = default if value is None else Path(value).expanduser()
    return candidate.resolve()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_commonjs_json(path: Path) -> Any:
    text = path.read_text(encoding="utf-8-sig").strip()
    while text.startswith("//"):
        _, separator, text = text.partition("\n")
        if not separator:
            break
        text = text.lstrip()
    prefix = "module.exports ="
    if not text.startswith(prefix):
        raise ValueError(f"{path} is not a JSON-compatible CommonJS export")
    body = text[len(prefix) :].strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    return json.loads(body)


def parse_floor(value: object) -> int:
    match = re.search(r"(\d+)", str(value or ""))
    if not match:
        raise ValueError(f"cannot parse floor from {value!r}")
    floor = int(match.group(1))
    if floor < 1 or floor > 13:
        raise ValueError(f"unsupported floor: {value!r}")
    return floor


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_input_path(path: Path, project_root: Path) -> str:
    return Path(os.path.relpath(path.resolve(), project_root.resolve())).as_posix()


def input_metadata(paths: Iterable[Path], project_root: Path) -> list[dict[str, str]]:
    unique = sorted({path.resolve() for path in paths}, key=lambda item: str(item).casefold())
    return [
        {
            "path": relative_input_path(path, project_root),
            "sha256": sha256_file(path),
        }
        for path in unique
    ]


def create_report_dir(path: Path) -> Path:
    target = path.resolve()
    if target.exists():
        raise FileExistsError(f"report directory already exists: {target}")
    target.mkdir(parents=True, exist_ok=False)
    return target


def metadata_document(script_name: str, inputs: list[dict[str, str]]) -> dict[str, object]:
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "generatedAtUtc": generated,
        "script": script_name,
        "scriptVersion": SCRIPT_VERSION,
        "inputs": inputs,
    }


def write_metadata(
    report_dir: Path,
    script_name: str,
    inputs: list[dict[str, str]],
) -> None:
    (report_dir / "metadata.json").write_text(
        json.dumps(metadata_document(script_name, inputs), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def point_valid(point: object) -> bool:
    return (
        isinstance(point, list)
        and len(point) == 2
        and all(
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
            for value in point
        )
        and all(0 <= value <= 100 for value in point)
    )


def image_size_valid(image_size: object) -> bool:
    return (
        isinstance(image_size, list)
        and len(image_size) == 2
        and all(
            not isinstance(value, bool)
            and isinstance(value, int)
            and value > 1
            for value in image_size
        )
    )


def point_to_map_pixel(
    point: object,
    image_size: object,
    *,
    label: str = "point",
) -> tuple[int, int]:
    if not point_valid(point):
        raise ValueError(f"invalid {label}: {point!r}")
    if not image_size_valid(image_size):
        raise ValueError(f"invalid imageSize: {image_size!r}")
    width, height = image_size
    return (
        round(float(point[0]) / 100 * (width - 1)),
        round(float(point[1]) / 100 * (height - 1)),
    )


def endpoint_pixel_distance(
    actual: object,
    expected: object,
    image_size: object,
    *,
    actual_label: str = "route endpoint",
    expected_label: str = "semantic endpoint",
) -> float:
    actual_pixel = point_to_map_pixel(actual, image_size, label=actual_label)
    expected_pixel = point_to_map_pixel(expected, image_size, label=expected_label)
    return math.hypot(
        actual_pixel[0] - expected_pixel[0],
        actual_pixel[1] - expected_pixel[1],
    )


def department_semantic_endpoint(
    department: object,
) -> tuple[list[float], str]:
    if not isinstance(department, dict) or not point_valid(department.get("anchor")):
        raise ValueError("invalid department anchor")
    door_approach = department.get("doorApproachPoint")
    if door_approach is None:
        return department["anchor"], "anchor"
    if not point_valid(door_approach):
        raise ValueError("invalid department doorApproachPoint")
    return door_approach, "doorApproachPoint"


def department_endpoint_residual(
    actual: object,
    department: object,
    image_size: object,
    max_anchor_snap_px: object,
) -> tuple[float, int, str]:
    if (
        isinstance(max_anchor_snap_px, bool)
        or not isinstance(max_anchor_snap_px, int)
        or max_anchor_snap_px < 1
    ):
        raise ValueError("invalid routing policy maxAnchorSnapPx")
    semantic, endpoint_type = department_semantic_endpoint(department)
    residual = endpoint_pixel_distance(
        actual,
        semantic,
        image_size,
        expected_label=endpoint_type,
    )
    tolerance = 1 if endpoint_type == "doorApproachPoint" else max_anchor_snap_px
    return residual, tolerance, endpoint_type


def max_anchor_snap_px_for_floor(document: object, floor: object) -> int:
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise ValueError("invalid routing policy schema")
    defaults = document.get("defaults")
    floors = document.get("floors")
    floor_policy = floors.get(floor) if isinstance(floors, dict) else None
    if not isinstance(defaults, dict) or not isinstance(floor_policy, dict):
        raise ValueError(f"missing routing policy for {floor!r}")
    value = floor_policy.get("maxAnchorSnapPx", defaults.get("maxAnchorSnapPx"))
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"invalid routing policy maxAnchorSnapPx for {floor!r}")
    return value


def point_distance(left: list[float], right: list[float]) -> float:
    return math.hypot(float(left[0]) - float(right[0]), float(left[1]) - float(right[1]))


def route_length(points: list[list[float]], image_size: list[float]) -> float:
    if not image_size_valid(image_size):
        raise ValueError(f"invalid imageSize: {image_size!r}")
    aspect = float(image_size[1]) / float(image_size[0])
    return sum(
        math.hypot(
            float(end[0]) - float(start[0]),
            (float(end[1]) - float(start[1])) * aspect,
        )
        for start, end in zip(points, points[1:])
    )


def floor_map_path(floor_dir: Path, floor: int) -> Path:
    for name in (f"{floor}F.jpg", f"{floor}F.jpeg", f"{floor}f.jpg", f"{floor}f.jpeg"):
        candidate = floor_dir / name
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"missing source floor map {floor}F in {floor_dir}")
