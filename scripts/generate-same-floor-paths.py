"""Generate canonical same-floor paths from reviewed or proposed endpoints."""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from project_paths import (
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


ROUTE_LENGTH_UNIT = "imageWidthPercent"
COLOCATED_PAIRS = frozenset(
    {
        frozenset(("中药房", "西药房")),
        frozenset(("耳鼻喉科门诊", "眼科门诊")),
        frozenset(("血液透析科", "内镜诊疗中心")),
        frozenset(("病理科", "重症医学科")),
        frozenset(("妇产科病房", "产房")),
    }
)


def load_floor_image(floor_dir: Path, floor: int) -> tuple[np.ndarray, Path]:
    source = resolve_floor_map(floor_dir, floor)
    with Image.open(source) as image:
        return np.asarray(image.convert("RGB")), source


def normalize_point(value: object, *, label: str, item: dict) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"invalid department {label}: {item!r}")
    point = [float(value[0]), float(value[1])]
    if not all(math.isfinite(coordinate) and 0 <= coordinate <= 100 for coordinate in point):
        raise ValueError(f"invalid department {label} coordinates: {item!r}")
    return point


def normalize_anchors(records: object) -> dict[str, dict]:
    if not isinstance(records, list):
        raise ValueError("department anchors must be a JSON array")
    anchors = {}
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("each department anchor must be an object")
        name = str(item.get("name") or "").strip()
        floor = str(item.get("floor") or "").strip()
        if not name or not floor:
            raise ValueError(f"invalid department anchor: {item!r}")
        anchor = normalize_point(item.get("anchor"), label="anchor", item=item)
        door_approach = (
            None
            if item.get("doorApproachPoint") is None
            else normalize_point(
                item.get("doorApproachPoint"),
                label="doorApproachPoint",
                item=item,
            )
        )
        if name in anchors:
            raise ValueError(f"duplicate department anchor: {name}")
        parse_floor_number(floor)
        anchors[name] = {
            "name": name,
            "floor": floor,
            "anchor": anchor,
            "doorApproachPoint": door_approach,
            "endpoint": door_approach if door_approach is not None else anchor,
        }
    return anchors


def normalize_public_destinations(config: object) -> list[dict]:
    records = config.get("publicDestinations") if isinstance(config, dict) else None
    if not isinstance(records, list):
        raise ValueError("public destinations config must contain publicDestinations array")
    normalized = []
    seen = set()
    for record in records:
        name = str((record or {}).get("name") or "").strip()
        floor = str((record or {}).get("floor") or "").strip()
        if not name or not floor or name in seen:
            raise ValueError(f"invalid public destination: {record!r}")
        parse_floor_number(floor)
        normalized.append({"name": name, "floor": floor})
        seen.add(name)
    return normalized


def is_colocated(source: dict, target: dict) -> bool:
    return (
        source["endpoint"] == target["endpoint"]
        or frozenset((source["name"], target["name"])) in COLOCATED_PAIRS
    )


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
    floor: str,
    image: str,
    image_size: list[int],
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
        "floor": floor,
        "image": image,
        "imageSize": image_size,
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


def generate_same_floor_paths(
    department_anchors: object,
    public_destinations: object,
    floor_dir: str | Path,
    *,
    diagnostics: dict | None = None,
) -> dict[str, dict]:
    anchors = normalize_anchors(department_anchors)
    public = normalize_public_destinations(public_destinations)
    for destination in public:
        anchor = anchors.get(destination["name"])
        if not anchor:
            raise ValueError(f"public destination has no independent anchor: {destination['name']}")
        if anchor["floor"] != destination["floor"]:
            raise ValueError(
                f"public destination floor differs from anchor: {destination['name']}"
            )

    by_floor: dict[str, list[dict]] = {}
    for destination in public:
        by_floor.setdefault(destination["floor"], []).append(anchors[destination["name"]])

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

    def process_one_floor(floor: str, records: list[dict]) -> dict[str, dict]:
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
            image_path = f"/assets/floor-maps/{floor_number}F.jpg"
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

            common = {
                "floor": floor,
                "image": image_path,
                "imageSize": image_size,
                "routeLengthUnit": ROUTE_LENGTH_UNIT,
                "sourceFloorMapSha256": source_hash,
            }
            for left_index, left in enumerate(records):
                for right in records[left_index + 1:]:
                    left_key = f"{left['name']}|||{right['name']}"
                    right_key = f"{right['name']}|||{left['name']}"
                    if is_colocated(left, right):
                        floor_paths[left_key] = {
                            **common,
                            "points": [list(left["endpoint"])],
                            "routeLength": 0,
                            "coLocated": True,
                        }
                        floor_paths[right_key] = {
                            **common,
                            "points": [list(right["endpoint"])],
                            "routeLength": 0,
                            "coLocated": True,
                        }
                        continue

                    solver_diagnostics = SolverDiagnostics()
                    result = solve_safe_path(
                        surface,
                        tuple(left["endpoint"]),
                        tuple(right["endpoint"]),
                        max_anchor_snap_px=policy.max_anchor_snap_px,
                        endpoint_bridge_radius_cells=policy.endpoint_bridge_radius_cells,
                        local_candidate_limit=policy.local_candidate_limit,
                        local_seed_index_radius=policy.local_seed_index_radius,
                        local_max_turns=policy.local_max_turns,
                        path_distance_tie_tolerance_px=path_distance_tie_tolerance_px,
                        turn_angle_degrees=turn_angle_degrees,
                        diagnostics=solver_diagnostics,
                    )
                    if (
                        result is None
                        or len(result.points) < 2
                        or result.points[0] == result.points[-1]
                    ):
                        reason = solver_diagnostics.failure_reason or "unknown"
                        failures.append(
                            f"{left['name']}/{right['name']}: path not found ({reason})"
                        )
                        continue
                    if not math.isfinite(result.route_length) or result.route_length <= 0:
                        failures.append(
                            f"{left['name']}/{right['name']}: invalid route length"
                        )
                        continue
                    if (
                        not math.isfinite(result.min_clearance_px)
                        or result.min_clearance_px <= 0
                    ):
                        failures.append(
                            f"{left['name']}/{right['name']}: invalid minimum clearance"
                        )
                        continue

                    residual_failures = []
                    if (
                        left["doorApproachPoint"] is not None
                        and result.source_snap_distance_px > 1
                    ):
                        residual_failures.append(
                            f"{left['name']} doorApproachPoint residual snap "
                            f"{result.source_snap_distance_px:.3f} px exceeds 1 px"
                        )
                    if (
                        right["doorApproachPoint"] is not None
                        and result.target_snap_distance_px > 1
                    ):
                        residual_failures.append(
                            f"{right['name']} doorApproachPoint residual snap "
                            f"{result.target_snap_distance_px:.3f} px exceeds 1 px"
                        )
                    if residual_failures:
                        failures.append(
                            f"{left['name']}/{right['name']}: "
                            + "; ".join(residual_failures)
                        )
                        continue

                    record_options = {
                        "floor": floor,
                        "image": image_path,
                        "image_size": image_size,
                        "source_hash": source_hash,
                    }
                    floor_paths[left_key] = path_record(
                        result,
                        **record_options,
                        reverse=False,
                    )
                    floor_paths[right_key] = path_record(
                        result,
                        **record_options,
                        reverse=True,
                    )
            return floor_paths
        finally:
            if floor in cache.keys():
                cache.evict(floor)
            if surface is not None:
                del surface
            if image is not None:
                del image

    for floor in sorted(by_floor, key=parse_floor_number):
        records = by_floor[floor]
        if len(records) < 2:
            continue
        paths.update(process_one_floor(floor, records))
        assert cache.keys() == (), "floor surface cache must be empty between floors"

    generation_diagnostics["peakSurfaceItems"] = cache.peak_items
    assert cache.keys() == (), "floor surface cache must be empty after generation"
    if failures:
        raise RuntimeError(
            "failed to generate same-floor route(s):\n"
            + "\n".join(f"- {item}" for item in failures)
        )
    return paths


def render_commonjs(value: object) -> str:
    return render_commonjs_export(
        value,
        "canonical same-floor routes",
        provenance=current_route_provenance(project_root(__file__)),
    )


def write_or_check(output: Path, content: str, check: bool) -> None:
    if check:
        if not output.is_file() or output.read_text(encoding="utf-8") != content:
            print(f"generated output is stale: {output}", file=sys.stderr)
            raise SystemExit(1)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8", newline="\n")


def build_parser() -> argparse.ArgumentParser:
    root = project_root(__file__)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--department-anchors",
        type=Path,
        default=root / "config" / "department-anchors.json",
    )
    parser.add_argument(
        "--public-destinations",
        type=Path,
        default=root / "config" / "public-destinations.json",
    )
    parser.add_argument(
        "--floor-dir",
        type=Path,
        default=root.parent / "放入院内导航页面目录下" / "放入images文件夹" / "floor-maps",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "miniprogram" / "data" / "sameFloorPaths.js",
    )
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.perf_counter()
    diagnostics = {}
    paths = generate_same_floor_paths(
        load_json(resolve_cli_path(args.department_anchors, "config/department-anchors.json")),
        load_json(resolve_cli_path(args.public_destinations, "config/public-destinations.json")),
        resolve_cli_path(args.floor_dir, "../放入院内导航页面目录下/放入images文件夹/floor-maps"),
        diagnostics=diagnostics,
    )
    output = resolve_cli_path(args.output, "miniprogram/data/sameFloorPaths.js")
    write_or_check(output, render_commonjs(paths), args.check)
    action = "verified" if args.check else "generated"
    print(
        f"same-floor paths {action}: {len(paths)} records, "
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
