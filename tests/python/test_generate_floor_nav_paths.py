from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from routing_surface import FloorSurfaceCache
from safe_path_solver import PathResult, SolverDiagnostics


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "generate-floor-nav-paths.py"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
ANCHORS_PATH = PROJECT_ROOT / "config" / "department-anchors.json"
SHAFTS_PATH = PROJECT_ROOT / "config" / "elevator-shafts.json"
GROUPS_PATH = PROJECT_ROOT / "miniprogram" / "data" / "elevatorGroups.js"
PROVENANCE_FIELDS = {
    "algorithmVersion",
    "routingPolicySha256",
    "navigationPolicySha256",
    "autoValidationStatus",
    "reviewStatus",
}


def load_generator():
    assert SCRIPT_PATH.exists(), "floor navigation generator has not been implemented"
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("generate_floor_nav_paths", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_groups(path: Path, groups: dict) -> Path:
    path.write_text(
        "module.exports = " + json.dumps(groups, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )
    return path


def write_floor_map(path: Path) -> Path:
    pixels = np.full((200, 100, 3), [0xDC, 0xDE, 0xDD], dtype=np.uint8)
    pixels[82:119, 42:59] = [0x66, 0x5B, 0x5D]
    pixels[82:119, 70:87] = [0x66, 0x5B, 0x5D]
    Image.fromarray(pixels, "RGB").save(path)
    return path


def make_inputs(tmp_path: Path, *, unknown_group: bool = False):
    floor_dir = tmp_path / "maps"
    floor_dir.mkdir()
    write_floor_map(floor_dir / "1F.jpg")
    anchors = [{"name": "测试科室", "floor": "1楼", "anchor": [10.0, 10.0]}]
    groups = {
        "1楼": [
            {"id": "E1", "bbox": [42, 82, 58, 118], "x": 50, "y": 50},
            {"id": "E2", "bbox": [70, 82, 86, 118], "x": 78, "y": 50},
        ]
    }
    shafts = [
        {
            "shaftId": "S1",
            "displayName": "1号电梯",
            "patientAccessible": True,
            "serviceFloors": ["1楼"],
            "floorMappings": {
                "1楼": {
                    "elevatorGroupId": "E9" if unknown_group else "E1",
                    "confirmed": True,
                }
            },
        },
        {
            "shaftId": "S2",
            "displayName": "2号电梯",
            "patientAccessible": True,
            "serviceFloors": ["1楼"],
            "floorMappings": {"1楼": {"elevatorGroupId": "E2", "confirmed": True}},
        },
        {
            "shaftId": "S3",
            "displayName": "3号电梯",
            "patientAccessible": True,
            "serviceFloors": ["1楼"],
            "floorMappings": {"1楼": {"elevatorGroupId": "E1", "confirmed": False}},
        },
        {
            "shaftId": "S4",
            "displayName": "4号电梯",
            "patientAccessible": False,
            "serviceFloors": ["1楼"],
            "floorMappings": {"1楼": {"elevatorGroupId": "E1", "confirmed": True}},
        },
    ]
    return anchors, shafts, groups, floor_dir


def make_path_result(
    source,
    target,
    *,
    points=None,
    source_snap_distance_px=0.0,
    target_snap_distance_px=0.0,
    geometry_sha256="a" * 64,
    solver_quality_status="optimized",
):
    normalized_points = points or (
        tuple(float(value) for value in source),
        tuple(float(value) for value in target),
    )
    return PathResult(
        points=tuple(tuple(point) for point in normalized_points),
        pixel_points=tuple((index * 10, index * 10) for index in range(len(normalized_points))),
        route_length=12.5,
        min_clearance_px=3.5,
        effective_turn_count=2,
        shortest_segment=1.0,
        semantic_point_indexes=(0, len(normalized_points) - 1),
        geometry_sha256=geometry_sha256,
        source_snap_distance_px=source_snap_distance_px,
        target_snap_distance_px=target_snap_distance_px,
        solver_quality_status=solver_quality_status,
    )


def install_routing_fakes(
    generator,
    monkeypatch,
    *,
    solve=None,
    resident_bytes_by_floor=None,
    cache_type=FloorSurfaceCache,
):
    resident_bytes_by_floor = resident_bytes_by_floor or {}
    monkeypatch.setattr(generator, "sha256_file", lambda _path: "f" * 64)

    def fake_load_floor_policy(document, floor, source_hash):
        assert source_hash == "f" * 64
        defaults = document["defaults"]
        return SimpleNamespace(
            floor=floor,
            algorithm_version=document["algorithmVersion"],
            max_anchor_snap_px=defaults["maxAnchorSnapPx"],
            endpoint_bridge_radius_cells=defaults["endpointBridgeRadiusCells"],
            local_candidate_limit=defaults["localCandidateLimit"],
            local_seed_index_radius=defaults["localSeedIndexRadius"],
            local_max_turns=defaults["localMaxTurns"],
        )

    monkeypatch.setattr(
        generator, "load_floor_policy", fake_load_floor_policy, raising=False
    )
    monkeypatch.setattr(
        generator,
        "build_routing_surface",
        lambda _image, policy: SimpleNamespace(
            resident_nbytes=resident_bytes_by_floor.get(policy.floor, 1234)
        ),
        raising=False,
    )
    monkeypatch.setattr(
        generator,
        "solve_safe_path",
        solve
        or (lambda _surface, source, target, **_options: make_path_result(source, target)),
        raising=False,
    )
    monkeypatch.setattr(generator, "FloorSurfaceCache", cache_type, raising=False)


def canonical_json_sha256(value):
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_route_length_uses_image_width_percent_for_vertical_distance():
    generator = load_generator()
    assert generator.route_length([[50, 10], [50, 20]], [100, 200]) == pytest.approx(20)


def test_each_confirmed_patient_shaft_is_solved_once_and_reversed_exactly(
    tmp_path, monkeypatch
):
    generator = load_generator()
    anchors, shafts, groups, floor_dir = make_inputs(tmp_path)
    calls = []

    monkeypatch.setattr(generator, "sha256_file", lambda _path: "f" * 64)

    def fake_load_floor_policy(document, floor, source_hash):
        assert source_hash == "f" * 64
        defaults = document["defaults"]
        return SimpleNamespace(
            floor=floor,
            algorithm_version=document["algorithmVersion"],
            max_anchor_snap_px=defaults["maxAnchorSnapPx"],
            endpoint_bridge_radius_cells=defaults["endpointBridgeRadiusCells"],
            local_candidate_limit=defaults["localCandidateLimit"],
            local_seed_index_radius=defaults["localSeedIndexRadius"],
            local_max_turns=defaults["localMaxTurns"],
        )

    monkeypatch.setattr(
        generator, "load_floor_policy", fake_load_floor_policy, raising=False
    )
    monkeypatch.setattr(
        generator,
        "build_routing_surface",
        lambda _image, _policy: SimpleNamespace(resident_nbytes=1234),
        raising=False,
    )

    def fake_solve(_surface, source, target, **options):
        calls.append((source, target, options))
        points = (
            tuple(source),
            (source[0] + 1.0, source[1]),
            (target[0] - 1.0, target[1]),
            tuple(target),
        )
        return PathResult(
            points=points,
            pixel_points=((10, 10), (11, 10), (19, 20), (20, 20)),
            route_length=12.5,
            min_clearance_px=3.5,
            effective_turn_count=2,
            shortest_segment=1.0,
            semantic_point_indexes=(0, 1, 3),
            geometry_sha256="a" * 64,
            source_snap_distance_px=0.0,
            target_snap_distance_px=0.0,
            solver_quality_status="optimized",
        )

    monkeypatch.setattr(generator, "solve_safe_path", fake_solve, raising=False)
    paths, runtime_shafts = generator.generate_floor_nav_paths(
        anchors, shafts, groups, floor_dir
    )

    assert set(paths) == {
        "测试科室|||S1|||toElevator",
        "测试科室|||S1|||fromElevator",
        "测试科室|||S2|||toElevator",
        "测试科室|||S2|||fromElevator",
    }
    assert len(calls) == 2
    for shaft_id in ("S1", "S2"):
        outbound = paths[f"测试科室|||{shaft_id}|||toElevator"]
        inbound = paths[f"测试科室|||{shaft_id}|||fromElevator"]
        assert outbound["routeLengthUnit"] == "imageWidthPercent"
        assert inbound["routeLengthUnit"] == "imageWidthPercent"
        assert outbound["points"][0] == [10.0, 10.0]
        assert outbound["points"][-1] == outbound["elevatorAnchor"]
        assert inbound["points"][0] == inbound["elevatorAnchor"]
        assert inbound["points"][-1] == [10.0, 10.0]
        assert inbound["points"] == list(reversed(outbound["points"]))
        assert outbound["semanticPointIndexes"] == [0, 1, 3]
        assert inbound["semanticPointIndexes"] == [0, 2, 3]

    by_id = {shaft["shaftId"]: shaft for shaft in runtime_shafts}
    for shaft_id in ("S1", "S2"):
        mapping = by_id[shaft_id]["floorMappings"]["1楼"]
        assert len(mapping["elevatorAnchor"]) == 2
        group = next(item for item in groups["1楼"] if item["id"] == mapping["elevatorGroupId"])
        center = [
            (group["bbox"][0] + group["bbox"][2]) / 2,
            (group["bbox"][1] + group["bbox"][3]) / 2,
        ]
        center_percent = [center[0], center[1] / 2]
        assert mapping["elevatorAnchor"] != center_percent
        anchor_pixel = [
            mapping["elevatorAnchor"][0],
            mapping["elevatorAnchor"][1] * 2,
        ]
        assert not (
            group["bbox"][0] <= anchor_pixel[0] <= group["bbox"][2]
            and group["bbox"][1] <= anchor_pixel[1] <= group["bbox"][3]
        )


def test_solver_receives_exact_policy_options_and_records_required_metadata(
    tmp_path, monkeypatch
):
    generator = load_generator()
    anchors, shafts, groups, floor_dir = make_inputs(tmp_path)
    calls = []

    def fake_solve(_surface, source, target, **options):
        diagnostics = options.pop("diagnostics")
        assert isinstance(diagnostics, SolverDiagnostics)
        calls.append((source, target, options, diagnostics))
        snapped_target = (target[0] + 0.125, target[1])
        return make_path_result(
            source,
            snapped_target,
            geometry_sha256="b" * 64,
            solver_quality_status="fallbackCandidateLimit",
        )

    install_routing_fakes(generator, monkeypatch, solve=fake_solve)
    real_load_json = generator.load_json
    policy_loads = []

    def recording_load_json(path):
        policy_loads.append(Path(path).name)
        return real_load_json(path)

    monkeypatch.setattr(generator, "load_json", recording_load_json)
    paths, runtime_shafts = generator.generate_floor_nav_paths(
        anchors, shafts[:1], groups, floor_dir
    )

    assert policy_loads == ["routing-policy.json", "navigation-policy.json"]
    assert len(calls) == 1
    assert calls[0][0] == (10.0, 10.0)
    assert calls[0][2] == {
        "max_anchor_snap_px": 120,
        "endpoint_bridge_radius_cells": 4,
        "local_candidate_limit": 96,
        "local_seed_index_radius": 12,
        "local_max_turns": 8,
        "path_distance_tie_tolerance_px": 6,
        "turn_angle_degrees": 25,
    }
    assert calls[0][3].failure_reason is None

    outbound = paths["测试科室|||S1|||toElevator"]
    inbound = paths["测试科室|||S1|||fromElevator"]
    snapped_endpoint = list(calls[0][1])
    snapped_endpoint[0] += 0.125
    expected_outbound = {
        "departmentName": "测试科室",
        "shaftId": "S1",
        "direction": "toElevator",
        "floor": "1楼",
        "image": "/assets/floor-maps/1F.jpg",
        "imageSize": [100, 200],
        "elevatorGroupId": "E1",
        "elevatorAnchor": snapped_endpoint,
        "routeLengthUnit": "imageWidthPercent",
        "sourceFloorMapSha256": "f" * 64,
        "geometrySha256": "b" * 64,
        "solverQualityStatus": "fallbackCandidateLimit",
        "points": [[10.0, 10.0], snapped_endpoint],
        "routeLength": 12.5,
        "minClearancePx": 3.5,
        "minClearanceImageWidthPercent": 3.5,
        "effectiveTurnCount": 2,
        "shortestSegment": 1.0,
        "semanticPointIndexes": [0, 1],
    }
    assert outbound == expected_outbound
    assert inbound["points"] == list(reversed(outbound["points"]))
    assert inbound["direction"] == "fromElevator"
    for field in (
        "geometrySha256",
        "routeLength",
        "minClearancePx",
        "minClearanceImageWidthPercent",
        "effectiveTurnCount",
        "shortestSegment",
        "solverQualityStatus",
    ):
        assert inbound[field] == outbound[field]
    assert runtime_shafts[0]["floorMappings"]["1楼"]["elevatorAnchor"] == snapped_endpoint


def test_route_provenance_header_is_single_strict_and_elevator_output_has_none():
    generator = load_generator()
    record = generator.path_record(
        make_path_result([10.0, 10.0], [20.0, 20.0]),
        department_name="测试科室",
        shaft_id="S1",
        direction="toElevator",
        floor="1楼",
        image="/assets/floor-maps/1F.jpg",
        image_size=[100, 200],
        elevator_group_id="E1",
        elevator_anchor=[20.0, 20.0],
        source_hash="f" * 64,
        reverse=False,
    )

    rendered = generator.render_commonjs(
        {"测试科室|||S1|||toElevator": record},
        "per-shaft elevator navigation paths",
        include_provenance=True,
    )
    elevator_rendered = generator.render_commonjs(
        [{"shaftId": "S1"}], "runtime elevator shaft mappings"
    )

    lines = rendered.splitlines()
    header_lines = [line for line in lines if line.startswith("// route-provenance: ")]
    assert len(header_lines) == 1
    module_index = next(
        index for index, line in enumerate(lines) if line.startswith("module.exports = ")
    )
    assert lines[module_index - 1] == header_lines[0]
    routing_policy = json.loads(
        (PROJECT_ROOT / "config" / "routing-policy.json").read_text(encoding="utf-8")
    )
    navigation_policy = json.loads(
        (PROJECT_ROOT / "config" / "navigation-policy.json").read_text(encoding="utf-8")
    )
    assert json.loads(header_lines[0].removeprefix("// route-provenance: ")) == {
        "schemaVersion": 1,
        "algorithmVersion": routing_policy["algorithmVersion"],
        "routingPolicySha256": canonical_json_sha256(routing_policy),
        "navigationPolicySha256": canonical_json_sha256(navigation_policy),
        "autoValidationStatus": "passed",
        "reviewStatus": "pending",
    }
    exported = json.loads(
        lines[module_index].removeprefix("module.exports = ").removesuffix(";")
    )
    assert exported == {"测试科室|||S1|||toElevator": record}
    assert PROVENANCE_FIELDS.isdisjoint(record)
    assert "// route-provenance:" not in elevator_rendered


def test_unknown_elevator_group_fails_explicitly(tmp_path):
    generator = load_generator()
    anchors, shafts, groups, floor_dir = make_inputs(tmp_path, unknown_group=True)
    with pytest.raises(ValueError, match=r"unknown elevator group.*E9"):
        generator.generate_floor_nav_paths(anchors, shafts, groups, floor_dir)


def test_elevator_anchor_ignores_nearby_isolated_walkable_artifact(
    tmp_path, monkeypatch
):
    generator = load_generator()
    install_routing_fakes(generator, monkeypatch)
    source_path = tmp_path / "1F.jpg"
    source_path.write_bytes(b"fixture source hash")
    image = np.zeros((60, 60, 3), dtype=np.uint8)
    grid = np.zeros((10, 10), dtype=bool)
    grid[3, 3] = True
    grid[8, 1:9] = True
    monkeypatch.setattr(generator, "load_floor_image", lambda *_: (image, source_path))
    monkeypatch.setattr(generator, "build_grid", lambda _: grid)
    anchors = [{"name": "测试科室", "floor": "1楼", "anchor": [50.0, 85.0]}]
    groups = {"1楼": [{"id": "E1", "bbox": [24, 12, 36, 24]}]}
    shafts = [
        {
            "shaftId": "S1",
            "displayName": "1号电梯",
            "patientAccessible": True,
            "serviceFloors": ["1楼"],
            "floorMappings": {"1楼": {"elevatorGroupId": "E1", "confirmed": True}},
        }
    ]

    paths, runtime_shafts = generator.generate_floor_nav_paths(
        anchors, shafts, groups, tmp_path
    )

    mapping = runtime_shafts[0]["floorMappings"]["1楼"]
    assert generator.percent_to_grid(mapping["elevatorAnchor"], image.shape)[1] == 8
    assert len(paths) == 2


def test_unconfirmed_mapping_and_inaccessible_shaft_do_not_emit_paths(
    tmp_path, monkeypatch
):
    generator = load_generator()
    install_routing_fakes(generator, monkeypatch)
    anchors, shafts, groups, floor_dir = make_inputs(tmp_path)
    paths, _ = generator.generate_floor_nav_paths(anchors, shafts, groups, floor_dir)
    assert not any("|||S3|||" in key for key in paths)
    assert not any("|||S4|||" in key for key in paths)


def test_floor_cache_is_capacity_one_and_reports_resident_peak_bytes(
    tmp_path, monkeypatch
):
    generator = load_generator()
    image = np.full((200, 100, 3), [0xDC, 0xDE, 0xDD], dtype=np.uint8)
    caches = []
    load_states = []
    evicted = []

    class TrackingCache(FloorSurfaceCache):
        def __init__(self, max_items=1):
            super().__init__(max_items=max_items)
            caches.append(self)

        def evict(self, key, **kwargs):
            evicted.append(key)
            return super().evict(key, **kwargs)

    install_routing_fakes(
        generator,
        monkeypatch,
        resident_bytes_by_floor={"1楼": 111, "2楼": 222},
        cache_type=TrackingCache,
    )

    def load_floor_image(_floor_dir, floor_number):
        load_states.append((floor_number, caches[0].keys()))
        return image.copy(), tmp_path / f"{floor_number}F.jpg"

    monkeypatch.setattr(generator, "load_floor_image", load_floor_image)
    anchors = [
        {"name": "甲", "floor": "2楼", "anchor": [10.0, 10.0]},
        {"name": "乙", "floor": "1楼", "anchor": [20.0, 20.0]},
    ]
    groups = {
        "1楼": [{"id": "E1", "bbox": [42, 82, 58, 118]}],
        "2楼": [{"id": "E2", "bbox": [42, 82, 58, 118]}],
    }
    shafts = [
        {
            "shaftId": "S1",
            "patientAccessible": True,
            "serviceFloors": ["1楼", "2楼"],
            "floorMappings": {
                "1楼": {"elevatorGroupId": "E1", "confirmed": True},
                "2楼": {"elevatorGroupId": "E2", "confirmed": True},
            },
        }
    ]
    diagnostics = {}

    paths, _ = generator.generate_floor_nav_paths(
        anchors,
        shafts,
        groups,
        tmp_path,
        diagnostics=diagnostics,
    )

    assert len(paths) == 4
    assert len(caches) == 1
    assert caches[0].max_items == 1
    assert caches[0].keys() == ()
    assert caches[0].peak_items == 1
    assert load_states == [(1, ()), (2, ())]
    assert evicted == ["1楼", "2楼"]
    assert diagnostics == {
        "floors": {
            "1楼": {
                "sourceImageBytes": image.nbytes,
                "surfaceResidentBytes": 111,
                "floorResidentBytes": image.nbytes + 111,
            },
            "2楼": {
                "sourceImageBytes": image.nbytes,
                "surfaceResidentBytes": 222,
                "floorResidentBytes": image.nbytes + 222,
            },
        },
        "peakSurfaceItems": 1,
        "peakSurfaceResidentBytes": 222,
        "peakFloorResidentBytes": image.nbytes + 222,
    }


def test_reviewed_door_approach_point_rejects_more_than_one_pixel_residual_snap(
    tmp_path, monkeypatch
):
    generator = load_generator()
    anchors, shafts, groups, floor_dir = make_inputs(tmp_path)
    anchors[0]["doorApproachPoint"] = [12.0, 14.0]
    solve_calls = []

    def fake_solve(_surface, source, target, **_options):
        solve_calls.append((source, target))
        return make_path_result(
            source,
            target,
            source_snap_distance_px=1.01,
        )

    install_routing_fakes(generator, monkeypatch, solve=fake_solve)

    with pytest.raises(RuntimeError, match=r"测试科室/S1.*doorApproachPoint.*1 px"):
        generator.generate_floor_nav_paths(anchors, shafts[:1], groups, floor_dir)

    assert solve_calls[0][0] == (12.0, 14.0)


def test_canonical_inputs_generate_235_geometries_and_470_directed_records(
    tmp_path, monkeypatch
):
    generator = load_generator()
    solve_calls = []

    def fake_solve(_surface, source, target, **_options):
        solve_calls.append((source, target))
        return make_path_result(source, target)

    install_routing_fakes(generator, monkeypatch, solve=fake_solve)
    image = np.full((200, 100, 3), [0xDC, 0xDE, 0xDD], dtype=np.uint8)
    monkeypatch.setattr(
        generator,
        "load_floor_image",
        lambda _floor_dir, floor: (image.copy(), tmp_path / f"{floor}F.jpg"),
    )
    monkeypatch.setattr(
        generator,
        "elevator_anchor_for_group",
        lambda _group, _grid, _shape, *_component: [99.0, 99.0],
    )
    anchors = json.loads(ANCHORS_PATH.read_text(encoding="utf-8"))
    shafts = json.loads(SHAFTS_PATH.read_text(encoding="utf-8"))
    groups = generator.load_commonjs_json(GROUPS_PATH)

    paths, _ = generator.generate_floor_nav_paths(
        anchors,
        shafts,
        groups,
        tmp_path,
    )

    assert len(solve_calls) == 235
    assert len(paths) == 470


def test_unreachable_directions_are_reported_with_department_and_shaft(tmp_path, monkeypatch):
    generator = load_generator()
    anchors, shafts, groups, floor_dir = make_inputs(tmp_path)

    def fail_with_reason(_surface, _source, _target, **options):
        diagnostics = options["diagnostics"]
        assert isinstance(diagnostics, SolverDiagnostics)
        diagnostics.failure_reason = "gridPathUnavailable"
        return None

    install_routing_fakes(generator, monkeypatch, solve=fail_with_reason)
    with pytest.raises(
        RuntimeError,
        match=r"测试科室/S1.*gridPathUnavailable",
    ):
        generator.generate_floor_nav_paths(anchors, shafts[:1], groups, floor_dir)


def test_cli_generates_without_old_output_and_check_detects_drift(tmp_path, monkeypatch):
    generator = load_generator()
    install_routing_fakes(generator, monkeypatch)
    anchors, shafts, groups, floor_dir = make_inputs(tmp_path)
    anchors_path = write_json(tmp_path / "anchors.json", anchors)
    shafts_path = write_json(tmp_path / "shafts.json", shafts[:1])
    groups_path = write_groups(tmp_path / "groups.js", groups)
    output = tmp_path / "does-not-exist" / "floorNavPaths.js"
    runtime_output = tmp_path / "does-not-exist" / "elevatorShafts.js"
    argv = [
        "--department-anchors", str(anchors_path),
        "--shaft-config", str(shafts_path),
        "--elevator-groups", str(groups_path),
        "--floor-dir", str(floor_dir),
        "--output", str(output),
        "--runtime-shaft-output", str(runtime_output),
    ]

    assert generator.main(argv) == 0
    assert output.exists() and runtime_output.exists()
    output.write_text("drift", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        generator.main([*argv, "--check"])
    assert error.value.code == 1
