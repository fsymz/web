#!/usr/bin/env python3
"""Safely merge redundant points in existing verified route geometries.

The authoritative high-resolution source maps are not stored in this repository.
This post-processor therefore keeps every route endpoint and every route field,
uses the packaged map only after scaling spatial policy values to its dimensions,
and applies a shortcut only when that shortcut is safe and does not reduce the
route's minimum clearance on the scaled runtime-map audit surface. If the scaled
surface cannot reproduce the existing route, only exactly collinear points are
removed; that operation leaves the geometric locus unchanged.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image

from project_paths import load_json, project_root, sha256_file
from route_provenance import current_route_provenance, render_commonjs_export
from routing_surface import build_routing_surface, load_floor_policy
from safe_path_solver import SolveContext, SolverDiagnostics


ROUTE_FILES = (
    ("sameFloorPaths.js", "canonical same-floor routes"),
    ("floorNavPaths.js", "per-shaft elevator navigation paths"),
)
SPATIAL_PIXEL_FIELDS = (
    "cellSizePx",
    "clearancePx",
    "hardBlackClosingRadiusPx",
    "maxAnchorSnapPx",
)
AREA_PIXEL_FIELDS = ("hardBlackMinComponentAreaPx",)


def strict_json(text: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(
        text,
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )


def load_commonjs(path: Path) -> dict[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig").strip()
    marker = "module.exports ="
    if marker not in text:
        raise ValueError(f"{path}: missing CommonJS export")
    body = text.split(marker, 1)[1].strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    value = strict_json(body)
    if not isinstance(value, dict):
        raise TypeError(f"{path}: export must be an object")
    if any(not isinstance(key, str) or not isinstance(item, dict) for key, item in value.items()):
        raise TypeError(f"{path}: route keys must map to objects")
    return value


def normalized_points(value: object, *, label: str) -> list[list[float | int]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label}: points must be a non-empty array")
    result: list[list[float | int]] = []
    for index, point in enumerate(value):
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"{label}: points[{index}] must contain two numbers")
        pair: list[float | int] = []
        for component in point:
            if (
                isinstance(component, bool)
                or not isinstance(component, (int, float))
                or not math.isfinite(float(component))
                or not 0 <= float(component) <= 100
            ):
                raise ValueError(f"{label}: points[{index}] is outside 0..100")
            pair.append(component)
        if not result or pair != result[-1]:
            result.append(pair)
    return result


def canonical_points(
    points: Sequence[Sequence[float | int]],
) -> tuple[list[list[float | int]], bool]:
    forward_points = [list(point) for point in points]
    reverse_points = [list(point) for point in reversed(points)]
    forward = json.dumps(forward_points, ensure_ascii=False, separators=(",", ":"))
    reverse = json.dumps(reverse_points, ensure_ascii=False, separators=(",", ":"))
    if reverse < forward:
        return reverse_points, True
    return forward_points, False


def canonical_geometry_hash(points: Sequence[Sequence[float | int]]) -> str:
    forward = json.dumps(points, ensure_ascii=False, separators=(",", ":"))
    reverse = json.dumps(list(reversed(points)), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(min(forward, reverse).encode("utf-8")).hexdigest()


def image_size(record: dict[str, Any], *, label: str) -> tuple[int, int]:
    value = record.get("imageSize")
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) or item <= 1 for item in value)
    ):
        raise ValueError(f"{label}: imageSize must contain two integers above one")
    return int(value[0]), int(value[1])


def floor_map_path(floor_dir: Path, record: dict[str, Any]) -> Path:
    image_name = Path(str(record.get("image") or "")).name
    candidate = floor_dir / image_name
    if image_name and candidate.is_file():
        return candidate
    digits = "".join(character for character in str(record.get("floor") or "") if character.isdigit())
    if digits:
        for suffix in ("F.jpg", "F.jpeg", "f.jpg", "f.jpeg"):
            candidate = floor_dir / f"{digits}{suffix}"
            if candidate.is_file():
                return candidate
    raise FileNotFoundError(f"missing packaged map for {record.get('floor')!r}")


def points_to_float_pixels(
    points: Sequence[Sequence[float | int]],
    size: tuple[int, int],
) -> list[tuple[float, float]]:
    width, height = size
    return [
        (
            float(point[0]) / 100 * (width - 1),
            float(point[1]) / 100 * (height - 1),
        )
        for point in points
    ]


def points_to_rounded_pixels(
    points: Sequence[Sequence[float | int]],
    size: tuple[int, int],
) -> list[tuple[int, int]]:
    width, height = size
    return [
        (
            max(0, min(width - 1, round(float(point[0]) / 100 * (width - 1)))),
            max(0, min(height - 1, round(float(point[1]) / 100 * (height - 1)))),
        )
        for point in points
    ]


def route_metrics(
    points: Sequence[Sequence[float | int]],
    size: tuple[int, int],
    *,
    turn_angle_degrees: float,
) -> dict[str, Any]:
    if len(points) < 2:
        return {
            "routeLength": 0.0,
            "effectiveTurnCount": 0,
            "shortestSegment": 0.0,
            "semanticPointIndexes": [0] if points else [],
            "geometrySha256": canonical_geometry_hash(points),
        }
    width, height = size
    aspect = height / width
    scaled = [(float(point[0]), float(point[1]) * aspect) for point in points]
    lengths = [
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in zip(scaled, scaled[1:])
    ]
    turn_indexes: list[int] = []
    for index, (left, middle, right) in enumerate(zip(scaled, scaled[1:], scaled[2:]), start=1):
        ax, ay = middle[0] - left[0], middle[1] - left[1]
        bx, by = right[0] - middle[0], right[1] - middle[1]
        if (ax == 0 and ay == 0) or (bx == 0 and by == 0):
            continue
        angle = math.degrees(math.atan2(abs(ax * by - ay * bx), ax * bx + ay * by))
        if angle >= turn_angle_degrees:
            turn_indexes.append(index)
    return {
        "routeLength": round(sum(lengths), 6),
        "effectiveTurnCount": len(turn_indexes),
        "shortestSegment": round(min(lengths), 6),
        "semanticPointIndexes": [0, *turn_indexes, len(points) - 1],
        "geometrySha256": canonical_geometry_hash(points),
    }


def point_to_segment_distance(
    point: tuple[float, float],
    left: tuple[float, float],
    right: tuple[float, float],
) -> tuple[float, float]:
    dx, dy = right[0] - left[0], right[1] - left[1]
    denominator = dx * dx + dy * dy
    if denominator == 0:
        return math.hypot(point[0] - left[0], point[1] - left[1]), 0.0
    projection = ((point[0] - left[0]) * dx + (point[1] - left[1]) * dy) / denominator
    closest = (left[0] + projection * dx, left[1] + projection * dy)
    return math.hypot(point[0] - closest[0], point[1] - closest[1]), projection


def same_geometric_locus(
    pixels: Sequence[tuple[float, float]],
    start: int,
    end: int,
    *,
    tolerance_px: float = 1e-7,
) -> bool:
    left, right = pixels[start], pixels[end]
    if left == right:
        return False
    previous_projection = -float("inf")
    for point in pixels[start : end + 1]:
        distance, projection = point_to_segment_distance(point, left, right)
        if distance > tolerance_px:
            return False
        if projection < -tolerance_px or projection > 1 + tolerance_px:
            return False
        if projection + tolerance_px < previous_projection:
            return False
        previous_projection = projection
    return True


def collapse_collinear_indices(
    points: Sequence[Sequence[float | int]],
    size: tuple[int, int],
) -> list[int]:
    if len(points) <= 2:
        return list(range(len(points)))
    pixels = points_to_float_pixels(points, size)
    indexes = [0]
    current = 0
    goal = len(points) - 1
    while current < goal:
        selected = current + 1
        for candidate in range(goal, current, -1):
            if same_geometric_locus(pixels, current, candidate):
                selected = candidate
                break
        indexes.append(selected)
        current = selected
    return indexes


def policy_value(document: dict[str, Any], floor: str, key: str) -> int:
    defaults = document.get("defaults")
    floors = document.get("floors")
    raw = floors.get(floor) if isinstance(floors, dict) else None
    if not isinstance(defaults, dict) or not isinstance(raw, dict):
        raise ValueError(f"missing routing policy for {floor}")
    value = raw[key] if key in raw else defaults.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"routing policy {floor}.{key} must be an integer")
    return value


def scaled_policy_document(
    document: dict[str, Any],
    floor: str,
    *,
    source_size: tuple[int, int],
    packaged_size: tuple[int, int],
    packaged_hash: str,
) -> dict[str, Any]:
    scaled = copy.deepcopy(document)
    floors = scaled.get("floors")
    raw = floors.get(floor) if isinstance(floors, dict) else None
    if not isinstance(raw, dict):
        raise ValueError(f"missing routing policy for {floor}")
    source_width, source_height = source_size
    packaged_width, packaged_height = packaged_size
    scale_x = (packaged_width - 1) / (source_width - 1)
    scale_y = (packaged_height - 1) / (source_height - 1)
    spatial_scale = min(scale_x, scale_y)
    area_scale = scale_x * scale_y
    raw["sourceFloorMapSha256"] = packaged_hash
    for key in SPATIAL_PIXEL_FIELDS:
        original = policy_value(document, floor, key)
        minimum = 0 if key == "hardBlackClosingRadiusPx" else 1
        raw[key] = max(minimum, round(original * spatial_scale))
    for key in AREA_PIXEL_FIELDS:
        original = policy_value(document, floor, key)
        raw[key] = max(1, round(original * area_scale))
    return scaled


def build_scaled_surface(
    routing_document: dict[str, Any],
    floor: str,
    map_path: Path,
    source_size: tuple[int, int],
) -> tuple[np.ndarray, Any]:
    with Image.open(map_path) as source:
        image = np.asarray(source.convert("RGB"))
    packaged_size = (int(image.shape[1]), int(image.shape[0]))
    packaged_hash = sha256_file(map_path)
    policy_document = scaled_policy_document(
        routing_document,
        floor,
        source_size=source_size,
        packaged_size=packaged_size,
        packaged_hash=packaged_hash,
    )
    policy = load_floor_policy(policy_document, floor, packaged_hash)
    return image, build_routing_surface(image, policy)


def edges_are_safe(context: SolveContext, pixels: Sequence[tuple[int, int]]) -> bool:
    return len(pixels) >= 2 and all(
        left != right and context.segment_is_safe(left, right)
        for left, right in zip(pixels, pixels[1:])
    )


def route_clearance(context: SolveContext, pixels: Sequence[tuple[int, int]]) -> float:
    return min(
        context.segment_clearance(left, right)
        for left, right in zip(pixels, pixels[1:])
    )


def simplify_on_scaled_surface(
    points: Sequence[Sequence[float | int]],
    *,
    source_size: tuple[int, int],
    packaged_size: tuple[int, int],
    context: SolveContext,
) -> tuple[list[int], dict[str, Any]]:
    base_indices = collapse_collinear_indices(points, source_size)
    base_points = [points[index] for index in base_indices]
    packaged_pixels = points_to_rounded_pixels(base_points, packaged_size)
    audit = {
        "runtimeMapReproduced": False,
        "baselineRuntimeClearancePx": None,
        "candidateRuntimeClearancePx": None,
        "mode": "collinear-only",
    }
    if not edges_are_safe(context, packaged_pixels):
        return base_indices, audit

    baseline_clearance = route_clearance(context, packaged_pixels)
    audit["runtimeMapReproduced"] = True
    audit["baselineRuntimeClearancePx"] = round(baseline_clearance, 6)
    selected_positions = [0]
    current = 0
    goal = len(base_points) - 1
    while current < goal:
        selected = current + 1
        for candidate in range(goal, current, -1):
            left, right = packaged_pixels[current], packaged_pixels[candidate]
            if left == right or not context.segment_is_safe(left, right):
                continue
            if context.segment_clearance(left, right) + 1e-6 < baseline_clearance:
                continue
            selected = candidate
            break
        selected_positions.append(selected)
        current = selected

    candidate_indices = [base_indices[position] for position in selected_positions]
    candidate_points = [points[index] for index in candidate_indices]
    candidate_pixels = points_to_rounded_pixels(candidate_points, packaged_size)
    if not edges_are_safe(context, candidate_pixels):
        return base_indices, audit
    candidate_clearance = route_clearance(context, candidate_pixels)
    if candidate_clearance + 1e-6 < baseline_clearance:
        return base_indices, audit
    audit["candidateRuntimeClearancePx"] = round(candidate_clearance, 6)
    audit["mode"] = "scaled-runtime-map-clearance-preserving"
    return candidate_indices, audit


def update_record(
    record: dict[str, Any],
    points: Sequence[Sequence[float | int]],
    *,
    turn_angle_degrees: float,
) -> dict[str, Any]:
    updated = dict(record)
    copied_points = [list(point) for point in points]
    metrics = route_metrics(
        copied_points,
        image_size(record, label=str(record.get("floor") or "route")),
        turn_angle_degrees=turn_angle_degrees,
    )
    updated["points"] = copied_points
    updated.update(metrics)
    if copied_points != record.get("points"):
        updated["solverQualityStatus"] = "optimized"
    return updated


def simplify_route_file(
    records: dict[str, dict[str, Any]],
    *,
    routing_document: dict[str, Any],
    floor_dir: Path,
    turn_angle_degrees: float,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    grouped: dict[
        tuple[str, str, tuple[int, int], str],
        list[tuple[str, dict[str, Any], bool]],
    ] = {}
    for key, record in records.items():
        points = normalized_points(record.get("points"), label=key)
        canonical, reversed_from_record = canonical_points(points)
        size = image_size(record, label=key)
        signature = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
        group_key = (
            str(record.get("floor") or ""),
            str(record.get("image") or ""),
            size,
            signature,
        )
        grouped.setdefault(group_key, []).append((key, record, reversed_from_record))

    output: dict[str, dict[str, Any]] = {}
    surface_cache: dict[tuple[str, Path, tuple[int, int]], tuple[np.ndarray, Any]] = {}
    summary: dict[str, Any] = {
        "routeCount": len(records),
        "uniqueGeometryCount": len(grouped),
        "changedRouteCount": 0,
        "changedUniqueGeometryCount": 0,
        "runtimeMapReproducedGeometryCount": 0,
        "runtimeMapInconclusiveGeometryCount": 0,
        "scaledMapSimplifiedGeometryCount": 0,
        "collinearOnlySimplifiedGeometryCount": 0,
        "pointsBefore": 0,
        "pointsAfter": 0,
        "turnsBefore": 0,
        "turnsAfter": 0,
        "endpointRegressionCount": 0,
        "lengthRegressionCount": 0,
        "turnRegressionCount": 0,
        "runtimeClearanceRegressionCount": 0,
        "rejectedRegressionGeometryCount": 0,
        "geometryAudits": [],
        "failures": [],
    }

    for (floor, _image_name, source_size, signature), members in sorted(grouped.items()):
        canonical = strict_json(signature)
        representative = members[0][1]
        if len(canonical) < 2 or representative.get("coLocated") is True:
            candidate = [list(point) for point in canonical]
            audit = {
                "mode": "co-located",
                "runtimeMapReproduced": False,
                "baselineRuntimeClearancePx": None,
                "candidateRuntimeClearancePx": None,
            }
        else:
            map_path = floor_map_path(floor_dir, representative).resolve()
            cache_key = (floor, map_path, source_size)
            if cache_key not in surface_cache:
                surface_cache[cache_key] = build_scaled_surface(
                    routing_document,
                    floor,
                    map_path,
                    source_size,
                )
            image, surface = surface_cache[cache_key]
            packaged_size = (int(image.shape[1]), int(image.shape[0]))
            context = SolveContext(surface, SolverDiagnostics())
            candidate_indexes, audit = simplify_on_scaled_surface(
                canonical,
                source_size=source_size,
                packaged_size=packaged_size,
                context=context,
            )
            candidate = [list(canonical[index]) for index in candidate_indexes]
            if audit["runtimeMapReproduced"]:
                summary["runtimeMapReproducedGeometryCount"] += 1
            else:
                summary["runtimeMapInconclusiveGeometryCount"] += 1
            if len(candidate) < len(canonical):
                if audit["mode"] == "scaled-runtime-map-clearance-preserving":
                    summary["scaledMapSimplifiedGeometryCount"] += 1
                else:
                    summary["collinearOnlySimplifiedGeometryCount"] += 1

        before_metrics = route_metrics(
            canonical,
            source_size,
            turn_angle_degrees=turn_angle_degrees,
        )
        after_metrics = route_metrics(
            candidate,
            source_size,
            turn_angle_degrees=turn_angle_degrees,
        )
        rejection_reasons: list[str] = []
        if candidate[0] != canonical[0] or candidate[-1] != canonical[-1]:
            summary["endpointRegressionCount"] += 1
            rejection_reasons.append("endpoint")
        if after_metrics["routeLength"] > before_metrics["routeLength"] + 0.00001:
            summary["lengthRegressionCount"] += 1
            rejection_reasons.append("length")
        if after_metrics["effectiveTurnCount"] > before_metrics["effectiveTurnCount"]:
            summary["turnRegressionCount"] += 1
            rejection_reasons.append("turn-count")
        if (
            audit.get("runtimeMapReproduced")
            and audit.get("candidateRuntimeClearancePx") is not None
            and audit.get("baselineRuntimeClearancePx") is not None
            and float(audit["candidateRuntimeClearancePx"]) + 1e-6
            < float(audit["baselineRuntimeClearancePx"])
        ):
            summary["runtimeClearanceRegressionCount"] += 1
            rejection_reasons.append("runtime-clearance")
        if rejection_reasons:
            summary["rejectedRegressionGeometryCount"] += 1
            candidate = [list(point) for point in canonical]
            after_metrics = before_metrics
            audit["mode"] = "rejected-" + "-".join(rejection_reasons)
            audit["candidateRuntimeClearancePx"] = audit.get("baselineRuntimeClearancePx")

        changed_geometry = candidate != canonical
        if changed_geometry:
            summary["changedUniqueGeometryCount"] += 1

        summary["geometryAudits"].append(
            {
                "floor": floor,
                "representativeKey": members[0][0],
                "routeCount": len(members),
                "pointsBefore": len(canonical),
                "pointsAfter": len(candidate),
                "turnsBefore": before_metrics["effectiveTurnCount"],
                "turnsAfter": after_metrics["effectiveTurnCount"],
                **audit,
            }
        )

        for key, record, reversed_from_record in members:
            points_before = normalized_points(record.get("points"), label=key)
            oriented = [list(point) for point in (reversed(candidate) if reversed_from_record else candidate)]
            updated = update_record(
                record,
                oriented,
                turn_angle_degrees=turn_angle_degrees,
            )
            output[key] = updated
            before_oriented_metrics = route_metrics(
                points_before,
                image_size(record, label=key),
                turn_angle_degrees=turn_angle_degrees,
            )
            summary["pointsBefore"] += len(points_before)
            summary["pointsAfter"] += len(oriented)
            summary["turnsBefore"] += before_oriented_metrics["effectiveTurnCount"]
            summary["turnsAfter"] += int(updated.get("effectiveTurnCount") or 0)
            if oriented != points_before:
                summary["changedRouteCount"] += 1

    summary["pointsRemoved"] = summary["pointsBefore"] - summary["pointsAfter"]
    summary["turnsRemoved"] = summary["turnsBefore"] - summary["turnsAfter"]
    if summary["changedRouteCount"] and summary["pointsRemoved"] <= 0:
        summary["failures"].append("route geometry changed without removing points")
    summary["failures"] = sorted(set(summary["failures"]))
    return output, summary


def build_parser() -> argparse.ArgumentParser:
    root = project_root(__file__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--route-dir",
        type=Path,
        default=root / "miniprogram" / "data",
    )
    parser.add_argument(
        "--floor-dir",
        type=Path,
        default=root / "miniprogram" / "assets" / "floor-maps",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = project_root(__file__)
    routing_document = load_json(root / "config" / "routing-policy.json")
    navigation_document = load_json(root / "config" / "navigation-policy.json")
    if not isinstance(routing_document, dict) or not isinstance(navigation_document, dict):
        raise ValueError("routing and navigation policies must be objects")
    turn_angle_degrees = float(navigation_document.get("turnAngleDegrees", 25))
    provenance = current_route_provenance(root)
    combined_summary: dict[str, Any] = {
        "schemaVersion": 2,
        "mode": "existing-route-scaled-runtime-map-clearance-preserving-merge",
        "routeFiles": {},
        "failures": [],
    }

    for filename, description in ROUTE_FILES:
        path = args.route_dir.resolve() / filename
        records = load_commonjs(path)
        simplified, summary = simplify_route_file(
            records,
            routing_document=routing_document,
            floor_dir=args.floor_dir.resolve(),
            turn_angle_degrees=turn_angle_degrees,
        )
        content = render_commonjs_export(
            simplified,
            description,
            provenance=provenance,
        )
        combined_summary["routeFiles"][filename] = summary
        combined_summary["failures"].extend(summary["failures"])
        if args.check:
            if path.read_text(encoding="utf-8") != content:
                combined_summary["failures"].append(f"{filename}: simplification output is stale")
        else:
            path.write_text(content, encoding="utf-8", newline="\n")

    combined_summary["failures"] = sorted(set(combined_summary["failures"]))
    if args.report is not None:
        report_path = args.report.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(combined_summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(combined_summary, ensure_ascii=False, indent=2))
    return 1 if combined_summary["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
