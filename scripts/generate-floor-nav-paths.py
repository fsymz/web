"""Generate verified per-shaft elevator walking paths from original floor maps."""

from __future__ import annotations

import argparse
import copy
import math
import sys
import time
from collections import deque
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from PIL import Image

from project_paths import (
    load_commonjs_json,
    load_json,
    parse_floor_number,
    project_root,
    resolve_cli_path,
    resolve_floor_map,
    sha256_file,
)
from route_provenance import current_route_provenance, render_commonjs_export
from routing_surface import FloorSurfaceCache, build_routing_surface, load_floor_policy
from safe_path_solver import PathResult, SolverDiagnostics, solve_safe_path


WALK_RGB = np.array([0xDC, 0xDE, 0xDD], dtype=np.uint8)
WALL_RGB = np.array([0x66, 0x5B, 0x5D], dtype=np.uint8)
CELL_SIZE = 6
WALK_TOLERANCE = 14
WALL_TOLERANCE = 36
WALL_DILATE_PX = 20
DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))
ROUTE_LENGTH_UNIT = "imageWidthPercent"


def route_length(points: Iterable[Iterable[float]], image_size: Iterable[float]) -> float:
    width, height = (float(value) for value in image_size)
    if width <= 0 or height <= 0:
        raise ValueError("image size must contain positive width and height")
    values = [[float(value) for value in point] for point in points]
    aspect = height / width
    total = 0.0
    for first, second in zip(values, values[1:]):
        dx = second[0] - first[0]
        dy = second[1] - first[1]
        total += math.sqrt(dx * dx + (dy * aspect) * (dy * aspect))
    return total


def load_floor_image(floor_dir: Path, floor: int) -> tuple[np.ndarray, Path]:
    source = resolve_floor_map(floor_dir, floor)
    with Image.open(source) as image:
        return np.asarray(image.convert("RGB")), source


def build_grid(image: np.ndarray) -> np.ndarray:
    walk_lower = np.maximum(WALK_RGB.astype(np.int16) - WALK_TOLERANCE, 0).astype(np.uint8)
    walk_upper = np.minimum(WALK_RGB.astype(np.int16) + WALK_TOLERANCE, 255).astype(np.uint8)
    wall_lower = np.maximum(WALL_RGB.astype(np.int16) - WALL_TOLERANCE, 0).astype(np.uint8)
    wall_upper = np.minimum(WALL_RGB.astype(np.int16) + WALL_TOLERANCE, 255).astype(np.uint8)
    near_walk = cv2.inRange(image, walk_lower, walk_upper)
    near_wall = cv2.inRange(image, wall_lower, wall_upper)
    walk = cv2.morphologyEx(
        near_walk,
        cv2.MORPH_CLOSE,
        np.ones((7, 7), dtype=np.uint8),
        iterations=1,
    )
    wall = cv2.dilate(
        near_wall,
        np.ones((WALL_DILATE_PX, WALL_DILATE_PX), dtype=np.uint8),
        iterations=1,
    )
    wall_mask = (wall > 0).astype(np.uint8)
    passable = ((walk > 0) & (wall_mask == 0)).astype(np.uint8)
    height, width = passable.shape
    grid_height = (height + CELL_SIZE - 1) // CELL_SIZE
    grid_width = (width + CELL_SIZE - 1) // CELL_SIZE
    padded = np.pad(
        passable,
        ((0, grid_height * CELL_SIZE - height), (0, grid_width * CELL_SIZE - width)),
        constant_values=0,
    )
    padded_wall = np.pad(
        wall_mask,
        ((0, grid_height * CELL_SIZE - height), (0, grid_width * CELL_SIZE - width)),
        constant_values=1,
    )
    pass_ratio = padded.reshape(
        grid_height, CELL_SIZE, grid_width, CELL_SIZE
    ).mean(axis=(1, 3))
    wall_ratio = padded_wall.reshape(
        grid_height, CELL_SIZE, grid_width, CELL_SIZE
    ).mean(axis=(1, 3))
    return (pass_ratio >= 0.30) & (wall_ratio == 0)


def percent_to_grid(point: Iterable[float], image_shape: tuple[int, ...]) -> tuple[int, int]:
    height, width = image_shape[:2]
    x_percent, y_percent = (float(value) for value in point)
    x = int(round(x_percent / 100 * width))
    y = int(round(y_percent / 100 * height))
    grid_width = (width + CELL_SIZE - 1) // CELL_SIZE
    grid_height = (height + CELL_SIZE - 1) // CELL_SIZE
    return (
        max(0, min(grid_width - 1, x // CELL_SIZE)),
        max(0, min(grid_height - 1, y // CELL_SIZE)),
    )


def pixel_to_grid(point: Iterable[float], image_shape: tuple[int, ...]) -> tuple[int, int]:
    height, width = image_shape[:2]
    x, y = (float(value) for value in point)
    grid_width = (width + CELL_SIZE - 1) // CELL_SIZE
    grid_height = (height + CELL_SIZE - 1) // CELL_SIZE
    return (
        max(0, min(grid_width - 1, int(x) // CELL_SIZE)),
        max(0, min(grid_height - 1, int(y) // CELL_SIZE)),
    )


def grid_point_to_percent(point: tuple[int, int], image_shape: tuple[int, ...]) -> list[float]:
    height, width = image_shape[:2]
    x = min(width - 1, point[0] * CELL_SIZE + CELL_SIZE / 2)
    y = min(height - 1, point[1] * CELL_SIZE + CELL_SIZE / 2)
    return [round(x / width * 100, 3), round(y / height * 100, 3)]


def snap_to_grid(
    grid: np.ndarray,
    start: tuple[int, int],
    *,
    max_distance: int = 360,
) -> tuple[int, int] | None:
    start_x, start_y = start
    grid_height, grid_width = grid.shape
    if grid[start_y, start_x]:
        return start
    queue = deque([start])
    seen = {start}
    while queue:
        x, y = queue.popleft()
        for dx, dy in DIRECTIONS:
            candidate = (x + dx, y + dy)
            next_x, next_y = candidate
            if (
                next_x < 0
                or next_y < 0
                or next_x >= grid_width
                or next_y >= grid_height
                or candidate in seen
            ):
                continue
            if abs(next_x - start_x) + abs(next_y - start_y) > max_distance:
                continue
            if grid[next_y, next_x]:
                return candidate
            seen.add(candidate)
            queue.append(candidate)
    return None


def normalize_point(value: object, *, label: str, item: dict) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"invalid department {label}: {item!r}")
    point = [float(value[0]), float(value[1])]
    if not all(math.isfinite(coordinate) and 0 <= coordinate <= 100 for coordinate in point):
        raise ValueError(f"invalid department {label} coordinates: {item!r}")
    return point


def normalize_anchors(records: object) -> list[dict]:
    if not isinstance(records, list):
        raise ValueError("department anchors must be a JSON array")
    anchors = []
    seen = set()
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("each department anchor must be an object")
        name = str(item.get("name") or "").strip()
        floor = str(item.get("floor") or "").strip()
        if not name or not floor:
            raise ValueError(f"invalid department anchor: {item!r}")
        point = normalize_point(item.get("anchor"), label="anchor", item=item)
        door_approach = (
            None
            if item.get("doorApproachPoint") is None
            else normalize_point(
                item.get("doorApproachPoint"),
                label="doorApproachPoint",
                item=item,
            )
        )
        if name in seen:
            raise ValueError(f"duplicate department anchor: {name}")
        parse_floor_number(floor)
        seen.add(name)
        anchors.append(
            {
                "name": name,
                "floor": floor,
                "anchor": point,
                "doorApproachPoint": door_approach,
                "endpoint": door_approach if door_approach is not None else point,
            }
        )
    return anchors


def group_index(elevator_groups: object) -> dict[tuple[str, str], dict]:
    if not isinstance(elevator_groups, dict):
        raise ValueError("elevator groups must be an object keyed by floor")
    index = {}
    for floor, groups in elevator_groups.items():
        if not isinstance(groups, list):
            raise ValueError(f"elevator groups for {floor} must be an array")
        for group in groups:
            group_id = str(group.get("id") or "").strip()
            bbox = group.get("bbox")
            if not group_id or not isinstance(bbox, list) or len(bbox) != 4:
                raise ValueError(f"invalid elevator group on {floor}: {group!r}")
            index[(str(floor), group_id)] = group
    return index


def validate_shaft_groups(shafts: object, groups: dict[tuple[str, str], dict]) -> list[dict]:
    if not isinstance(shafts, list):
        raise ValueError("shaft config must be a JSON array")
    for shaft in shafts:
        shaft_id = str(shaft.get("shaftId") or "").strip()
        if not shaft_id:
            raise ValueError(f"shaft is missing shaftId: {shaft!r}")
        mappings = shaft.get("floorMappings") or {}
        if not isinstance(mappings, dict):
            raise ValueError(f"shaft {shaft_id} floorMappings must be an object")
        for floor, mapping in mappings.items():
            group_id = str((mapping or {}).get("elevatorGroupId") or "").strip()
            if not group_id or (str(floor), group_id) not in groups:
                raise ValueError(
                    f"unknown elevator group {group_id or '<missing>'} for {shaft_id} on {floor}"
                )
    return shafts


def is_eligible_mapping(shaft: dict, floor: str, mapping: dict) -> bool:
    return (
        shaft.get("patientAccessible") is True
        and floor in (shaft.get("serviceFloors") or [])
        and mapping.get("confirmed") is True
    )


def elevator_anchor_for_group(
    group: dict,
    grid: np.ndarray,
    image_shape: tuple[int, ...],
    component_labels: np.ndarray | None = None,
    required_component: int | None = None,
) -> list[float]:
    bbox = [float(value) for value in group["bbox"]]
    center = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    start = pixel_to_grid(center, image_shape)
    grid_height, grid_width = grid.shape
    queue = deque([start])
    seen = {start}
    snapped = None
    while queue:
        x, y = queue.popleft()
        center_x = x * CELL_SIZE + CELL_SIZE / 2
        center_y = y * CELL_SIZE + CELL_SIZE / 2
        outside_bbox = not (
            bbox[0] <= center_x <= bbox[2] and bbox[1] <= center_y <= bbox[3]
        )
        in_required_component = (
            required_component is None
            or (
                component_labels is not None
                and int(component_labels[y, x]) == required_component
            )
        )
        if outside_bbox and grid[y, x] and in_required_component:
            snapped = (x, y)
            break
        for dx, dy in DIRECTIONS:
            candidate = (x + dx, y + dy)
            next_x, next_y = candidate
            if (
                next_x < 0
                or next_y < 0
                or next_x >= grid_width
                or next_y >= grid_height
                or candidate in seen
            ):
                continue
            seen.add(candidate)
            queue.append(candidate)
    if snapped is None:
        raise RuntimeError(f"cannot find walkable corridor anchor near elevator group {group['id']}")
    return grid_point_to_percent(snapped, image_shape)


def department_corridor_component(
    grid: np.ndarray,
    departments: list[dict],
    image_shape: tuple[int, ...],
) -> tuple[np.ndarray, int]:
    _, labels = cv2.connectedComponents(grid.astype(np.uint8), connectivity=4)
    components = set()
    missing = []
    for department in departments:
        snapped = snap_to_grid(
            grid,
            percent_to_grid(department["endpoint"], image_shape),
        )
        if snapped is None:
            missing.append(department["name"])
            continue
        component = int(labels[snapped[1], snapped[0]])
        if component <= 0:
            missing.append(department["name"])
            continue
        components.add(component)
    if missing:
        raise RuntimeError(
            "department anchor cannot reach a walkable corridor: " + ", ".join(missing)
        )
    if len(components) != 1:
        raise RuntimeError(
            "department anchors do not share one walkable corridor component: "
            + ", ".join(str(value) for value in sorted(components))
        )
    return labels, next(iter(components))


def navigation_number(
    document: object,
    key: str,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    value = document.get(key) if isinstance(document, dict) else None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        bounds = f"at least {minimum}"
        if maximum is not None:
            bounds = f"in the inclusive range {minimum}..{maximum}"
        raise ValueError(f"navigation policy {key} must be a finite number {bounds}")
    return float(value)


def path_record(
    result: PathResult,
    *,
    department_name: str,
    shaft_id: str,
    direction: str,
    floor: str,
    image: str,
    image_size: list[int],
    elevator_group_id: str,
    elevator_anchor: list[float],
    source_hash: str,
    reverse: bool,
) -> dict:
    points = list(reversed(result.points)) if reverse else list(result.points)
    semantic_indexes = (
        [len(points) - 1 - index for index in reversed(result.semantic_point_indexes)]
        if reverse
        else list(result.semantic_point_indexes)
    )
    return {
        "departmentName": department_name,
        "shaftId": shaft_id,
        "direction": direction,
        "floor": floor,
        "image": image,
        "imageSize": image_size,
        "elevatorGroupId": elevator_group_id,
        "elevatorAnchor": elevator_anchor,
        "routeLengthUnit": ROUTE_LENGTH_UNIT,
        "sourceFloorMapSha256": source_hash,
        "geometrySha256": result.geometry_sha256,
        "solverQualityStatus": result.solver_quality_status,
        "points": [list(point) for point in points],
        "routeLength": result.route_length,
        "minClearancePx": result.min_clearance_px,
        "minClearanceImageWidthPercent": round(
            result.min_clearance_px / image_size[0] * 100,
            6,
        ),
        "effectiveTurnCount": result.effective_turn_count,
        "shortestSegment": result.shortest_segment,
        "semanticPointIndexes": semantic_indexes,
    }


def generate_floor_nav_paths(
    department_anchors: object,
    shaft_config: object,
    elevator_groups: object,
    floor_dir: str | Path,
    *,
    diagnostics: dict | None = None,
) -> tuple[dict, list[dict]]:
    anchors = normalize_anchors(department_anchors)
    groups = group_index(elevator_groups)
    shafts = validate_shaft_groups(shaft_config, groups)
    runtime_shafts = copy.deepcopy(shafts)
    runtime_by_id = {shaft["shaftId"]: shaft for shaft in runtime_shafts}

    anchors_by_floor: dict[str, list[dict]] = {}
    for anchor in anchors:
        anchors_by_floor.setdefault(anchor["floor"], []).append(anchor)

    root = project_root(__file__)
    routing_policy_document = load_json(root / "config" / "routing-policy.json")
    navigation_policy_document = load_json(root / "config" / "navigation-policy.json")
    path_distance_tie_tolerance_px = navigation_number(
        navigation_policy_document,
        "pathDistanceTieTolerancePx",
        minimum=0,
    )
    turn_angle_degrees = navigation_number(
        navigation_policy_document,
        "turnAngleDegrees",
        minimum=0,
        maximum=180,
    )

    generation_diagnostics = diagnostics if diagnostics is not None else {}
    generation_diagnostics.clear()
    generation_diagnostics.update(
        {
            "floors": {},
            "peakSurfaceItems": 0,
            "peakSurfaceResidentBytes": 0,
            "peakFloorResidentBytes": 0,
        }
    )
    cache = FloorSurfaceCache(max_items=1)
    paths: dict[str, dict] = {}
    failures: list[str] = []

    def process_one_floor(floor: str, departments: list[dict]) -> dict[str, dict]:
        floor_paths: dict[str, dict] = {}
        floor_number = parse_floor_number(floor)
        image = None
        surface = None
        try:
            image, source_path = load_floor_image(Path(floor_dir), floor_number)
            source_hash = sha256_file(source_path)
            policy = load_floor_policy(routing_policy_document, floor, source_hash)
            surface = cache.get_or_build(
                floor,
                lambda: build_routing_surface(image, policy),
            )
            height, width = image.shape[:2]
            image_size = [width, height]
            image_url = f"/assets/floor-maps/{floor_number}F.jpg"
            source_image_bytes = int(image.nbytes)
            surface_resident_bytes = int(surface.resident_nbytes)
            floor_resident_bytes = source_image_bytes + surface_resident_bytes
            generation_diagnostics["floors"][floor] = {
                "sourceImageBytes": source_image_bytes,
                "surfaceResidentBytes": surface_resident_bytes,
                "floorResidentBytes": floor_resident_bytes,
            }
            generation_diagnostics["peakSurfaceItems"] = cache.peak_items
            generation_diagnostics["peakSurfaceResidentBytes"] = max(
                generation_diagnostics["peakSurfaceResidentBytes"],
                surface_resident_bytes,
            )
            generation_diagnostics["peakFloorResidentBytes"] = max(
                generation_diagnostics["peakFloorResidentBytes"],
                floor_resident_bytes,
            )

            proposal_grid = build_grid(image)
            component_labels, required_component = department_corridor_component(
                proposal_grid,
                departments,
                image.shape,
            )
            eligible = []
            for shaft in shafts:
                mapping = (shaft.get("floorMappings") or {}).get(floor)
                if not mapping or not is_eligible_mapping(shaft, floor, mapping):
                    continue
                group = groups[(floor, mapping["elevatorGroupId"])]
                try:
                    elevator_anchor = elevator_anchor_for_group(
                        group,
                        proposal_grid,
                        image.shape,
                        component_labels,
                        required_component,
                    )
                except RuntimeError as error:
                    failures.append(f"{floor}/{shaft['shaftId']}/elevatorAnchor: {error}")
                    continue
                eligible.append((shaft, mapping, elevator_anchor))
            del component_labels
            del proposal_grid

            for department in departments:
                department_endpoint = department["endpoint"]
                for shaft, mapping, elevator_anchor in eligible:
                    solver_diagnostics = SolverDiagnostics()
                    result = solve_safe_path(
                        surface,
                        tuple(department_endpoint),
                        tuple(elevator_anchor),
                        max_anchor_snap_px=policy.max_anchor_snap_px,
                        endpoint_bridge_radius_cells=policy.endpoint_bridge_radius_cells,
                        local_candidate_limit=policy.local_candidate_limit,
                        local_seed_index_radius=policy.local_seed_index_radius,
                        local_max_turns=policy.local_max_turns,
                        path_distance_tie_tolerance_px=path_distance_tie_tolerance_px,
                        turn_angle_degrees=turn_angle_degrees,
                        diagnostics=solver_diagnostics,
                    )
                    failure_key = f"{department['name']}/{shaft['shaftId']}"
                    if (
                        result is None
                        or len(result.points) < 2
                        or result.points[0] == result.points[-1]
                    ):
                        reason = solver_diagnostics.failure_reason or "unknown"
                        failures.append(f"{failure_key}: path not found ({reason})")
                        continue
                    if not math.isfinite(result.route_length) or result.route_length <= 0:
                        failures.append(f"{failure_key}: invalid route length")
                        continue
                    if (
                        not math.isfinite(result.min_clearance_px)
                        or result.min_clearance_px <= 0
                    ):
                        failures.append(f"{failure_key}: invalid minimum clearance")
                        continue
                    if (
                        department["doorApproachPoint"] is not None
                        and result.source_snap_distance_px > 1
                    ):
                        failures.append(
                            f"{failure_key}: {department['name']} doorApproachPoint "
                            f"residual snap {result.source_snap_distance_px:.3f} px exceeds 1 px"
                        )
                        continue

                    actual_elevator_anchor = list(result.points[-1])
                    runtime_mapping = runtime_by_id[shaft["shaftId"]]["floorMappings"][floor]
                    runtime_mapping["elevatorAnchor"] = actual_elevator_anchor
                    record_options = {
                        "department_name": department["name"],
                        "shaft_id": shaft["shaftId"],
                        "floor": floor,
                        "image": image_url,
                        "image_size": image_size,
                        "elevator_group_id": mapping["elevatorGroupId"],
                        "elevator_anchor": actual_elevator_anchor,
                        "source_hash": source_hash,
                    }
                    to_key = f"{department['name']}|||{shaft['shaftId']}|||toElevator"
                    from_key = f"{department['name']}|||{shaft['shaftId']}|||fromElevator"
                    floor_paths[to_key] = path_record(
                        result,
                        direction="toElevator",
                        reverse=False,
                        **record_options,
                    )
                    floor_paths[from_key] = path_record(
                        result,
                        direction="fromElevator",
                        reverse=True,
                        **record_options,
                    )
            return floor_paths
        finally:
            if floor in cache.keys():
                cache.evict(floor)
            if surface is not None:
                del surface
            if image is not None:
                del image

    for floor in sorted(anchors_by_floor, key=parse_floor_number):
        paths.update(process_one_floor(floor, anchors_by_floor[floor]))
        assert cache.keys() == (), "floor surface cache must be empty between floors"

    generation_diagnostics["peakSurfaceItems"] = cache.peak_items
    assert cache.keys() == (), "floor surface cache must be empty after generation"
    if failures:
        raise RuntimeError(
            "failed to generate elevator route(s):\n" + "\n".join(f"- {item}" for item in failures)
        )
    return paths, runtime_shafts


def render_commonjs(
    value: object,
    description: str,
    *,
    include_provenance: bool = False,
) -> str:
    return render_commonjs_export(
        value,
        description,
        provenance=(
            current_route_provenance(project_root(__file__))
            if include_provenance
            else None
        ),
    )


def write_or_check(outputs: list[tuple[Path, str]], check: bool) -> None:
    drifted = []
    for output, content in outputs:
        if check:
            if not output.is_file() or output.read_text(encoding="utf-8") != content:
                drifted.append(output)
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8", newline="\n")
    if drifted:
        for output in drifted:
            print(f"generated output is stale: {output}", file=sys.stderr)
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    root = project_root(__file__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--department-anchors",
        type=Path,
        default=root / "config" / "department-anchors.json",
    )
    parser.add_argument(
        "--shaft-config",
        type=Path,
        default=root / "config" / "elevator-shafts.json",
    )
    parser.add_argument(
        "--elevator-groups",
        type=Path,
        default=root / "miniprogram" / "data" / "elevatorGroups.js",
    )
    parser.add_argument(
        "--floor-dir",
        type=Path,
        default=root.parent / "放入院内导航页面目录下" / "放入images文件夹" / "floor-maps",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "miniprogram" / "data" / "floorNavPaths.js",
    )
    parser.add_argument(
        "--runtime-shaft-output",
        type=Path,
        default=root / "miniprogram" / "data" / "elevatorShafts.js",
    )
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.perf_counter()
    diagnostics = {}
    paths, runtime_shafts = generate_floor_nav_paths(
        load_json(resolve_cli_path(args.department_anchors, "config/department-anchors.json")),
        load_json(resolve_cli_path(args.shaft_config, "config/elevator-shafts.json")),
        load_commonjs_json(
            resolve_cli_path(args.elevator_groups, "miniprogram/data/elevatorGroups.js")
        ),
        resolve_cli_path(args.floor_dir, "../放入院内导航页面目录下/放入images文件夹/floor-maps"),
        diagnostics=diagnostics,
    )
    output = resolve_cli_path(args.output, "miniprogram/data/floorNavPaths.js")
    runtime_output = resolve_cli_path(
        args.runtime_shaft_output, "miniprogram/data/elevatorShafts.js"
    )
    write_or_check(
        [
            (
                output,
                render_commonjs(
                    paths,
                    "per-shaft elevator navigation paths",
                    include_provenance=True,
                ),
            ),
            (runtime_output, render_commonjs(runtime_shafts, "runtime elevator shaft mappings")),
        ],
        args.check,
    )
    action = "verified" if args.check else "generated"
    print(
        f"floor navigation paths {action}: {len(paths)} records, "
        f"peakSurfaceItems={diagnostics['peakSurfaceItems']}, "
        f"peakSurfaceResidentBytes={diagnostics['peakSurfaceResidentBytes']}, "
        f"peakFloorResidentBytes={diagnostics['peakFloorResidentBytes']}, "
        f"{time.perf_counter() - started:.1f}s"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
