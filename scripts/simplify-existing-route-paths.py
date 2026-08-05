#!/usr/bin/env python3
"""Safely merge redundant points in existing verified route geometries.

The repository does not contain the original authoritative floor-map files used by
full route generation. This post-processor therefore starts from the existing
verified routes and only removes intermediate points when the packaged runtime
map confirms that the replacement segment is walkable and has no lower packaged-
map clearance than the original route geometry.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image

from project_paths import load_json, project_root, sha256_file
from route_provenance import current_route_provenance, render_commonjs_export
from routing_surface import build_routing_surface, load_floor_policy
from safe_path_solver import SolveContext, SolverDiagnostics, _quality


ROUTE_FILES = (
    ("sameFloorPaths.js", "canonical same-floor routes"),
    ("floorNavPaths.js", "per-shaft elevator navigation paths"),
)


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
    return value


def normalized_points(value: object, *, label: str) -> list[list[float]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label}: points must be a non-empty array")
    result: list[list[float]] = []
    for index, point in enumerate(value):
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"{label}: points[{index}] must contain two numbers")
        pair = [float(point[0]), float(point[1])]
        if any(not math.isfinite(number) or not 0 <= number <= 100 for number in pair):
            raise ValueError(f"{label}: points[{index}] is outside 0..100")
        if not result or result[-1] != pair:
            result.append(pair)
    return result


def canonical_points(points: Sequence[Sequence[float]]) -> tuple[list[list[float]], bool]:
    forward = json.dumps(points, ensure_ascii=False, separators=(",", ":"))
    reversed_points = [list(point) for point in reversed(points)]
    reverse = json.dumps(reversed_points, ensure_ascii=False, separators=(",", ":"))
    if reverse < forward:
        return reversed_points, True
    return [list(point) for point in points], False


def percent_to_pixels(
    points: Sequence[Sequence[float]],
    width: int,
    height: int,
) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for point in points:
        pixel = (
            max(0, min(width - 1, round(float(point[0]) / 100 * (width - 1)))),
            max(0, min(height - 1, round(float(point[1]) / 100 * (height - 1)))),
        )
        if not result or result[-1] != pixel:
            result.append(pixel)
    return result


def route_clearance(context: SolveContext, pixels: Sequence[tuple[int, int]]) -> float:
    return min(
        context.segment_clearance(left, right)
        for left, right in zip(pixels, pixels[1:])
    )


def edges_are_safe(context: SolveContext, pixels: Sequence[tuple[int, int]]) -> bool:
    return all(
        context.segment_is_safe(left, right)
        for left, right in zip(pixels, pixels[1:])
    )


def simplify_pixels(
    context: SolveContext,
    pixels: Sequence[tuple[int, int]],
    *,
    minimum_clearance_px: float,
) -> list[tuple[int, int]]:
    if len(pixels) <= 2:
        return list(pixels)
    result = [pixels[0]]
    current = 0
    goal = len(pixels) - 1
    epsilon = 1e-6
    while current < goal:
        selected = current + 1
        for candidate in range(goal, current, -1):
            if not context.segment_is_safe(pixels[current], pixels[candidate]):
                continue
            if (
                context.segment_clearance(pixels[current], pixels[candidate]) + epsilon
                < minimum_clearance_px
            ):
                continue
            selected = candidate
            break
        result.append(pixels[selected])
        current = selected
    return result


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


def build_surface_for_packaged_map(
    routing_document: dict[str, Any],
    floor: str,
    map_path: Path,
) -> tuple[np.ndarray, Any]:
    with Image.open(map_path) as source:
        image = np.asarray(source.convert("RGB"))
    audit_document = copy.deepcopy(routing_document)
    floors = audit_document.get("floors")
    if not isinstance(floors, dict) or not isinstance(floors.get(floor), dict):
        raise ValueError(f"missing routing policy for {floor}")
    actual_hash = sha256_file(map_path)
    floors[floor]["sourceFloorMapSha256"] = actual_hash
    policy = load_floor_policy(audit_document, floor, actual_hash)
    return image, build_routing_surface(image, policy)


def update_record(
    record: dict[str, Any],
    *,
    surface: Any,
    pixels: Sequence[tuple[int, int]],
    turn_angle_degrees: float,
) -> dict[str, Any]:
    percentages, lengths, turns, packaged_clearance, semantic_indexes, geometry_hash = _quality(
        surface,
        pixels,
        turn_angle_degrees=turn_angle_degrees,
    )
    previous_clearance = float(record.get("minClearancePx") or packaged_clearance)
    conservative_clearance = min(previous_clearance, packaged_clearance)
    updated = dict(record)
    updated["geometrySha256"] = geometry_hash
    updated["solverQualityStatus"] = "optimized"
    updated["points"] = [list(point) for point in percentages]
    updated["routeLength"] = round(sum(lengths), 6)
    updated["minClearancePx"] = round(conservative_clearance, 3)
    image_size = updated.get("imageSize")
    if isinstance(image_size, list) and len(image_size) == 2 and float(image_size[0]) > 0:
        updated["minClearanceImageWidthPercent"] = round(
            conservative_clearance / float(image_size[0]) * 100,
            6,
        )
    updated["effectiveTurnCount"] = turns
    updated["shortestSegment"] = round(min(lengths), 6)
    updated["semanticPointIndexes"] = list(semantic_indexes)
    return updated


def simplify_route_file(
    records: dict[str, dict[str, Any]],
    *,
    routing_document: dict[str, Any],
    floor_dir: Path,
    turn_angle_degrees: float,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[tuple[str, dict[str, Any], bool]]] = {}
    for key, record in records.items():
        points = normalized_points(record.get("points"), label=key)
        canonical, reversed_from_record = canonical_points(points)
        signature = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
        group_key = (
            str(record.get("floor") or ""),
            str(record.get("image") or ""),
            signature,
        )
        grouped.setdefault(group_key, []).append((key, record, reversed_from_record))

    output: dict[str, dict[str, Any]] = {}
    surface_cache: dict[tuple[str, Path], tuple[np.ndarray, Any]] = {}
    summary = {
        "routeCount": len(records),
        "uniqueGeometryCount": len(grouped),
        "changedRouteCount": 0,
        "changedUniqueGeometryCount": 0,
        "pointsBefore": 0,
        "pointsAfter": 0,
        "turnsBefore": 0,
        "turnsAfter": 0,
        "unsafeOriginalGeometryCount": 0,
        "serializedSafetyFailureCount": 0,
        "clearanceRegressionCount": 0,
        "lengthRegressionCount": 0,
        "turnRegressionCount": 0,
        "failures": [],
    }

    for (floor, _image, signature), members in sorted(grouped.items()):
        canonical = strict_json(signature)
        representative = members[0][1]
        if len(canonical) < 2 or representative.get("coLocated") is True:
            for key, record, _reversed in members:
                output[key] = dict(record)
                summary["pointsBefore"] += len(normalized_points(record.get("points"), label=key))
                summary["pointsAfter"] += len(normalized_points(record.get("points"), label=key))
            continue

        map_path = floor_map_path(floor_dir, representative).resolve()
        cache_key = (floor, map_path)
        if cache_key not in surface_cache:
            surface_cache[cache_key] = build_surface_for_packaged_map(
                routing_document,
                floor,
                map_path,
            )
        image, surface = surface_cache[cache_key]
        height, width = image.shape[:2]
        original_pixels = percent_to_pixels(canonical, width, height)
        context = SolveContext(surface, SolverDiagnostics())
        original_quality = _quality(
            surface,
            original_pixels,
            turn_angle_degrees=turn_angle_degrees,
        )
        original_length = sum(original_quality[1])
        original_turns = original_quality[2]
        summary["turnsBefore"] += original_turns * len(members)

        if not edges_are_safe(context, original_pixels):
            summary["unsafeOriginalGeometryCount"] += 1
            summary["failures"].append(
                f"{floor}:{members[0][0]} original geometry is unsafe on packaged map"
            )
            simplified_pixels = list(original_pixels)
        else:
            original_clearance = route_clearance(context, original_pixels)
            simplified_pixels = simplify_pixels(
                context,
                original_pixels,
                minimum_clearance_px=original_clearance,
            )
            simplified_quality = _quality(
                surface,
                simplified_pixels,
                turn_angle_degrees=turn_angle_degrees,
            )
            simplified_length = sum(simplified_quality[1])
            simplified_turns = simplified_quality[2]
            simplified_clearance = simplified_quality[3]
            if simplified_length > original_length + 1e-6:
                summary["lengthRegressionCount"] += 1
                summary["failures"].append(f"{floor}:{members[0][0]} length increased")
                simplified_pixels = list(original_pixels)
            elif simplified_turns > original_turns:
                summary["turnRegressionCount"] += 1
                summary["failures"].append(f"{floor}:{members[0][0]} turns increased")
                simplified_pixels = list(original_pixels)
            elif simplified_clearance + 1e-6 < original_clearance:
                summary["clearanceRegressionCount"] += 1
                summary["failures"].append(f"{floor}:{members[0][0]} clearance decreased")
                simplified_pixels = list(original_pixels)

        simplified_quality = _quality(
            surface,
            simplified_pixels,
            turn_angle_degrees=turn_angle_degrees,
        )
        serialized_pixels = percent_to_pixels(simplified_quality[0], width, height)
        if not edges_are_safe(SolveContext(surface, SolverDiagnostics()), serialized_pixels):
            summary["serializedSafetyFailureCount"] += 1
            summary["failures"].append(
                f"{floor}:{members[0][0]} serialized simplified geometry is unsafe"
            )
            simplified_pixels = list(original_pixels)

        canonical_updated = update_record(
            representative,
            surface=surface,
            pixels=simplified_pixels,
            turn_angle_degrees=turn_angle_degrees,
        )
        canonical_updated_points = canonical_updated["points"]
        changed_geometry = canonical_updated_points != canonical
        if changed_geometry:
            summary["changedUniqueGeometryCount"] += 1

        for key, record, reversed_from_record in members:
            points_before = normalized_points(record.get("points"), label=key)
            updated = dict(canonical_updated)
            if reversed_from_record:
                updated["points"] = [list(point) for point in reversed(canonical_updated_points)]
                indexes = canonical_updated["semanticPointIndexes"]
                updated["semanticPointIndexes"] = [
                    len(canonical_updated_points) - 1 - index
                    for index in reversed(indexes)
                ]
            else:
                updated["points"] = [list(point) for point in canonical_updated_points]
                updated["semanticPointIndexes"] = list(canonical_updated["semanticPointIndexes"])
            output[key] = updated
            summary["pointsBefore"] += len(points_before)
            summary["pointsAfter"] += len(updated["points"])
            summary["turnsAfter"] += int(updated.get("effectiveTurnCount") or 0)
            if updated["points"] != points_before:
                summary["changedRouteCount"] += 1

    summary["pointsRemoved"] = summary["pointsBefore"] - summary["pointsAfter"]
    summary["turnsRemoved"] = summary["turnsBefore"] - summary["turnsAfter"]
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
        "schemaVersion": 1,
        "mode": "existing-route-packaged-map-safe-merge",
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
        args.report.resolve().parent.mkdir(parents=True, exist_ok=True)
        args.report.resolve().write_text(
            json.dumps(combined_summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(combined_summary, ensure_ascii=False, indent=2))
    return 1 if combined_summary["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
