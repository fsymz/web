"""Load floor-routing safety policy and preflight source-map black components."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass
from math import hypot
from pathlib import Path
from typing import Callable, Iterator

import cv2
import numpy as np
from PIL import Image

from project_paths import resolve_floor_map, sha256_file


ALGORITHM_VERSION = "grid-a-star-visible-local-v1"
WALK_RGB = np.array([0xDC, 0xDE, 0xDD], dtype=np.int16)
WALL_RGB = np.array([0x66, 0x5B, 0x5D], dtype=np.int16)
REVIEW_STATUSES = frozenset(
    {"pending", "approved", "rejected", "siteConfirmationRequired"}
)
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
HISTOGRAM_KEYS = (
    "1",
    "2-3",
    "4-15",
    "16-63",
    "64-255",
    "256-1023",
    "1024+",
)


@dataclass(frozen=True)
class FloorRoutingPolicy:
    floor: str
    algorithm_version: str
    source_floor_map_sha256: str
    cell_size_px: int
    walk_tolerance: int
    wall_tolerance: int
    clearance_px: int
    hard_black_closing_radius_px: int
    hard_black_min_component_area_px: int
    clearance_review_status: str
    clearance_evidence_id: str
    clearance_reviewer: str
    clearance_reviewed_at: str
    max_anchor_snap_px: int
    endpoint_bridge_radius_cells: int
    local_candidate_limit: int
    local_seed_index_radius: int
    local_max_turns: int
    force_walkable_polygons: tuple[tuple[tuple[float, float], ...], ...]
    force_blocked_polygons: tuple[tuple[tuple[float, float], ...], ...]


@dataclass(frozen=True)
class RoutingSurface:
    safe_mask: np.ndarray
    raw_obstacle_mask: np.ndarray
    clearance_field: np.ndarray
    buffer_margin_field: np.ndarray
    hard_forbidden_mask: np.ndarray
    cell_size_px: int

    @property
    def resident_nbytes(self) -> int:
        return sum(
            array.nbytes
            for array in (
                self.safe_mask,
                self.raw_obstacle_mask,
                self.clearance_field,
                self.buffer_margin_field,
                self.hard_forbidden_mask,
            )
        )


@dataclass(frozen=True)
class SnapResult:
    pixel: tuple[int, int]
    distance_px: float


class FloorSurfaceCache:
    def __init__(self, max_items: int = 1):
        if max_items < 1 or max_items > 2:
            raise ValueError("surface cache capacity must be 1 or 2")
        self.max_items = max_items
        self._items: OrderedDict[str, RoutingSurface] = OrderedDict()
        self.peak_items = 0

    def get_or_build(
        self,
        key: str,
        builder: Callable[[], RoutingSurface],
    ) -> RoutingSurface:
        if key in self._items:
            value = self._items.pop(key)
            self._items[key] = value
            return value
        if len(self._items) >= self.max_items:
            raise RuntimeError(
                "evict the current floor before building another surface"
            )
        value = builder()
        self._items[key] = value
        self.peak_items = max(self.peak_items, len(self._items))
        return value

    def keys(self) -> tuple[str, ...]:
        return tuple(self._items)

    def evict(
        self,
        key: str,
        *,
        on_evict: Callable[[RoutingSurface], None] = lambda _surface: None,
    ) -> None:
        value = self._items.pop(key)
        on_evict(value)

    def clear(self) -> None:
        self._items.clear()


def _integer_field(
    raw: dict,
    defaults: dict,
    key: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if key in raw:
        value = raw[key]
    elif key in defaults:
        value = defaults[key]
    else:
        raise ValueError(f"routing policy {key} is missing")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"routing policy {key} must be a JSON integer")
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            raise ValueError(f"routing policy {key} must be at least {minimum}")
        raise ValueError(
            f"routing policy {key} must be in the inclusive range {minimum}..{maximum}"
        )
    return value


def _review_string(raw: dict, key: str) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"routing policy {key} must be a string")
    return value


def _polygons(value: object) -> tuple[tuple[tuple[float, float], ...], ...]:
    if not isinstance(value, list):
        raise ValueError("routing polygons must be arrays")
    normalized = []
    for polygon in value:
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise ValueError("routing polygon must contain at least three points")
        normalized_polygon = []
        for point in polygon:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("routing polygon point must be an array of two numbers")
            coordinates = []
            for coordinate in point:
                if (
                    isinstance(coordinate, bool)
                    or not isinstance(coordinate, (int, float))
                    or not math.isfinite(coordinate)
                    or coordinate < 0
                    or coordinate > 100
                ):
                    raise ValueError(
                        "routing polygon coordinates must be finite numbers in 0..100"
                    )
                coordinates.append(float(coordinate))
            normalized_polygon.append((coordinates[0], coordinates[1]))
        normalized.append(tuple(normalized_polygon))
    return tuple(normalized)


def _validated_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"routing policy {label} must be a 64-character hexadecimal hash")
    return value.lower()


def load_floor_policy(
    document: object,
    floor: str,
    source_sha256: str,
) -> FloorRoutingPolicy:
    if (
        not isinstance(document, dict)
        or isinstance(document.get("schemaVersion"), bool)
        or document.get("schemaVersion") != 1
    ):
        raise ValueError("unsupported routing policy schema")
    if document.get("algorithmVersion") != ALGORITHM_VERSION:
        raise ValueError(
            f"routing policy algorithmVersion must equal {ALGORITHM_VERSION!r}"
        )
    floors = document.get("floors")
    raw = floors.get(floor) if isinstance(floors, dict) else None
    if not isinstance(raw, dict):
        raise ValueError(f"missing routing policy for {floor}")
    expected_hash = _validated_hash(
        raw.get("sourceFloorMapSha256"),
        "sourceFloorMapSha256",
    )
    actual_hash = _validated_hash(source_sha256, "source SHA-256")
    if expected_hash != actual_hash:
        raise ValueError(f"{floor}: source map hash mismatch")
    defaults = document.get("defaults")
    if not isinstance(defaults, dict):
        raise ValueError("routing policy defaults are missing")

    clearance_review_status = raw.get("clearanceReviewStatus")
    if clearance_review_status not in REVIEW_STATUSES:
        raise ValueError(
            "routing policy clearanceReviewStatus must be one of: "
            + ", ".join(sorted(REVIEW_STATUSES))
        )

    return FloorRoutingPolicy(
        floor=floor,
        algorithm_version=ALGORITHM_VERSION,
        source_floor_map_sha256=expected_hash,
        cell_size_px=_integer_field(raw, defaults, "cellSizePx", 1),
        walk_tolerance=_integer_field(raw, defaults, "walkTolerance", 0, 255),
        wall_tolerance=_integer_field(raw, defaults, "wallTolerance", 0, 255),
        clearance_px=_integer_field(raw, defaults, "clearancePx", 1),
        hard_black_closing_radius_px=_integer_field(
            raw,
            defaults,
            "hardBlackClosingRadiusPx",
            0,
            10,
        ),
        hard_black_min_component_area_px=_integer_field(
            raw,
            defaults,
            "hardBlackMinComponentAreaPx",
            1,
            100000,
        ),
        clearance_review_status=clearance_review_status,
        clearance_evidence_id=_review_string(raw, "clearanceEvidenceId"),
        clearance_reviewer=_review_string(raw, "clearanceReviewer"),
        clearance_reviewed_at=_review_string(raw, "clearanceReviewedAt"),
        max_anchor_snap_px=_integer_field(raw, defaults, "maxAnchorSnapPx", 1),
        endpoint_bridge_radius_cells=_integer_field(
            raw,
            defaults,
            "endpointBridgeRadiusCells",
            1,
            16,
        ),
        local_candidate_limit=_integer_field(
            raw,
            defaults,
            "localCandidateLimit",
            3,
            96,
        ),
        local_seed_index_radius=_integer_field(
            raw,
            defaults,
            "localSeedIndexRadius",
            1,
            64,
        ),
        local_max_turns=_integer_field(raw, defaults, "localMaxTurns", 1, 8),
        force_walkable_polygons=_polygons(raw.get("forceWalkablePolygons", [])),
        force_blocked_polygons=_polygons(raw.get("forceBlockedPolygons", [])),
    )


def _histogram_key(area: int) -> str:
    if area == 1:
        return "1"
    if area <= 3:
        return "2-3"
    if area <= 15:
        return "4-15"
    if area <= 63:
        return "16-63"
    if area <= 255:
        return "64-255"
    if area <= 1023:
        return "256-1023"
    return "1024+"


def _validate_rgb_image(image: np.ndarray) -> None:
    if (
        not isinstance(image, np.ndarray)
        or image.dtype != np.uint8
        or image.ndim != 3
        or image.shape[2] != 3
    ):
        raise ValueError("image must be an HxWx3 uint8 array")


def _validate_closing_radius(closing_radius_px: int) -> None:
    if (
        isinstance(closing_radius_px, bool)
        or not isinstance(closing_radius_px, int)
        or not 0 <= closing_radius_px <= 10
    ):
        raise ValueError("closing_radius_px must be a JSON integer in 0..10")


def _near_black_masks(
    image: np.ndarray,
    closing_radius_px: int,
) -> tuple[np.ndarray, np.ndarray]:
    raw_near_black = cv2.inRange(
        image,
        np.array([0, 0, 0], dtype=np.uint8),
        np.array([45, 45, 45], dtype=np.uint8),
    )
    if closing_radius_px:
        diameter = closing_radius_px * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (diameter, diameter))
        component_mask = cv2.morphologyEx(
            raw_near_black,
            cv2.MORPH_CLOSE,
            kernel,
        )
    else:
        component_mask = raw_near_black
    return raw_near_black, component_mask


def near_black_component_report(
    image: np.ndarray,
    closing_radius_px: int,
) -> dict[str, object]:
    _validate_rgb_image(image)
    _validate_closing_radius(closing_radius_px)

    raw_near_black, component_mask = _near_black_masks(
        image,
        closing_radius_px,
    )
    raw_count = int(cv2.countNonZero(raw_near_black))

    count_with_background, _, stats, _ = cv2.connectedComponentsWithStats(
        component_mask,
        connectivity=8,
    )
    areas = [int(area) for area in stats[1:, cv2.CC_STAT_AREA]]
    histogram = {key: 0 for key in HISTOGRAM_KEYS}
    for area in areas:
        histogram[_histogram_key(area)] += 1
    return {
        "rawNearBlackPixelCount": raw_count,
        "connectedComponentCount": count_with_background - 1,
        "componentAreaHistogram": histogram,
    }


def _polygon_pixels(
    polygon: tuple[tuple[float, float], ...],
    width: int,
    height: int,
) -> np.ndarray:
    return np.asarray(
        [
            [
                max(0, min(width - 1, round(x / 100 * (width - 1)))),
                max(0, min(height - 1, round(y / 100 * (height - 1)))),
            ]
            for x, y in polygon
        ],
        dtype=np.int32,
    )


def build_routing_surface(
    image: np.ndarray,
    policy: FloorRoutingPolicy,
) -> RoutingSurface:
    _validate_rgb_image(image)
    pixels = image[:, :, :3]
    walkable = cv2.inRange(
        pixels,
        np.clip(WALK_RGB - policy.walk_tolerance, 0, 255).astype(np.uint8),
        np.clip(WALK_RGB + policy.walk_tolerance, 0, 255).astype(np.uint8),
    ) > 0
    wall = cv2.inRange(
        pixels,
        np.clip(WALL_RGB - policy.wall_tolerance, 0, 255).astype(np.uint8),
        np.clip(WALL_RGB + policy.wall_tolerance, 0, 255).astype(np.uint8),
    ) > 0
    near_black_raw, near_black_components = _near_black_masks(
        pixels,
        policy.hard_black_closing_radius_px,
    )
    _, labels, stats, _ = cv2.connectedComponentsWithStats(
        near_black_components,
        connectivity=8,
    )
    accepted_labels = (
        stats[:, cv2.CC_STAT_AREA] >= policy.hard_black_min_component_area_px
    )
    accepted_labels[0] = False
    hard_black = accepted_labels[labels]
    hard_forbidden = np.logical_or(wall, hard_black)
    del (
        accepted_labels,
        labels,
        stats,
        near_black_components,
        near_black_raw,
        hard_black,
        wall,
    )

    height, width = walkable.shape
    for polygon in policy.force_walkable_polygons:
        proposed = np.zeros_like(walkable, dtype=np.uint8)
        cv2.fillPoly(proposed, [_polygon_pixels(polygon, width, height)], 1)
        walkable |= (proposed > 0) & ~hard_forbidden
    for polygon in policy.force_blocked_polygons:
        blocked = np.zeros_like(walkable, dtype=np.uint8)
        cv2.fillPoly(blocked, [_polygon_pixels(polygon, width, height)], 1)
        hard_forbidden |= blocked > 0
        walkable &= blocked == 0

    raw_obstacle = np.logical_or(~walkable, hard_forbidden).astype(np.uint8)
    clearance_field = cv2.distanceTransform(
        (raw_obstacle == 0).astype(np.uint8),
        cv2.DIST_L2,
        5,
    )
    buffered_obstacle = raw_obstacle.copy()
    if policy.clearance_px > 0:
        radius = policy.clearance_px
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (radius * 2 + 1, radius * 2 + 1),
        )
        buffered_obstacle = cv2.dilate(buffered_obstacle, kernel)
    safe_mask = buffered_obstacle == 0
    buffer_margin_field = cv2.distanceTransform(
        safe_mask.astype(np.uint8),
        cv2.DIST_L2,
        5,
    )
    return RoutingSurface(
        safe_mask=safe_mask,
        raw_obstacle_mask=raw_obstacle.astype(bool),
        clearance_field=clearance_field,
        buffer_margin_field=buffer_margin_field,
        hard_forbidden_mask=hard_forbidden,
        cell_size_px=policy.cell_size_px,
    )


def iter_supercover_pixels(
    start: tuple[int, int],
    end: tuple[int, int],
) -> Iterator[tuple[int, int]]:
    x0, y0 = start
    x1, y1 = end
    dx = x1 - x0
    dy = y1 - y0
    steps = max(abs(dx), abs(dy), 1)
    seen: set[tuple[int, int]] = set()
    previous: tuple[int, int] | None = None
    for index in range(steps + 1):
        ratio = index / steps
        x = round(x0 + dx * ratio)
        y = round(y0 + dy * ratio)
        candidates = ((x, y),)
        if index and dx and dy:
            assert previous is not None
            candidates = (
                previous,
                (x, previous[1]),
                (previous[0], y),
                (x, y),
            )
        for pixel in candidates:
            if pixel not in seen:
                seen.add(pixel)
                previous = pixel
                yield pixel


def supercover_pixels(
    start: tuple[int, int],
    end: tuple[int, int],
) -> tuple[tuple[int, int], ...]:
    return tuple(iter_supercover_pixels(start, end))


def line_is_safe(
    surface: RoutingSurface,
    start: tuple[int, int],
    end: tuple[int, int],
) -> bool:
    height, width = surface.safe_mask.shape
    for x, y in iter_supercover_pixels(start, end):
        if x < 0 or y < 0 or x >= width or y >= height:
            return False
        if not bool(surface.safe_mask[y, x]):
            return False
    return True


def snap_anchor(
    surface: RoutingSurface,
    point_percent: tuple[float, float],
    *,
    max_distance_px: int,
) -> SnapResult | None:
    height, width = surface.safe_mask.shape
    origin_x = round(point_percent[0] / 100 * (width - 1))
    origin_y = round(point_percent[1] / 100 * (height - 1))
    best: tuple[float, int, int] | None = None
    for y in range(
        max(0, origin_y - max_distance_px),
        min(height, origin_y + max_distance_px + 1),
    ):
        for x in range(
            max(0, origin_x - max_distance_px),
            min(width, origin_x + max_distance_px + 1),
        ):
            if not surface.safe_mask[y, x]:
                continue
            distance = hypot(x - origin_x, y - origin_y)
            if distance > max_distance_px:
                continue
            candidate = (distance, y, x)
            if best is None or candidate < best:
                best = candidate
    return None if best is None else SnapResult((best[2], best[1]), best[0])


def _source_map_preflight(document: object, floor_map_dir: Path) -> dict[str, object]:
    floors = []
    for floor_number in range(1, 14):
        floor = f"{floor_number}楼"
        source_path = resolve_floor_map(floor_map_dir, floor_number)
        source_hash = sha256_file(source_path)
        policy = load_floor_policy(document, floor, source_hash)
        with Image.open(source_path) as source_image:
            image = np.asarray(source_image.convert("RGB"), dtype=np.uint8)
            report = near_black_component_report(
                image,
                policy.hard_black_closing_radius_px,
            )
        floors.append(
            {
                "floor": floor,
                "sourceFloorMapSha256": source_hash,
                **report,
            }
        )
        del image
    return {"schemaVersion": 1, "floors": floors}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--floor-map-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.preflight:
        parser.error("--preflight is required")
    document = json.loads(args.policy.read_text(encoding="utf-8"))
    report = _source_map_preflight(document, args.floor_map_dir)
    serialized = json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n"
    sys.stdout.buffer.write(serialized.encode("utf-8"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
