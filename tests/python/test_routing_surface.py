from __future__ import annotations

import gc
import hashlib
import inspect
import json
import math
import subprocess
import sys
import weakref
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import routing_surface as routing_surface_module
from routing_surface import (
    FloorSurfaceCache,
    FloorRoutingPolicy,
    RoutingSurface,
    build_routing_surface,
    iter_supercover_pixels,
    line_is_safe,
    load_floor_policy,
    near_black_component_report,
    snap_anchor,
    supercover_pixels,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "routing_surface.py"
EXPECTED_SOURCE_HASHES = {
    "1楼": "3bbf64a344793154006b8f44c2b5443a5383c75c67ae3e7d8e67c60d7fcdaaa5",
    "2楼": "f4c5696ece31a8a296bf208d00bca5cceee653f2671f58605be023d2833f9fb7",
    "3楼": "1f25d9c8d373a0d41cce2c1ab1816660532e0a625e84060fbfe94ef5d5582edc",
    "4楼": "278d891b5063907504daeed60ed3afe08a101c858d3c21e0c88685b134c0f2f8",
    "5楼": "0cbe916c28b55380796c0c62978c3370ab0bf850cf8305dc0eb9db21f4a0771c",
    "6楼": "9e79554d68ad23d200248455b2f057109940e41750f18e0834e6f98781e467ce",
    "7楼": "39ca0726726356e42a5bb3b811f4cc674312efd641877ea1f79dd991a5cc422f",
    "8楼": "3171eb0bd55317979db1dea96b49c6c0b06b0262e991e7b2b409ddf0ce0cc481",
    "9楼": "7e0ae10783c4ca912f72b4d31f0a04ecd50524326a86279d3b9021a1ae2a33a6",
    "10楼": "eae557daafdb006b5826d89dd4d689692130808b93776cde1ac16fa69a7b2c59",
    "11楼": "13d37d48c0f7b1c3885d5f894db84da953141763ad8c950081d2de3c25bd1960",
    "12楼": "19cb4eaa54567520f557351aab34df016321998a35f8259fcdf13568412781c1",
    "13楼": "cd5a12c73ac83e9d74d1dc657da4a3f80354c58f162b0522440e6b29bbc26f95",
}
EMPTY_HISTOGRAM = {
    "1": 0,
    "2-3": 0,
    "4-15": 0,
    "16-63": 0,
    "64-255": 0,
    "256-1023": 0,
    "1024+": 0,
}
NEW_DEFAULTS = {
    "endpointBridgeRadiusCells": 4,
    "localCandidateLimit": 96,
    "localSeedIndexRadius": 12,
    "localMaxTurns": 8,
}


def valid_document() -> dict:
    return {
        "schemaVersion": 1,
        "algorithmVersion": "grid-a-star-visible-local-v2",
        "defaults": {
            "cellSizePx": 6,
            "walkTolerance": 14,
            "wallTolerance": 36,
            "clearancePx": 20,
            "hardBlackClosingRadiusPx": 2,
            "hardBlackMinComponentAreaPx": 256,
            "maxAnchorSnapPx": 120,
            **NEW_DEFAULTS,
        },
        "floors": {
            "1楼": {
                "sourceFloorMapSha256": "a" * 64,
                "clearancePx": 20,
                "hardBlackClosingRadiusPx": 2,
                "hardBlackMinComponentAreaPx": 256,
                "clearanceReviewStatus": "pending",
                "clearanceEvidenceId": "",
                "clearanceReviewer": "",
                "clearanceReviewedAt": "",
                "forceWalkablePolygons": [],
                "forceBlockedPolygons": [],
            }
        },
    }


def fixture_policy(**changes):
    values = {
        "floor": "1楼",
        "algorithm_version": "grid-a-star-visible-local-v2",
        "source_floor_map_sha256": "a" * 64,
        "cell_size_px": 2,
        "walk_tolerance": 14,
        "wall_tolerance": 36,
        "clearance_px": 1,
        "hard_black_closing_radius_px": 0,
        "hard_black_min_component_area_px": 4,
        "clearance_review_status": "pending",
        "clearance_evidence_id": "",
        "clearance_reviewer": "",
        "clearance_reviewed_at": "",
        "max_anchor_snap_px": 4,
        "endpoint_bridge_radius_cells": 4,
        "local_candidate_limit": 96,
        "local_seed_index_radius": 12,
        "local_max_turns": 8,
        "force_walkable_polygons": (),
        "force_blocked_polygons": (),
    }
    values.update(changes)
    return FloorRoutingPolicy(**values)


def test_policy_covers_all_thirteen_authoritative_maps():
    document = json.loads(
        (PROJECT_ROOT / "config" / "routing-policy.json").read_text(encoding="utf-8")
    )

    assert document["schemaVersion"] == 1
    assert document["algorithmVersion"] == "grid-a-star-visible-local-v2"
    assert document["defaults"] == {
        "cellSizePx": 6,
        "walkTolerance": 14,
        "wallTolerance": 36,
        "clearancePx": 20,
        "hardBlackClosingRadiusPx": 2,
        "hardBlackMinComponentAreaPx": 256,
        "maxAnchorSnapPx": 120,
        **NEW_DEFAULTS,
    }
    assert list(document["floors"]) == [f"{floor}楼" for floor in range(1, 14)]
    assert {
        floor: item["sourceFloorMapSha256"]
        for floor, item in document["floors"].items()
    } == EXPECTED_SOURCE_HASHES
    for item in document["floors"].values():
        assert item["clearancePx"] == 20
        assert item["hardBlackClosingRadiusPx"] == 2
        assert item["hardBlackMinComponentAreaPx"] == 256
        assert item["clearanceReviewStatus"] == "pending"
        assert item["clearanceEvidenceId"] == ""
        assert item["clearanceReviewer"] == ""
        assert item["clearanceReviewedAt"] == ""
        assert item["forceWalkablePolygons"] == []
        assert item["forceBlockedPolygons"] == []


def test_policy_rejects_a_different_source_map_hash():
    document = valid_document()

    with pytest.raises(ValueError, match="source map hash mismatch"):
        load_floor_policy(document, "1楼", "b" * 64)


def test_policy_loads_case_insensitive_hash_and_normalized_polygons():
    document = valid_document()
    document["floors"]["1楼"].update(
        {
            "sourceFloorMapSha256": "A" * 64,
            "clearanceReviewStatus": "approved",
            "clearanceEvidenceId": "surface/1F.png",
            "clearanceReviewer": "reviewer",
            "clearanceReviewedAt": "2026-07-16T12:00:00Z",
            "forceWalkablePolygons": [
                [[0, 0], [50, 10.5], [100, 100]],
            ],
            "forceBlockedPolygons": [
                [[1, 2], [3, 4], [5, 6]],
            ],
        }
    )

    policy = load_floor_policy(document, "1楼", "a" * 64)

    assert isinstance(policy, FloorRoutingPolicy)
    assert policy.floor == "1楼"
    assert policy.algorithm_version == "grid-a-star-visible-local-v2"
    assert policy.source_floor_map_sha256 == "a" * 64
    assert policy.cell_size_px == 6
    assert policy.walk_tolerance == 14
    assert policy.wall_tolerance == 36
    assert policy.clearance_px == 20
    assert policy.hard_black_closing_radius_px == 2
    assert policy.hard_black_min_component_area_px == 256
    assert policy.max_anchor_snap_px == 120
    assert policy.endpoint_bridge_radius_cells == 4
    assert policy.local_candidate_limit == 96
    assert policy.local_seed_index_radius == 12
    assert policy.local_max_turns == 8
    assert policy.clearance_review_status == "approved"
    assert policy.clearance_evidence_id == "surface/1F.png"
    assert policy.clearance_reviewer == "reviewer"
    assert policy.clearance_reviewed_at == "2026-07-16T12:00:00Z"
    assert policy.force_walkable_polygons == (
        ((0.0, 0.0), (50.0, 10.5), (100.0, 100.0)),
    )
    assert policy.force_blocked_polygons == (
        ((1.0, 2.0), (3.0, 4.0), (5.0, 6.0)),
    )


def test_policy_loads_bounded_grid_solver_parameters():
    document = valid_document()
    policy = load_floor_policy(document, "1楼", "a" * 64)

    assert policy.algorithm_version == "grid-a-star-visible-local-v2"
    assert policy.endpoint_bridge_radius_cells == 4
    assert policy.local_candidate_limit == 96
    assert policy.local_seed_index_radius == 12
    assert policy.local_max_turns == 8


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("endpointBridgeRadiusCells", 0),
        ("endpointBridgeRadiusCells", 17),
        ("endpointBridgeRadiusCells", True),
        ("localCandidateLimit", 2),
        ("localCandidateLimit", 97),
        ("localSeedIndexRadius", 0),
        ("localSeedIndexRadius", 65),
        ("localMaxTurns", 0),
        ("localMaxTurns", -1),
        ("localMaxTurns", 9),
    ],
)
def test_policy_rejects_invalid_bounded_solver_parameters(key, value):
    document = valid_document()
    document["defaults"][key] = value

    with pytest.raises(ValueError, match=key):
        load_floor_policy(document, "1楼", "a" * 64)


@pytest.mark.parametrize(
    "status",
    ["pending", "approved", "rejected", "siteConfirmationRequired"],
)
def test_policy_accepts_exact_clearance_review_statuses(status):
    document = valid_document()
    document["floors"]["1楼"]["clearanceReviewStatus"] = status

    assert load_floor_policy(document, "1楼", "a" * 64).clearance_review_status == status


def test_policy_rejects_unknown_clearance_review_status():
    document = valid_document()
    document["floors"]["1楼"]["clearanceReviewStatus"] = "APPROVED"

    with pytest.raises(ValueError, match="clearanceReviewStatus"):
        load_floor_policy(document, "1楼", "a" * 64)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("cellSizePx", 0),
        ("cellSizePx", True),
        ("cellSizePx", "6"),
        ("walkTolerance", -1),
        ("walkTolerance", 256),
        ("wallTolerance", -1),
        ("wallTolerance", 256),
        ("clearancePx", 0),
        ("hardBlackClosingRadiusPx", -1),
        ("hardBlackClosingRadiusPx", 11),
        ("hardBlackMinComponentAreaPx", 0),
        ("hardBlackMinComponentAreaPx", 100001),
        ("maxAnchorSnapPx", 0),
    ],
)
def test_policy_rejects_out_of_range_or_non_integer_values(key, value):
    document = valid_document()
    document["floors"]["1楼"][key] = value

    with pytest.raises(ValueError, match=key):
        load_floor_policy(document, "1楼", "a" * 64)


@pytest.mark.parametrize(
    "value",
    [
        None,
        [[[0, 0], [50, 50]]],
        [[[0, 0], [50, 50], [101, 10]]],
        [[[0, 0], [50, 50], [10]]],
        [[[0, 0], [50, 50], ["10", 10]]],
        [[[0, 0], [50, 50], [True, 10]]],
        [[[0, 0], [50, 50], [math.nan, 10]]],
        [[(0, 0), [50, 50], [100, 100]]],
    ],
)
def test_policy_rejects_malformed_or_out_of_range_polygons(value):
    document = valid_document()
    document["floors"]["1楼"]["forceWalkablePolygons"] = value

    with pytest.raises(ValueError, match="routing polygon"):
        load_floor_policy(document, "1楼", "a" * 64)


@pytest.mark.parametrize("expected_hash", ["a" * 63, "g" * 64])
def test_policy_rejects_malformed_expected_source_hash(expected_hash):
    document = valid_document()
    document["floors"]["1楼"]["sourceFloorMapSha256"] = expected_hash

    with pytest.raises(ValueError, match="sourceFloorMapSha256"):
        load_floor_policy(document, "1楼", "a" * 64)


@pytest.mark.parametrize("source_hash", ["a" * 63, "g" * 64])
def test_policy_rejects_malformed_actual_source_hash(source_hash):
    with pytest.raises(ValueError, match="source SHA-256"):
        load_floor_policy(valid_document(), "1楼", source_hash)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda document: document.update(schemaVersion=2), "schema"),
        (lambda document: document.update(algorithmVersion="astar-v1"), "algorithmVersion"),
        (lambda document: document.pop("defaults"), "defaults"),
        (lambda document: document.update(floors={}), "missing routing policy"),
    ],
)
def test_policy_rejects_invalid_document_structure(mutation, message):
    document = valid_document()
    mutation(document)

    with pytest.raises(ValueError, match=message):
        load_floor_policy(document, "1楼", "a" * 64)


def test_near_black_component_report_emits_fixed_histogram_bins():
    image = np.full((80, 100, 3), 255, dtype=np.uint8)
    image[1:2, 1:2] = [45, 45, 45]
    image[1:2, 5:7] = [0, 0, 0]
    image[5:7, 1:3] = [0, 0, 0]
    image[5:9, 8:12] = [0, 0, 0]
    image[1:9, 20:28] = [0, 0, 0]
    image[20:36, 1:17] = [0, 0, 0]
    image[40:72, 40:72] = [0, 0, 0]
    image[75, 1] = [46, 0, 0]

    report = near_black_component_report(image, 0)

    assert report == {
        "rawNearBlackPixelCount": 1367,
        "connectedComponentCount": 7,
        "componentAreaHistogram": {
            "1": 1,
            "2-3": 1,
            "4-15": 1,
            "16-63": 1,
            "64-255": 1,
            "256-1023": 1,
            "1024+": 1,
        },
    }


def test_near_black_component_report_applies_elliptical_close():
    image = np.full((8, 9, 3), 255, dtype=np.uint8)
    image[2:5, 1:4] = 0
    image[2:5, 5:8] = 0

    without_close = near_black_component_report(image, 0)
    with_close = near_black_component_report(image, 1)

    assert without_close["rawNearBlackPixelCount"] == 18
    assert with_close["rawNearBlackPixelCount"] == 18
    assert without_close["connectedComponentCount"] == 2
    assert with_close["connectedComponentCount"] == 1
    assert with_close["componentAreaHistogram"] == {
        **EMPTY_HISTOGRAM,
        "16-63": 1,
    }


@pytest.mark.parametrize("closing_radius_px", [-1, 11, True, 1.5])
def test_near_black_component_report_rejects_invalid_closing_radius(
    closing_radius_px,
):
    image = np.zeros((2, 2, 3), dtype=np.uint8)

    with pytest.raises(ValueError, match="closing_radius_px"):
        near_black_component_report(image, closing_radius_px)


def test_preflight_cli_verifies_hashes_and_emits_compact_numeric_floor_report(tmp_path):
    floor_map_dir = tmp_path / "maps"
    floor_map_dir.mkdir()
    document = valid_document()
    document["floors"] = {}
    expected_floors = []
    for floor_number in range(1, 14):
        floor = f"{floor_number}楼"
        image = np.full((4, 4, 3), 255, dtype=np.uint8)
        image[1, 1] = 0
        image_path = floor_map_dir / f"{floor_number}F.jpg"
        Image.fromarray(image, "RGB").save(image_path, format="PNG")
        source_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
        document["floors"][floor] = {
            "sourceFloorMapSha256": source_hash,
            "clearancePx": 20,
            "hardBlackClosingRadiusPx": 0,
            "hardBlackMinComponentAreaPx": 256,
            "clearanceReviewStatus": "pending",
            "clearanceEvidenceId": "",
            "clearanceReviewer": "",
            "clearanceReviewedAt": "",
            "forceWalkablePolygons": [],
            "forceBlockedPolygons": [],
        }
        expected_floors.append(
            {
                "floor": floor,
                "sourceFloorMapSha256": source_hash,
                "rawNearBlackPixelCount": 1,
                "connectedComponentCount": 1,
                "componentAreaHistogram": {**EMPTY_HISTOGRAM, "1": 1},
            }
        )
    policy_path = tmp_path / "routing-policy.json"
    policy_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--preflight",
            "--policy",
            str(policy_path),
            "--floor-map-dir",
            str(floor_map_dir),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    expected = {"schemaVersion": 1, "floors": expected_floors}

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == json.dumps(
        expected,
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"


def legacy_supercover_pixels(start, end):
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    steps = max(abs(dx), abs(dy), 1)
    pixels, seen = [], set()
    for index in range(steps + 1):
        ratio = index / steps
        x, y = round(x0 + dx * ratio), round(y0 + dy * ratio)
        candidates = ((x, y),)
        if index and dx and dy:
            previous = pixels[-1]
            candidates = (
                previous,
                (x, previous[1]),
                (previous[0], y),
                (x, y),
            )
        for pixel in candidates:
            if pixel not in seen:
                seen.add(pixel)
                pixels.append(pixel)
    return tuple(pixels)


def test_iter_supercover_matches_legacy_for_10000_seeded_segments():
    rng = np.random.default_rng(20260717)
    for _ in range(10_000):
        start = tuple(int(v) for v in rng.integers(-40, 41, size=2))
        end = tuple(int(v) for v in rng.integers(-40, 41, size=2))
        assert tuple(iter_supercover_pixels(start, end)) == legacy_supercover_pixels(
            start, end
        )


def test_line_is_safe_stops_after_first_blocked_pixel(monkeypatch):
    visited = []

    def pixels(_start, _end):
        for point in ((0, 0), (1, 0)):
            visited.append(point)
            yield point
        raise AssertionError("line check consumed pixels after the blocker")

    mask = np.array([[True, False, True]], dtype=bool)
    safe = RoutingSurface(
        safe_mask=mask,
        raw_obstacle_mask=~mask,
        clearance_field=mask.astype(np.float32),
        buffer_margin_field=mask.astype(np.float32),
        hard_forbidden_mask=~mask,
        cell_size_px=1,
    )
    monkeypatch.setattr(routing_surface_module, "iter_supercover_pixels", pixels)
    assert not line_is_safe(safe, (0, 0), (2, 0))
    assert visited == [(0, 0), (1, 0)]


def test_black_region_and_corner_cut_are_not_safe():
    image = np.full((12, 12, 3), [0xDC, 0xDE, 0xDD], dtype=np.uint8)
    image[5:7, 5:7] = [0, 0, 0]
    surface = build_routing_surface(image, fixture_policy())
    assert line_is_safe(surface, (1, 1), (10, 10)) is False
    assert (5, 5) in set(supercover_pixels((1, 1), (10, 10)))


def test_manual_blocked_polygon_overrides_walkable_gray():
    image = np.full((12, 12, 3), [0xDC, 0xDE, 0xDD], dtype=np.uint8)
    policy = fixture_policy(
        force_blocked_polygons=(
            ((40.0, 0.0), (60.0, 0.0), (60.0, 100.0), (40.0, 100.0)),
        )
    )
    surface = build_routing_surface(image, policy)
    assert line_is_safe(surface, (1, 6), (10, 6)) is False


def test_manual_walkable_polygon_cannot_reopen_a_black_region():
    image = np.full((12, 12, 3), [0xDC, 0xDE, 0xDD], dtype=np.uint8)
    image[4:8, 4:8] = [0, 0, 0]
    policy = fixture_policy(
        force_walkable_polygons=(
            ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)),
        )
    )
    surface = build_routing_surface(image, policy)
    assert surface.hard_forbidden_mask[5, 5]
    assert line_is_safe(surface, (1, 5), (10, 5)) is False


def test_reviewed_walkable_overlay_can_correct_only_small_black_annotation_noise():
    image = np.full((12, 12, 3), [0xDC, 0xDE, 0xDD], dtype=np.uint8)
    image[5, 5] = [0, 0, 0]
    policy = fixture_policy(
        force_walkable_polygons=(
            ((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)),
        )
    )
    surface = build_routing_surface(image, policy)
    assert not surface.hard_forbidden_mask[5, 5]
    assert surface.safe_mask[5, 5]


def test_clearance_field_is_raw_obstacle_distance_not_post_buffer_margin():
    image = np.full((30, 30, 3), [0xDC, 0xDE, 0xDD], dtype=np.uint8)
    image[13:17, 13:17] = [0, 0, 0]
    surface = build_routing_surface(image, fixture_policy(clearance_px=3))
    assert np.all(
        surface.clearance_field[surface.safe_mask]
        >= surface.buffer_margin_field[surface.safe_mask]
    )
    assert np.any(
        surface.clearance_field[surface.safe_mask]
        > surface.buffer_margin_field[surface.safe_mask]
    )


def test_many_black_components_use_one_vectorized_label_lookup():
    image = np.full((128, 128, 3), [0xDC, 0xDE, 0xDD], dtype=np.uint8)
    for y in range(0, 128, 8):
        for x in range(0, 128, 8):
            image[y:y + 2, x:x + 2] = [0, 0, 0]
    surface = build_routing_surface(image, fixture_policy(clearance_px=0))
    assert surface.hard_forbidden_mask.sum() == 16 * 16 * 4
    source = inspect.getsource(build_routing_surface)
    assert "accepted_labels[labels]" in source
    assert "labels == label" not in source


def test_floor_surface_cache_evicts_before_loading_the_next_floor():
    cache = FloorSurfaceCache(max_items=1)
    references = []

    class FakeSurface:
        pass

    def process_floor(key):
        surface = cache.get_or_build(key, FakeSurface)
        references.append(weakref.ref(surface))
        cache.evict(key)

    process_floor("1楼")
    gc.collect()
    assert references[0]() is None
    process_floor("2楼")
    assert cache.keys() == ()
    assert cache.peak_items == 1


def test_cache_refuses_to_build_next_floor_until_explicit_eviction():
    cache = FloorSurfaceCache(max_items=1)
    cache.get_or_build("1楼", lambda: object())
    with pytest.raises(RuntimeError, match="evict"):
        cache.get_or_build("2楼", lambda: object())


def test_surface_resident_bytes_are_bounded_to_declared_arrays():
    image = np.full((30, 30, 3), [0xDC, 0xDE, 0xDD], dtype=np.uint8)
    surface = build_routing_surface(image, fixture_policy())
    expected = sum(array.nbytes for array in (
        surface.safe_mask,
        surface.raw_obstacle_mask,
        surface.clearance_field,
        surface.buffer_margin_field,
        surface.hard_forbidden_mask,
    ))
    assert surface.resident_nbytes == expected
    assert surface.resident_nbytes <= image.shape[0] * image.shape[1] * 11


def test_anchor_snap_stops_at_the_configured_radius():
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    image[10, 10] = [0xDC, 0xDE, 0xDD]
    surface = build_routing_surface(image, fixture_policy(clearance_px=0))
    assert snap_anchor(surface, (10.0, 10.0), max_distance_px=3) is None
    result = snap_anchor(surface, (50.0, 50.0), max_distance_px=3)
    assert result is not None
    assert result.pixel == (10, 10)
    assert result.distance_px == 0
