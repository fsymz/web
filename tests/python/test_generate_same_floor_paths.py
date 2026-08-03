from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from routing_surface import FloorSurfaceCache
from safe_path_solver import PathResult, SolverDiagnostics


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "generate-same-floor-paths.py"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
ANCHORS_PATH = PROJECT_ROOT / "config" / "department-anchors.json"
PUBLIC_PATH = PROJECT_ROOT / "config" / "public-destinations.json"
PROVENANCE_FIELDS = {
    "algorithmVersion",
    "routingPolicySha256",
    "navigationPolicySha256",
    "autoValidationStatus",
    "reviewStatus",
}


def load_generator():
    assert SCRIPT_PATH.exists(), "same-floor generator has not been implemented"
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("generate_same_floor_paths", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def walkable_image():
    return np.full((200, 100, 3), [0xDC, 0xDE, 0xDD], dtype=np.uint8)


def make_path_result(
    source,
    target,
    *,
    source_snap_distance_px=0.0,
    target_snap_distance_px=0.0,
    geometry_sha256="a" * 64,
    solver_quality_status="optimized",
):
    points = (tuple(float(value) for value in source), tuple(float(value) for value in target))
    return PathResult(
        points=points,
        pixel_points=((10, 10), (20, 20)),
        route_length=1.25,
        min_clearance_px=2.5,
        effective_turn_count=0,
        shortest_segment=1.25,
        semantic_point_indexes=(0, 1),
        geometry_sha256=geometry_sha256,
        source_snap_distance_px=source_snap_distance_px,
        target_snap_distance_px=target_snap_distance_px,
        solver_quality_status=solver_quality_status,
    )


def install_routing_fakes(
    generator,
    monkeypatch,
    tmp_path,
    *,
    solve=None,
    resident_bytes_by_floor=None,
    cache_type=FloorSurfaceCache,
):
    resident_bytes_by_floor = resident_bytes_by_floor or {}
    monkeypatch.setattr(
        generator,
        "load_floor_image",
        lambda _floor_dir, floor: (walkable_image(), tmp_path / f"{floor}F.jpg"),
    )
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

    def fake_build_routing_surface(_image, policy):
        return SimpleNamespace(
            resident_nbytes=resident_bytes_by_floor.get(policy.floor, 1234)
        )

    monkeypatch.setattr(
        generator, "load_floor_policy", fake_load_floor_policy, raising=False
    )
    monkeypatch.setattr(
        generator, "build_routing_surface", fake_build_routing_surface, raising=False
    )
    monkeypatch.setattr(
        generator,
        "solve_safe_path",
        solve or (lambda _surface, source, target, **_kwargs: make_path_result(source, target)),
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


def test_moving_routes_solve_each_unordered_pair_once_and_reverse_exactly(
    tmp_path, monkeypatch
):
    generator = load_generator()
    anchors = [
        {"name": "甲", "floor": "1楼", "anchor": [10.0, 10.0]},
        {"name": "乙", "floor": "1楼", "anchor": [40.0, 40.0]},
        {"name": "丙", "floor": "1楼", "anchor": [80.0, 80.0]},
    ]
    public = {
        "publicDestinations": [
            {"name": item["name"], "floor": item["floor"]} for item in anchors
        ]
    }
    names_by_point = {tuple(item["anchor"]): item["name"] for item in anchors}
    solve_calls = []
    solve_options = []
    solver_diagnostics = []

    def fake_solve(_surface, source, target, **options):
        diagnostics = options.pop("diagnostics")
        assert isinstance(diagnostics, SolverDiagnostics)
        solver_diagnostics.append(diagnostics)
        solve_options.append(options)
        left = names_by_point[tuple(source)]
        right = names_by_point[tuple(target)]
        solve_calls.append((left, right))
        geometry_hash = hashlib.sha256(f"{left}|{right}".encode("utf-8")).hexdigest()
        return make_path_result(source, target, geometry_sha256=geometry_hash)

    install_routing_fakes(generator, monkeypatch, tmp_path, solve=fake_solve)
    real_load_json = generator.load_json
    policy_loads = []

    def recording_load_json(path):
        policy_loads.append(Path(path).name)
        return real_load_json(path)

    monkeypatch.setattr(generator, "load_json", recording_load_json)

    paths = generator.generate_same_floor_paths(anchors, public, tmp_path)

    assert solve_calls == [
        ("甲", "乙"),
        ("甲", "丙"),
        ("乙", "丙"),
    ]
    assert paths["乙|||甲"]["points"] == list(reversed(paths["甲|||乙"]["points"]))
    for shared_field in (
        "geometrySha256",
        "routeLength",
        "minClearancePx",
        "effectiveTurnCount",
        "shortestSegment",
        "solverQualityStatus",
    ):
        assert paths["乙|||甲"][shared_field] == paths["甲|||乙"][shared_field]
    assert paths["甲|||乙"]["semanticPointIndexes"] == [0, 1]
    assert paths["乙|||甲"]["semanticPointIndexes"] == [0, 1]
    assert paths["甲|||乙"]["minClearancePx"] == 2.5
    assert paths["乙|||甲"]["effectiveTurnCount"] == 0
    assert paths["甲|||乙"]["solverQualityStatus"] == "optimized"
    assert paths["乙|||甲"]["solverQualityStatus"] == "optimized"
    assert PROVENANCE_FIELDS.isdisjoint(paths["甲|||乙"])
    assert PROVENANCE_FIELDS.isdisjoint(paths["乙|||甲"])
    assert policy_loads == ["routing-policy.json", "navigation-policy.json"]
    expected_solver_options = {
        "max_anchor_snap_px": 120,
        "endpoint_bridge_radius_cells": 4,
        "local_candidate_limit": 96,
        "local_seed_index_radius": 12,
        "local_max_turns": 8,
        "path_distance_tie_tolerance_px": 6,
        "turn_angle_degrees": 25,
    }
    assert solve_options == [expected_solver_options] * 3
    assert len(solver_diagnostics) == 3
    assert all(item.failure_reason is None for item in solver_diagnostics)
def test_path_failure_includes_the_public_solver_reason(tmp_path, monkeypatch):
    generator = load_generator()
    anchors = [
        {"name": "甲", "floor": "1楼", "anchor": [10.0, 10.0]},
        {"name": "乙", "floor": "1楼", "anchor": [90.0, 90.0]},
    ]
    public = {
        "publicDestinations": [
            {"name": item["name"], "floor": item["floor"]}
            for item in anchors
        ]
    }

    def fail_with_reason(_surface, _source, _target, **options):
        diagnostics = options["diagnostics"]
        assert isinstance(diagnostics, SolverDiagnostics)
        diagnostics.failure_reason = "gridPathUnavailable"
        return None

    install_routing_fakes(
        generator,
        monkeypatch,
        tmp_path,
        solve=fail_with_reason,
    )
    with pytest.raises(
        RuntimeError,
        match=r"甲/乙: path not found \(gridPathUnavailable\)",
    ):
        generator.generate_same_floor_paths(anchors, public, tmp_path)


def test_floor_cache_is_capacity_one_and_reports_resident_peak_bytes(
    tmp_path, monkeypatch
):
    generator = load_generator()
    anchors = [
        {"name": "甲", "floor": "2楼", "anchor": [10.0, 10.0]},
        {"name": "乙", "floor": "2楼", "anchor": [80.0, 80.0]},
        {"name": "丙", "floor": "1楼", "anchor": [20.0, 20.0]},
        {"name": "丁", "floor": "1楼", "anchor": [70.0, 70.0]},
    ]
    public = {
        "publicDestinations": [
            {"name": item["name"], "floor": item["floor"]} for item in anchors
        ]
    }
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
        tmp_path,
        resident_bytes_by_floor={"1楼": 111, "2楼": 222},
        cache_type=TrackingCache,
    )

    def load_floor_image(_floor_dir, floor_number):
        load_states.append((floor_number, caches[0].keys()))
        return walkable_image(), tmp_path / f"{floor_number}F.jpg"

    monkeypatch.setattr(generator, "load_floor_image", load_floor_image)
    diagnostics = {}

    paths = generator.generate_same_floor_paths(
        anchors,
        public,
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
    assert diagnostics["peakSurfaceItems"] == 1
    assert diagnostics["peakSurfaceResidentBytes"] == 222
    assert diagnostics["peakFloorResidentBytes"] == walkable_image().nbytes + 222
    assert diagnostics["floors"] == {
        "1楼": {
            "sourceImageBytes": walkable_image().nbytes,
            "surfaceResidentBytes": 111,
            "floorResidentBytes": walkable_image().nbytes + 111,
        },
        "2楼": {
            "sourceImageBytes": walkable_image().nbytes,
            "surfaceResidentBytes": 222,
            "floorResidentBytes": walkable_image().nbytes + 222,
        },
    }


def test_reviewed_door_approach_point_rejects_more_than_one_pixel_residual_snap(
    tmp_path, monkeypatch
):
    generator = load_generator()
    anchors = [
        {
            "name": "甲",
            "floor": "1楼",
            "anchor": [5.0, 5.0],
            "doorApproachPoint": [10.0, 10.0],
        },
        {
            "name": "乙",
            "floor": "1楼",
            "anchor": [95.0, 95.0],
            "doorApproachPoint": [80.0, 80.0],
        },
    ]
    public = {
        "publicDestinations": [
            {"name": item["name"], "floor": item["floor"]} for item in anchors
        ]
    }
    solve_calls = []

    def fake_solve(_surface, source, target, **_options):
        solve_calls.append((source, target))
        return make_path_result(
            source,
            target,
            source_snap_distance_px=0.25,
            target_snap_distance_px=1.01,
        )

    install_routing_fakes(generator, monkeypatch, tmp_path, solve=fake_solve)

    with pytest.raises(RuntimeError, match="doorApproachPoint.*1 px"):
        generator.generate_same_floor_paths(anchors, public, tmp_path)

    assert solve_calls == [((10.0, 10.0), (80.0, 80.0))]


def test_shared_anchor_is_a_single_explicit_colocated_point_without_fake_neighbor(
    tmp_path, monkeypatch
):
    generator = load_generator()
    install_routing_fakes(generator, monkeypatch, tmp_path)
    anchors = [
        {"name": "甲", "floor": "1楼", "anchor": [10.0, 10.0]},
        {"name": "乙", "floor": "1楼", "anchor": [10.0, 10.0]},
        {"name": "丙", "floor": "1楼", "anchor": [80.0, 80.0]},
    ]
    public = {"publicDestinations": [{"name": item["name"], "floor": "1楼"} for item in anchors]}

    paths = generator.generate_same_floor_paths(anchors, public, tmp_path)

    assert paths["甲|||乙"]["coLocated"] is True
    assert paths["甲|||乙"]["points"] == [[10.0, 10.0]]
    assert paths["乙|||甲"]["points"] == [[10.0, 10.0]]
    assert paths["甲|||乙"]["routeLength"] == 0
    assert paths["甲|||丙"]["points"][0] == [10.0, 10.0]
    assert paths["甲|||丙"]["points"][-1] == [80.0, 80.0]


def test_public_destination_set_generates_exactly_260_directed_records(
    tmp_path, monkeypatch
):
    generator = load_generator()
    install_routing_fakes(generator, monkeypatch, tmp_path)
    anchors = json.loads(ANCHORS_PATH.read_text(encoding="utf-8"))
    public = json.loads(PUBLIC_PATH.read_text(encoding="utf-8"))

    paths = generator.generate_same_floor_paths(anchors, public, Path("unused"))

    assert len(paths) == 260
    expected_colocated = [
        ("中药房", "西药房"),
        ("耳鼻喉科门诊", "眼科门诊"),
        ("血液透析科", "内镜诊疗中心"),
        ("病理科", "重症医学科"),
        ("妇产科病房", "产房"),
    ]
    for left, right in expected_colocated:
        for source, target in ((left, right), (right, left)):
            record = paths[f"{source}|||{target}"]
            assert record["coLocated"] is True
            assert len(record["points"]) == 1


def test_route_provenance_header_is_single_strict_and_records_are_compact():
    generator = load_generator()
    record = generator.path_record(
        make_path_result([10.0, 10.0], [20.0, 20.0]),
        floor="1楼",
        image="/assets/floor-maps/1F.jpg",
        image_size=[100, 200],
        source_hash="f" * 64,
        reverse=False,
    )

    rendered = generator.render_commonjs({"甲|||乙": record})

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
    assert exported == {"甲|||乙": record}
    assert PROVENANCE_FIELDS.isdisjoint(exported["甲|||乙"])


def test_generator_does_not_parse_old_output_and_check_detects_drift(tmp_path, monkeypatch):
    generator = load_generator()
    install_routing_fakes(generator, monkeypatch, tmp_path)
    anchors = [
        {"name": "甲", "floor": "1楼", "anchor": [10.0, 10.0]},
        {"name": "乙", "floor": "1楼", "anchor": [80.0, 80.0]},
    ]
    public = {"publicDestinations": [{"name": item["name"], "floor": "1楼"} for item in anchors]}
    anchors_path = tmp_path / "anchors.json"
    public_path = tmp_path / "public.json"
    output = tmp_path / "sameFloorPaths.js"
    anchors_path.write_text(json.dumps(anchors, ensure_ascii=False), encoding="utf-8")
    public_path.write_text(json.dumps(public, ensure_ascii=False), encoding="utf-8")
    output.write_text("this is deliberately not parseable CommonJS", encoding="utf-8")
    argv = [
        "--department-anchors", str(anchors_path),
        "--public-destinations", str(public_path),
        "--floor-dir", str(tmp_path),
        "--output", str(output),
    ]

    assert generator.main(argv) == 0
    generated = output.read_text(encoding="utf-8")
    assert "甲|||乙" in generated
    output.write_text(generated + "// drift", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        generator.main([*argv, "--check"])
    assert error.value.code == 1
