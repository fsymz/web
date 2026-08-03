"""Read-only audit of route endpoints, geometry, walls, and shaft continuity."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from audit_common import (
    DEFAULT_FLOOR_DIR,
    PROJECT_ROOT,
    WALL_RGB,
    create_report_dir,
    department_semantic_endpoint,
    endpoint_pixel_distance,
    floor_map_path,
    image_size_valid,
    input_metadata,
    load_commonjs_json,
    load_json,
    parse_floor,
    point_to_map_pixel,
    point_valid,
    resolve_path,
    route_length,
    sha256_file,
    write_csv,
    write_json,
    write_metadata,
)
from routing_surface import build_routing_surface, load_floor_policy


LENGTH_TOLERANCE = 0.00001
COLOCATED_ANCHOR_TOLERANCE = 0.6


def wall_hits(
    points: list[list[float]],
    image: Image.Image,
) -> int:
    width, height = image.size
    pixels = image.load()
    hits: set[tuple[int, int]] = set()
    for start, end in zip(points, points[1:]):
        x1 = max(0, min(width - 1, round(float(start[0]) / 100 * width)))
        y1 = max(0, min(height - 1, round(float(start[1]) / 100 * height)))
        x2 = max(0, min(width - 1, round(float(end[0]) / 100 * width)))
        y2 = max(0, min(height - 1, round(float(end[1]) / 100 * height)))
        steps = max(abs(x2 - x1), abs(y2 - y1), 1)
        for index in range(steps + 1):
            ratio = index / steps
            x = round(x1 + (x2 - x1) * ratio)
            y = round(y1 + (y2 - y1) * ratio)
            if pixels[x, y][:3] == WALL_RGB:
                hits.add((x, y))
    return len(hits)


def add_issue(issues: list[dict[str, object]], kind: str, key: str, message: str) -> None:
    issues.append({"type": kind, "key": key, "message": message})


def validate_record(
    kind: str,
    key: str,
    item: dict[str, Any],
    expected_start: list[float],
    expected_end: list[float],
    start_tolerance_px: int,
    end_tolerance_px: int,
    start_endpoint_type: str,
    end_endpoint_type: str,
    floor: int,
    images: dict[int, Image.Image],
    safe_masks: dict[int, np.ndarray],
    source_hashes: dict[int, str],
    issues: list[dict[str, object]],
    *,
    co_located: bool = False,
) -> None:
    points = item.get("points")
    minimum = 1 if co_located else 2
    if not isinstance(points, list) or len(points) < minimum or not all(point_valid(p) for p in points):
        add_issue(issues, kind, key, "invalid points")
        return
    if parse_floor(item.get("floor")) != floor:
        add_issue(issues, kind, key, "floor mismatch")
    if item.get("routeLengthUnit") != "imageWidthPercent":
        add_issue(issues, kind, key, "invalid routeLengthUnit")
    image_size = item.get("imageSize")
    if not image_size_valid(image_size):
        add_issue(issues, kind, key, f"invalid imageSize: {image_size!r}")
        return
    if tuple(image_size) != images[floor].size:
        add_issue(
            issues,
            kind,
            key,
            f"imageSize {image_size!r} does not match floor map {list(images[floor].size)!r}",
        )
        return
    try:
        start_residual = endpoint_pixel_distance(
            points[0],
            expected_start,
            image_size,
            expected_label=start_endpoint_type,
        )
        if start_residual > start_tolerance_px:
            add_issue(
                issues,
                kind,
                key,
                f"start {start_endpoint_type} residual {start_residual:.3f} px "
                f"exceeds {start_tolerance_px} px",
            )
        if not co_located:
            end_residual = endpoint_pixel_distance(
                points[-1],
                expected_end,
                image_size,
                expected_label=end_endpoint_type,
            )
            if end_residual > end_tolerance_px:
                add_issue(
                    issues,
                    kind,
                    key,
                    f"end {end_endpoint_type} residual {end_residual:.3f} px "
                    f"exceeds {end_tolerance_px} px",
                )
    except (TypeError, ValueError) as error:
        add_issue(issues, kind, key, f"invalid endpoint contract: {error}")
    try:
        actual_length = route_length(points, image_size)
    except (TypeError, ValueError) as error:
        add_issue(issues, kind, key, f"invalid imageSize: {error}")
        actual_length = -1
    stored_length = item.get("routeLength")
    if not isinstance(stored_length, (int, float)) or abs(actual_length - stored_length) > LENGTH_TOLERANCE:
        add_issue(issues, kind, key, "aspect-correct routeLength mismatch")
    if co_located:
        if item.get("coLocated") is not True or stored_length != 0:
            add_issue(issues, kind, key, "invalid coLocated record")
        destination_distance = route_length([points[-1], expected_end], image_size)
        anchor_distance = route_length([expected_start, expected_end], image_size)
        if (
            len(points) != 1
            or destination_distance > COLOCATED_ANCHOR_TOLERANCE
            or anchor_distance > COLOCATED_ANCHOR_TOLERANCE
        ):
            add_issue(
                issues,
                kind,
                key,
                "coLocated destination anchor is outside the 0.6 image-width-percent tolerance",
            )
    else:
        safe_mask = safe_masks[floor]
        for label, point in (("start", points[0]), ("end", points[-1])):
            x, y = point_to_map_pixel(point, image_size, label=f"{label} route endpoint")
            if not bool(safe_mask[y, x]):
                add_issue(
                    issues,
                    kind,
                    key,
                    f"{label} endpoint is outside routing policy safe_mask at pixel ({x}, {y})",
                )
        count = wall_hits(points, images[floor])
        if count:
            add_issue(issues, kind, key, f"crosses {count} exact #665B5D wall pixel(s)")
    if item.get("sourceFloorMapSha256") != source_hashes[floor]:
        add_issue(issues, kind, key, "source floor-map SHA-256 mismatch")


def audit(project_root: Path, floor_dir: Path) -> tuple[dict[str, object], list[Path]]:
    input_paths = [
        project_root / "config" / "department-anchors.json",
        project_root / "config" / "elevator-shafts.json",
        project_root / "config" / "routing-policy.json",
        project_root / "miniprogram" / "data" / "floorNavPaths.js",
        project_root / "miniprogram" / "data" / "sameFloorPaths.js",
        project_root / "miniprogram" / "data" / "elevatorShafts.js",
    ]
    anchors = load_json(input_paths[0])
    shaft_config = load_json(input_paths[1])
    routing_policy = load_json(input_paths[2])
    floor_nav = load_commonjs_json(input_paths[3])
    same_floor = load_commonjs_json(input_paths[4])
    runtime_shafts = load_commonjs_json(input_paths[5])

    if not isinstance(anchors, list):
        raise ValueError("department anchors must be a JSON array")
    anchor_by_name: dict[str, dict[str, Any]] = {}
    floor_labels: dict[int, str] = {}
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, dict):
            raise ValueError(f"invalid department anchor at index {index}")
        name = anchor.get("name")
        floor_label = anchor.get("floor")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"invalid department anchor name at index {index}")
        if name in anchor_by_name:
            raise ValueError(f"duplicate department anchor: {name}")
        department_semantic_endpoint(anchor)
        floor_number = parse_floor(floor_label)
        if floor_number in floor_labels and floor_labels[floor_number] != floor_label:
            raise ValueError(f"inconsistent floor labels for floor {floor_number}")
        floor_labels[floor_number] = floor_label
        anchor_by_name[name] = anchor
    runtime_by_id = {item["shaftId"]: item for item in runtime_shafts}
    configured_by_id = {item["shaftId"]: item for item in shaft_config}
    floors = set(floor_labels)
    floor_paths = {floor: floor_map_path(floor_dir, floor) for floor in floors}
    input_paths.extend(floor_paths.values())
    images = {floor: Image.open(source).convert("RGB") for floor, source in floor_paths.items()}
    source_hashes = {floor: sha256_file(source) for floor, source in floor_paths.items()}
    floor_policies = {}
    safe_masks: dict[int, np.ndarray] = {}
    for floor in sorted(floors):
        policy = load_floor_policy(
            routing_policy,
            floor_labels[floor],
            source_hashes[floor],
        )
        surface = build_routing_surface(
            np.asarray(images[floor], dtype=np.uint8),
            policy,
        )
        floor_policies[floor] = policy
        safe_masks[floor] = surface.safe_mask
    issues: list[dict[str, object]] = []

    for key, item in sorted(floor_nav.items()):
        try:
            department_name, shaft_id, direction = key.split("|||", 2)
            anchor = anchor_by_name[department_name]
            floor = parse_floor(anchor["floor"])
            shaft = runtime_by_id[shaft_id]
            mapping = shaft["floorMappings"][anchor["floor"]]
            config_mapping = configured_by_id[shaft_id]["floorMappings"][anchor["floor"]]
            if mapping.get("elevatorGroupId") != config_mapping.get("elevatorGroupId"):
                add_issue(issues, "floorNav", key, "runtime/config elevator group mismatch")
            elevator_anchor = mapping.get("elevatorAnchor")
            if not point_valid(elevator_anchor):
                add_issue(issues, "floorNav", key, "invalid (shaftId, floor) elevator anchor")
                continue
            if item.get("shaftId") != shaft_id or item.get("direction") != direction:
                add_issue(issues, "floorNav", key, "route metadata mismatch")
            if item.get("elevatorGroupId") != mapping.get("elevatorGroupId"):
                add_issue(issues, "floorNav", key, "elevator group mismatch")
            department_endpoint, department_endpoint_type = department_semantic_endpoint(anchor)
            department_tolerance = (
                1
                if department_endpoint_type == "doorApproachPoint"
                else floor_policies[floor].max_anchor_snap_px
            )
            if direction == "toElevator":
                expected_start, expected_end = department_endpoint, elevator_anchor
                start_tolerance_px, end_tolerance_px = department_tolerance, 0
                start_endpoint_type, end_endpoint_type = (
                    department_endpoint_type,
                    "runtime elevatorAnchor",
                )
            elif direction == "fromElevator":
                expected_start, expected_end = elevator_anchor, department_endpoint
                start_tolerance_px, end_tolerance_px = 0, department_tolerance
                start_endpoint_type, end_endpoint_type = (
                    "runtime elevatorAnchor",
                    department_endpoint_type,
                )
            else:
                add_issue(issues, "floorNav", key, "invalid direction")
                continue
            try:
                item_anchor_residual = endpoint_pixel_distance(
                    item.get("elevatorAnchor"),
                    elevator_anchor,
                    item.get("imageSize"),
                    actual_label="item.elevatorAnchor",
                    expected_label="runtime elevatorAnchor",
                )
                if item_anchor_residual > 0:
                    add_issue(
                        issues,
                        "floorNav",
                        key,
                        "item.elevatorAnchor is on a different rounded pixel from runtime elevatorAnchor",
                    )
            except (TypeError, ValueError) as error:
                add_issue(issues, "floorNav", key, f"invalid item.elevatorAnchor: {error}")
            validate_record(
                "floorNav", key, item, expected_start, expected_end,
                start_tolerance_px, end_tolerance_px,
                start_endpoint_type, end_endpoint_type,
                floor, images, safe_masks, source_hashes, issues,
            )
        except (KeyError, TypeError, ValueError) as error:
            add_issue(issues, "floorNav", key, f"invalid record: {error}")

    for key, item in sorted(same_floor.items()):
        try:
            start_name, end_name = key.split("|||", 1)
            start_anchor = anchor_by_name[start_name]
            end_anchor = anchor_by_name[end_name]
            floor = parse_floor(start_anchor["floor"])
            if start_anchor["floor"] != end_anchor["floor"]:
                add_issue(issues, "sameFloor", key, "departments are on different floors")
                continue
            start_endpoint, start_endpoint_type = department_semantic_endpoint(start_anchor)
            end_endpoint, end_endpoint_type = department_semantic_endpoint(end_anchor)
            max_anchor_snap_px = floor_policies[floor].max_anchor_snap_px
            start_tolerance_px = (
                1 if start_endpoint_type == "doorApproachPoint" else max_anchor_snap_px
            )
            end_tolerance_px = (
                1 if end_endpoint_type == "doorApproachPoint" else max_anchor_snap_px
            )
            validate_record(
                "sameFloor", key, item, start_endpoint, end_endpoint,
                start_tolerance_px, end_tolerance_px,
                start_endpoint_type, end_endpoint_type,
                floor, images, safe_masks, source_hashes, issues,
                co_located=item.get("coLocated") is True,
            )
        except (KeyError, TypeError, ValueError) as error:
            add_issue(issues, "sameFloor", key, f"invalid record: {error}")

    checked_floor_nav_pairs: set[tuple[str, str]] = set()
    for key, item in sorted(floor_nav.items()):
        try:
            department_name, shaft_id, direction = key.split("|||", 2)
            if direction not in {"toElevator", "fromElevator"}:
                continue
            pair = (department_name, shaft_id)
            if pair in checked_floor_nav_pairs:
                continue
            checked_floor_nav_pairs.add(pair)
            forward_key = f"{department_name}|||{shaft_id}|||toElevator"
            reverse_key = f"{department_name}|||{shaft_id}|||fromElevator"
            forward = floor_nav.get(forward_key)
            reverse = floor_nav.get(reverse_key)
            if not isinstance(forward, dict):
                add_issue(issues, "floorNav", reverse_key, "missing forward route record")
            elif not isinstance(reverse, dict):
                add_issue(issues, "floorNav", forward_key, "missing reverse route record")
            elif reverse.get("points") != list(reversed(forward.get("points", []))):
                add_issue(
                    issues,
                    "floorNav",
                    forward_key,
                    "forward/reverse geometry mismatch",
                )
        except (AttributeError, TypeError, ValueError) as error:
            add_issue(issues, "floorNav", key, f"invalid reverse route pair: {error}")

    checked_same_floor_pairs: set[tuple[str, str]] = set()
    for key, item in sorted(same_floor.items()):
        try:
            start_name, end_name = key.split("|||", 1)
            pair = tuple(sorted((start_name, end_name)))
            if pair in checked_same_floor_pairs:
                continue
            checked_same_floor_pairs.add(pair)
            reverse_key = f"{end_name}|||{start_name}"
            reverse = same_floor.get(reverse_key)
            if not isinstance(reverse, dict):
                add_issue(issues, "sameFloor", key, "missing reverse route record")
            elif item.get("coLocated") is True and reverse.get("coLocated") is True:
                continue
            elif reverse.get("points") != list(reversed(item.get("points", []))):
                add_issue(issues, "sameFloor", key, "forward/reverse geometry mismatch")
        except (AttributeError, TypeError, ValueError) as error:
            add_issue(issues, "sameFloor", key, f"invalid reverse route pair: {error}")

    pair_plans = 0
    for start in anchors:
        for end in anchors:
            if start["name"] == end["name"]:
                continue
            pair_plans += 1
            if start["floor"] == end["floor"]:
                if f'{start["name"]}|||{end["name"]}' not in same_floor:
                    add_issue(issues, "plan", f'{start["name"]} -> {end["name"]}', "missing same-floor path")
                continue
            candidates: list[tuple[float, str]] = []
            for shaft in runtime_shafts:
                shaft_id = shaft["shaftId"]
                first_key = f'{start["name"]}|||{shaft_id}|||toElevator'
                second_key = f'{end["name"]}|||{shaft_id}|||fromElevator'
                first = floor_nav.get(first_key)
                second = floor_nav.get(second_key)
                if first and second:
                    candidates.append((float(first["routeLength"]), shaft_id))
            if not candidates:
                add_issue(issues, "plan", f'{start["name"]} -> {end["name"]}', "no common verified shaft")
                continue
            _, selected_shaft = sorted(candidates, key=lambda value: (value[0], value[1]))[0]
            first = floor_nav.get(f'{start["name"]}|||{selected_shaft}|||toElevator')
            second = floor_nav.get(f'{end["name"]}|||{selected_shaft}|||fromElevator')
            if not first or not second or first.get("shaftId") != second.get("shaftId"):
                add_issue(issues, "plan", f'{start["name"]} -> {end["name"]}', "two legs switch shafts")

    for image in images.values():
        image.close()
    return (
        {
            "departments": len(anchors),
            "floorNavPaths": len(floor_nav),
            "sameFloorPaths": len(same_floor),
            "pairPlans": pair_plans,
            "issues": issues,
        },
        input_paths,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument("--floor-dir", type=Path)
    parser.add_argument("--report-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        project_root = resolve_path(args.project_dir, PROJECT_ROOT)
        floor_dir = resolve_path(args.floor_dir, DEFAULT_FLOOR_DIR)
        if args.report_dir is not None and args.report_dir.resolve().exists():
            raise FileExistsError(f"report directory already exists: {args.report_dir.resolve()}")
        summary, inputs = audit(project_root, floor_dir)
        print("Route connectivity audit")
        print(f'- departments: {summary["departments"]}')
        print(f'- per-shaft paths: {summary["floorNavPaths"]}')
        print(f'- same-floor paths: {summary["sameFloorPaths"]}')
        print(f'- pair plans: {summary["pairPlans"]}')
        print(f'- issues: {len(summary["issues"])}')
        if args.report_dir is not None:
            report_dir = create_report_dir(args.report_dir)
            write_json(report_dir / "route-connectivity.json", summary)
            write_csv(
                report_dir / "route-connectivity-issues.csv",
                summary["issues"], ["type", "key", "message"],
            )
            write_metadata(
                report_dir, Path(__file__).name,
                input_metadata(inputs, project_root),
            )
            print(f"- report: {report_dir}")
        if summary["issues"]:
            for issue in summary["issues"][:20]:
                print(f'ERROR {issue["type"]} {issue["key"]}: {issue["message"]}', file=sys.stderr)
            return 1
        return 0
    except Exception as error:  # CLI boundary
        print(f"audit-route-connectivity failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
