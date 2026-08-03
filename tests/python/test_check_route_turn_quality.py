from __future__ import annotations

import importlib.util
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check-route-turn-quality.py"
ROUTING_POLICY_PATH = PROJECT_ROOT / "config" / "routing-policy.json"
NAVIGATION_POLICY_PATH = PROJECT_ROOT / "config" / "navigation-policy.json"
PROVENANCE_FIELDS = {
    "algorithmVersion",
    "routingPolicySha256",
    "navigationPolicySha256",
    "autoValidationStatus",
    "reviewStatus",
}


def load_checker():
    assert SCRIPT_PATH.exists(), "route-turn quality checker has not been implemented"
    spec = importlib.util.spec_from_file_location("check_route_turn_quality", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def canonical_json_sha256(value):
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def geometry_sha256(points):
    forward = json.dumps(points, ensure_ascii=False, separators=(",", ":"))
    reverse = json.dumps(list(reversed(points)), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(min(forward, reverse).encode("utf-8")).hexdigest()


def policy_values():
    routing = json.loads(ROUTING_POLICY_PATH.read_text(encoding="utf-8"))
    navigation = json.loads(NAVIGATION_POLICY_PATH.read_text(encoding="utf-8"))
    return (
        routing["algorithmVersion"],
        canonical_json_sha256(routing),
        canonical_json_sha256(navigation),
    )


def provenance_document(**overrides):
    algorithm, routing_hash, navigation_hash = policy_values()
    document = {
        "schemaVersion": 1,
        "algorithmVersion": algorithm,
        "routingPolicySha256": routing_hash,
        "navigationPolicySha256": navigation_hash,
        "autoValidationStatus": "passed",
        "reviewStatus": "pending",
    }
    document.update(overrides)
    return document


def provenance_json(**overrides):
    return json.dumps(
        provenance_document(**overrides),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def six_same_direction_turns():
    return [
        [50, 50],
        [55, 50],
        [55, 55],
        [45, 55],
        [45, 45],
        [60, 45],
        [60, 60],
        [40, 60],
    ]


def moving_record(points, *, solver_status="optimized", **overrides):
    checker = load_checker()
    metrics = checker.analyze_geometry(points, (100, 100))
    record = {
        "floor": "1F",
        "image": "/assets/floor-maps/1F.jpg",
        "imageSize": [100, 100],
        "routeLengthUnit": "imageWidthPercent",
        "sourceFloorMapSha256": "f" * 64,
        "geometrySha256": geometry_sha256(points),
        "solverQualityStatus": solver_status,
        "points": points,
        "routeLength": 42.0,
        "minClearancePx": 3.0,
        "minClearanceImageWidthPercent": 3.0,
        "effectiveTurnCount": metrics.spoken_turn_count,
        "shortestSegment": 1.0,
        "semanticPointIndexes": list(metrics.semantic_point_indexes),
    }
    record.update(overrides)
    return record


def review(geometry_hash, decision="approvedNecessaryComplexGeometry", **overrides):
    item = {
        "geometrySha256": geometry_hash,
        "decision": decision,
        "reviewer": "QA Reviewer",
        "reviewedAt": "2026-08-01",
        "reason": "Hand-inspected route is necessary and clear.",
    }
    item.update(overrides)
    return item


def write_commonjs(path, records, *, headers=None):
    payload = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    header_values = [provenance_json()] if headers is None else headers
    header_lines = "".join(
        f"// route-provenance: {header}\n" for header in header_values
    )
    path.write_text(
        f"// generated fixture\n{header_lines}module.exports = {payload};\n",
        encoding="utf-8",
    )


def run_checker(
    tmp_path,
    same_records,
    *,
    floor_records=None,
    reviews=None,
    extra_args=None,
    same_headers=None,
    floor_headers=None,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    same_path = tmp_path / "sameFloorPaths.js"
    floor_path = tmp_path / "floorNavPaths.js"
    review_path = tmp_path / "route-turn-reviews.json"
    write_commonjs(same_path, same_records, headers=same_headers)
    write_commonjs(floor_path, floor_records or {}, headers=floor_headers)
    review_path.write_text(
        json.dumps({"schemaVersion": 1, "reviews": reviews or []}), encoding="utf-8"
    )
    args = [
        sys.executable,
        str(SCRIPT_PATH),
        "--same-floor-paths",
        str(same_path),
        "--floor-nav-paths",
        str(floor_path),
        "--review-decisions",
        str(review_path),
    ]
    if extra_args:
        args.extend(str(item) for item in extra_args)
    input_bytes = {path: path.read_bytes() for path in (same_path, floor_path, review_path)}
    result = subprocess.run(args, cwd=PROJECT_ROOT, text=True, capture_output=True)
    result.input_bytes = input_bytes
    return result, same_path, floor_path, review_path


def provenance_json_without(field):
    document = provenance_document()
    del document[field]
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def test_route_provenance_valid_header_and_compact_records_pass(tmp_path):
    record = moving_record([[0, 0], [10, 0]])

    result, *_ = run_checker(tmp_path, {"A|||B": record})

    assert PROVENANCE_FIELDS.isdisjoint(record)
    assert result.returncode == 0, result.stdout + result.stderr


def test_approved_route_provenance_requires_explicit_release_expectation(tmp_path):
    record = moving_record([[0, 0], [10, 0]])
    approved_header = provenance_json(reviewStatus="approved")

    candidate, *_ = run_checker(
        tmp_path / "candidate",
        {"A|||B": record},
        same_headers=[approved_header],
        floor_headers=[approved_header],
    )
    release, *_ = run_checker(
        tmp_path / "release",
        {"A|||B": record},
        same_headers=[approved_header],
        floor_headers=[approved_header],
        extra_args=["--expected-review-status", "approved"],
    )

    assert candidate.returncode == 2
    assert "reviewStatus must equal pending" in candidate.stderr
    assert release.returncode == 0, release.stdout + release.stderr


@pytest.mark.parametrize(
    ("target", "headers"),
    [
        ("same", []),
        ("floor", []),
        ("same", [provenance_json(), provenance_json()]),
        ("floor", [provenance_json(), provenance_json()]),
    ],
    ids=["same-missing", "floor-missing", "same-duplicate", "floor-duplicate"],
)
def test_route_provenance_missing_or_duplicate_header_fails(
    tmp_path, target, headers
):
    options = {f"{target}_headers": headers}

    result, *_ = run_checker(tmp_path, {}, **options)

    assert result.returncode == 2
    assert "route-provenance" in result.stderr


@pytest.mark.parametrize(
    ("header", "expected_error"),
    [
        ("{", "route-provenance"),
        ("[]", "object"),
        (
            provenance_json().replace(
                '"schemaVersion":1',
                '"schemaVersion":1,"schemaVersion":1',
                1,
            ),
            "duplicate",
        ),
        (provenance_json(extra="unexpected"), "exactly"),
        (provenance_json_without("reviewStatus"), "exactly"),
        (provenance_json(schemaVersion=2), "schemaVersion"),
        (provenance_json(schemaVersion=True), "schemaVersion"),
        (provenance_json(schemaVersion=float("nan")), "non-standard JSON constant"),
        (provenance_json(schemaVersion=float("inf")), "non-standard JSON constant"),
        (provenance_json(algorithmVersion="obsolete"), "algorithmVersion"),
        (provenance_json(algorithmVersion=1), "algorithmVersion"),
        (provenance_json(routingPolicySha256="0" * 64), "routingPolicySha256"),
        (provenance_json(routingPolicySha256=1), "routingPolicySha256"),
        (provenance_json(navigationPolicySha256="0" * 64), "navigationPolicySha256"),
        (provenance_json(navigationPolicySha256=None), "navigationPolicySha256"),
        (provenance_json(autoValidationStatus="failed"), "autoValidationStatus"),
        (provenance_json(autoValidationStatus=True), "autoValidationStatus"),
        (provenance_json(reviewStatus="approved"), "reviewStatus"),
        (provenance_json(reviewStatus=None), "reviewStatus"),
    ],
    ids=[
        "malformed",
        "non-object",
        "duplicate-key",
        "extra-key",
        "missing-key",
        "wrong-schema",
        "boolean-schema",
        "nan",
        "infinity",
        "stale-algorithm",
        "algorithm-type",
        "stale-routing-hash",
        "routing-hash-type",
        "stale-navigation-hash",
        "navigation-hash-type",
        "invalid-auto-status",
        "auto-status-type",
        "invalid-review-status",
        "review-status-type",
    ],
)
def test_route_provenance_invalid_header_fails(tmp_path, header, expected_error):
    result, *_ = run_checker(tmp_path, {}, same_headers=[header])

    assert result.returncode == 2
    assert expected_error in result.stderr


def test_route_provenance_header_must_immediately_precede_export(tmp_path):
    header_with_gap = provenance_json() + "\n// intervening comment"

    result, *_ = run_checker(tmp_path, {}, same_headers=[header_with_gap])

    assert result.returncode == 2
    assert "immediately before module.exports" in result.stderr


@pytest.mark.parametrize("field", sorted(PROVENANCE_FIELDS))
def test_route_provenance_record_level_copy_fails(tmp_path, field):
    record = moving_record(
        [[0, 0], [10, 0]],
        **{field: provenance_document()[field]},
    )

    result, *_ = run_checker(tmp_path, {"A|||B": record})

    assert result.returncode == 1
    assert field in result.stdout


@pytest.mark.parametrize("level", ["map", "record"])
def test_commonjs_route_input_rejects_proto_keys(tmp_path, level):
    record = moving_record([[0, 0], [10, 0]])
    if level == "map":
        records = {"__proto__": record}
    else:
        records = {
            "A|||B": {
                **record,
                "__proto__": {"algorithmVersion": "override"},
            }
        }

    result, *_ = run_checker(tmp_path, records)

    assert result.returncode == 2
    assert "__proto__" in result.stderr


def test_hard_zigzag_has_six_spoken_turns():
    checker = load_checker()
    hard_zigzag = [
        (0, 0),
        (1, 1),
        (2, 0),
        (3, 1),
        (4, 0),
        (5, 1),
        (6, 0),
        (7, 1),
    ]

    metrics = checker.analyze_geometry(hard_zigzag, (100, 100))

    assert metrics.spoken_turn_count == 6
    assert metrics.semantic_point_indexes == (0, 1, 2, 3, 4, 5, 6, 7)


@pytest.mark.parametrize("reverse", [False, True])
def test_continuous_micro_zigzag_is_blocked_in_both_directions(reverse):
    checker = load_checker()
    micro_zigzag = [
        (0, 0),
        (5, 1),
        (10, 0),
        (15, 1),
        (20, 0),
        (25, 1),
        (30, 0),
        (35, 1),
    ]
    if reverse:
        micro_zigzag.reverse()

    metrics = checker.analyze_geometry(micro_zigzag, (100, 100))

    assert metrics.spoken_turn_count == 0
    assert metrics.low_angle_alternations >= 5
    assert metrics.has_micro_zigzag is True


@pytest.mark.parametrize("reverse", [False, True])
def test_pediatrics_s1_semantic_turns_reset_micro_zigzag_runs(reverse):
    checker = load_checker()
    points = [
        [87.039, 61.770],
        [86.251, 56.967],
        [86.144, 56.632],
        [85.714, 56.213],
        [75.725, 49.344],
        [64.769, 40.045],
        [57.895, 38.202],
        [57.465, 38.034],
        [56.176, 31.667],
        [55.961, 31.332],
        [31.364, 30.159],
        [31.149, 30.075],
        [29.753, 26.724],
        [29.592, 26.669],
    ]
    if reverse:
        points.reverse()

    metrics = checker.analyze_geometry(points, (5587, 7163))

    assert metrics.spoken_turn_count == 5
    assert metrics.low_angle_alternations == 1
    assert metrics.has_micro_zigzag is False


def test_large_semantic_turn_splits_small_angle_alternation_runs():
    checker = load_checker()
    points = [
        (10.00000000, 10.00000000),
        (20.00000000, 10.00000000),
        (29.84807753, 11.73648178),
        (39.84807753, 11.73648178),
        (49.69615506, 13.47296356),
        (47.95967328, 23.32104109),
        (47.95967328, 33.32104109),
        (46.22319150, 43.16911862),
        (46.22319150, 53.16911862),
    ]

    metrics = checker.analyze_geometry(points, (100, 100))

    assert metrics.spoken_turn_count == 1
    assert metrics.low_angle_alternations == 2
    assert metrics.has_micro_zigzag is False


def test_geometry_metrics_are_aspect_corrected_and_ignore_duplicates():
    checker = load_checker()

    metrics = checker.analyze_geometry(
        [(0, 0), (1, 1), (1, 1), (2, 1)],
        (100, 10),
    )

    assert metrics.spoken_turn_count == 0
    assert metrics.semantic_point_indexes == (0, 3)


def test_u_turn_counts_as_one_spoken_turn():
    checker = load_checker()

    metrics = checker.analyze_geometry([(0, 0), (1, 0), (0, 0)], (100, 100))

    assert metrics.spoken_turn_count == 1
    assert metrics.semantic_point_indexes == (0, 1, 2)


def test_five_turns_pass_without_review_and_default_run_is_read_only(tmp_path):
    points = [[0, 0], [1, 1], [2, 0], [3, 1], [4, 0], [5, 1], [6, 0]]
    result, same_path, floor_path, review_path = run_checker(
        tmp_path, {"A|||B": moving_record(points)}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "routes=1" in result.stdout
    assert all(path.read_bytes() == content for path, content in result.input_bytes.items())
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "floorNavPaths.js",
        "route-turn-reviews.json",
        "sameFloorPaths.js",
    ]


def test_six_turns_fail_without_hash_bound_review(tmp_path):
    points = six_same_direction_turns()

    result, *_ = run_checker(tmp_path, {"A|||B": moving_record(points)})

    assert result.returncode == 1
    assert "reviewRequired" in result.stdout
    assert "A|||B" in result.stdout


def test_matching_high_turn_approval_passes_and_stale_hash_fails(tmp_path):
    points = six_same_direction_turns()
    record = moving_record(points)

    approved, *_ = run_checker(
        tmp_path / "approved",
        {"A|||B": record},
        reviews=[review(record["geometrySha256"])],
    )
    stale, *_ = run_checker(
        tmp_path / "stale",
        {"A|||B": record},
        reviews=[review("a" * 64)],
    )

    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert stale.returncode == 1
    assert "staleReview" in stale.stdout


def test_fallback_requires_fallback_specific_approval(tmp_path):
    points = [[0, 0], [10, 0]]
    record = moving_record(points, solver_status="fallbackCandidateLimit")

    wrong, *_ = run_checker(
        tmp_path / "wrong",
        {"A|||B": record},
        reviews=[review(record["geometrySha256"])],
    )
    approved, *_ = run_checker(
        tmp_path / "approved",
        {"A|||B": record},
        reviews=[
            review(
                record["geometrySha256"],
                decision="approvedFallbackAfterVisualReview",
            )
        ],
    )

    assert wrong.returncode == 1
    assert "fallbackApprovalRequired" in wrong.stdout
    assert approved.returncode == 0, approved.stdout + approved.stderr


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        ({"effectiveTurnCount": 99}, "effectiveTurnCount"),
        ({"semanticPointIndexes": [0, 0, 2]}, "semanticPointIndexes"),
        ({"geometrySha256": "b" * 64}, "geometrySha256"),
        ({"routeLength": 0}, "routeLength"),
        ({"minClearancePx": 0}, "minClearancePx"),
        ({"solverQualityStatus": "mystery"}, "solverQualityStatus"),
    ],
)
def test_malformed_or_mismatched_moving_metadata_fails(
    tmp_path, overrides, expected_error
):
    points = [[0, 0], [10, 0], [10, 10]]
    record = moving_record(points, **overrides)

    result, *_ = run_checker(tmp_path, {"A|||B": record})

    assert result.returncode == 1
    assert expected_error in result.stdout


def test_micro_zigzag_is_a_blocking_cli_failure(tmp_path):
    points = [[0, 0], [5, 1], [10, 0], [15, 1], [20, 0], [25, 1], [30, 0], [35, 1]]

    result, *_ = run_checker(tmp_path, {"A|||B": moving_record(points)})

    assert result.returncode == 1
    assert "microZigzag" in result.stdout


@pytest.mark.parametrize(
    "bad_review",
    [
        review("a" * 64, unexpected="nope"),
        review("a" * 64, reviewedAt="2026-02-30"),
        review("A" * 64),
        review("a" * 64, reviewer=""),
    ],
)
def test_review_schema_is_strict_and_invalid_input_returns_two(tmp_path, bad_review):
    result, *_ = run_checker(tmp_path, {}, reviews=[bad_review])

    assert result.returncode == 2
    assert "review decisions" in result.stderr


def test_conflicting_duplicate_review_decisions_fail(tmp_path):
    geometry_hash = "a" * 64
    result, *_ = run_checker(
        tmp_path,
        {},
        reviews=[
            review(geometry_hash),
            review(geometry_hash, decision="approvedFallbackAfterVisualReview"),
        ],
    )

    assert result.returncode == 2
    assert "conflicting" in result.stderr


def test_duplicate_review_decisions_fail_even_when_they_match(tmp_path):
    geometry_hash = "a" * 64
    result, *_ = run_checker(
        tmp_path,
        {},
        reviews=[review(geometry_hash), review(geometry_hash)],
    )

    assert result.returncode == 2
    assert "duplicate" in result.stderr


def test_route_provenance_commonjs_parser_does_not_execute_javascript(tmp_path):
    marker = tmp_path / "executed.txt"
    same_path = tmp_path / "sameFloorPaths.js"
    floor_path = tmp_path / "floorNavPaths.js"
    review_path = tmp_path / "reviews.json"
    report_dir = tmp_path / "report"
    same_path.write_text(
        "// generated fixture\n"
        f"// route-provenance: {provenance_json()}\n"
        f'module.exports = require("fs").writeFileSync({json.dumps(str(marker))}, "bad");',
        encoding="utf-8",
    )
    write_commonjs(floor_path, {})
    review_path.write_text('{"schemaVersion":1,"reviews":[]}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--same-floor-paths",
            str(same_path),
            "--floor-nav-paths",
            str(floor_path),
            "--review-decisions",
            str(review_path),
            "--report-dir",
            str(report_dir),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert not marker.exists()
    assert not report_dir.exists()


def test_explicit_report_writes_full_local_evidence_for_shared_reverse_geometry(tmp_path):
    points = six_same_direction_turns()
    forward = moving_record(points)
    reverse = moving_record(list(reversed(points)))
    floor_dir = tmp_path / "maps"
    report_dir = tmp_path / "local-review"
    floor_dir.mkdir()
    Image.new("RGB", (100, 100), "white").save(floor_dir / "1F.jpg")

    result, same_path, floor_path, review_path = run_checker(
        tmp_path / "inputs",
        {"A|||B": forward, "B|||A": reverse},
        reviews=[review(forward["geometrySha256"])],
        extra_args=["--floor-dir", floor_dir, "--report-dir", report_dir],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(path.read_bytes() == content for path, content in result.input_bytes.items())
    assert (report_dir / "summary.json").is_file()
    assert (report_dir / "all-routes.csv").is_file()
    assert (report_dir / "high-turn-routes.csv").is_file()
    assert (report_dir / "manifest.json").is_file()
    assert sorted(path.name for path in (report_dir / "route-images").glob("*.jpg")) == [
        "route-001.jpg"
    ]
    route_image = Image.open(report_dir / "route-images" / "route-001.jpg").convert("RGB")
    red_pixels = [
        (x, y)
        for y in range(route_image.height)
        for x in range(route_image.width)
        if route_image.getpixel((x, y))[0] > 170
        and route_image.getpixel((x, y))[1] < 110
        and route_image.getpixel((x, y))[2] < 110
    ]
    assert max(x for x, _ in red_pixels) - min(x for x, _ in red_pixels) > 100
    assert max(y for _, y in red_pixels) - min(y for _, y in red_pixels) > 100
    assert sorted(path.name for path in (report_dir / "sheets").glob("*.jpg")) == [
        "sheet-01.jpg"
    ]
    manifest = json.loads((report_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["routes"][0]["routeKeys"] == ["A|||B", "B|||A"]
    with (report_dir / "all-routes.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["key"] for row in rows] == ["A|||B", "B|||A"]


def test_missing_floor_map_fails_only_when_rendering_is_requested(tmp_path):
    points = six_same_direction_turns()
    record = moving_record(points)
    decisions = [review(record["geometrySha256"])]

    no_report, *_ = run_checker(tmp_path / "plain", {"A|||B": record}, reviews=decisions)
    report, *_ = run_checker(
        tmp_path / "render",
        {"A|||B": record},
        reviews=decisions,
        extra_args=["--floor-dir", tmp_path / "missing", "--report-dir", tmp_path / "report"],
    )

    assert no_report.returncode == 0, no_report.stdout + no_report.stderr
    assert report.returncode == 2
    assert "floor map" in report.stderr


def test_low_turn_fallback_is_rendered_but_not_listed_as_high_turn(tmp_path):
    record = moving_record([[10, 10], [90, 10]], solver_status="fallbackCandidateLimit")
    floor_dir = tmp_path / "maps"
    report_dir = tmp_path / "report"
    floor_dir.mkdir()
    Image.new("RGB", (100, 100), "white").save(floor_dir / "1F.jpg")

    result, *_ = run_checker(
        tmp_path / "inputs",
        {"A|||B": record},
        reviews=[
            review(
                record["geometrySha256"],
                decision="approvedFallbackAfterVisualReview",
            )
        ],
        extra_args=["--floor-dir", floor_dir, "--report-dir", report_dir],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((report_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["routes"][0]["routeKeys"] == ["A|||B"]
    with (report_dir / "high-turn-routes.csv").open(encoding="utf-8", newline="") as handle:
        assert list(csv.DictReader(handle)) == []


@pytest.mark.parametrize(
    ("record", "expected_error"),
    [
        (moving_record(six_same_direction_turns()), "reviewRequired"),
        (
            moving_record(
                [[10, 10], [90, 10]], solver_status="fallbackCandidateLimit"
            ),
            "fallbackApprovalRequired",
        ),
    ],
)
def test_unapproved_review_required_routes_still_export_first_visual_evidence(
    tmp_path, record, expected_error
):
    floor_dir = tmp_path / "maps"
    report_dir = tmp_path / "report"
    floor_dir.mkdir()
    Image.new("RGB", (100, 100), "white").save(floor_dir / "1F.jpg")

    result, *_ = run_checker(
        tmp_path / "inputs",
        {"A|||B": record},
        extra_args=["--floor-dir", floor_dir, "--report-dir", report_dir],
    )

    assert result.returncode == 1
    assert expected_error in result.stdout
    assert (report_dir / "summary.json").is_file()
    assert (report_dir / "manifest.json").is_file()
    assert (report_dir / "route-images" / "route-001.jpg").is_file()
    assert (report_dir / "sheets" / "sheet-01.jpg").is_file()


def test_low_turn_geometry_without_decision_may_repeat_across_map_contexts(tmp_path):
    points = [[10, 10], [90, 10]]
    first = moving_record(points)
    second = moving_record(
        list(reversed(points)),
        floor="2F",
        image="/assets/floor-maps/2F.jpg",
        imageSize=[100, 120],
        sourceFloorMapSha256="e" * 64,
    )

    result, *_ = run_checker(
        tmp_path,
        {"A|||B": first, "B|||A": second},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "geometryContextCollision" not in result.stdout


def test_low_turn_geometry_with_decision_is_rejected_across_map_contexts(tmp_path):
    points = [[10, 10], [90, 10]]
    first = moving_record(points)
    second = moving_record(
        list(reversed(points)),
        floor="2F",
        image="/assets/floor-maps/2F.jpg",
        imageSize=[100, 120],
        sourceFloorMapSha256="e" * 64,
    )

    result, *_ = run_checker(
        tmp_path,
        {"A|||B": first, "B|||A": second},
        reviews=[review(first["geometrySha256"])],
    )

    assert result.returncode == 1
    assert "geometryContextCollision" in result.stdout


def test_high_turn_geometry_in_different_map_contexts_is_rejected_and_not_shared(
    tmp_path,
):
    points = six_same_direction_turns()
    first = moving_record(points)
    second = moving_record(
        list(reversed(points)),
        floor="2F",
        image="/assets/floor-maps/2F.jpg",
        imageSize=[100, 120],
        sourceFloorMapSha256="e" * 64,
    )
    floor_dir = tmp_path / "maps"
    report_dir = tmp_path / "report"
    floor_dir.mkdir()
    Image.new("RGB", (100, 100), "white").save(floor_dir / "1F.jpg")
    Image.new("RGB", (100, 120), "white").save(floor_dir / "2F.jpg")

    result, *_ = run_checker(
        tmp_path / "inputs",
        {"A|||B": first, "B|||A": second},
        reviews=[review(first["geometrySha256"])],
        extra_args=["--floor-dir", floor_dir, "--report-dir", report_dir],
    )

    assert result.returncode == 1
    assert "geometryContextCollision" in result.stdout
    assert len(list((report_dir / "route-images").glob("route-*.jpg"))) == 2
    manifest = json.loads((report_dir / "manifest.json").read_text(encoding="utf-8"))
    assert [item["routeKeys"] for item in manifest["routes"]] == [
        ["A|||B"],
        ["B|||A"],
    ]


def test_report_path_overlaps_are_rejected_before_any_input_is_modified(tmp_path):
    cases = []

    exact_report = tmp_path / "exact-report"
    exact_report.mkdir()
    exact_same = tmp_path / "exact-same.js"
    exact_floor = tmp_path / "exact-floor.js"
    exact_review = exact_report / "summary.json"
    write_commonjs(exact_same, {})
    write_commonjs(exact_floor, {})
    exact_review.write_text('{"schemaVersion":1,"reviews":[]}', encoding="utf-8")
    cases.append((exact_same, exact_floor, exact_review, tmp_path / "maps-a", exact_report))

    parent_report = tmp_path / "input-parent"
    parent_report.mkdir()
    parent_same = parent_report / "sameFloorPaths.js"
    parent_floor = parent_report / "floorNavPaths.js"
    parent_review = parent_report / "reviews.json"
    write_commonjs(parent_same, {})
    write_commonjs(parent_floor, {})
    parent_review.write_text('{"schemaVersion":1,"reviews":[]}', encoding="utf-8")
    cases.append((parent_same, parent_floor, parent_review, tmp_path / "maps-b", parent_report))

    map_report = tmp_path / "map-context"
    map_report.mkdir()
    Image.new("RGB", (100, 100), "white").save(map_report / "1F.jpg")
    map_inputs = tmp_path / "map-inputs"
    map_inputs.mkdir()
    map_same = map_inputs / "sameFloorPaths.js"
    map_floor = map_inputs / "floorNavPaths.js"
    map_review = map_inputs / "reviews.json"
    record = moving_record(six_same_direction_turns())
    write_commonjs(map_same, {"A|||B": record})
    write_commonjs(map_floor, {})
    map_review.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "reviews": [review(record["geometrySha256"])],
            }
        ),
        encoding="utf-8",
    )
    cases.append((map_same, map_floor, map_review, map_report, map_report))

    for same_path, floor_path, review_path, floor_dir, report_dir in cases:
        inputs = [same_path, floor_path, review_path]
        if (floor_dir / "1F.jpg").is_file():
            inputs.append(floor_dir / "1F.jpg")
        before = {path: path.read_bytes() for path in inputs}
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--same-floor-paths",
                str(same_path),
                "--floor-nav-paths",
                str(floor_path),
                "--review-decisions",
                str(review_path),
                "--floor-dir",
                str(floor_dir),
                "--report-dir",
                str(report_dir),
            ],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )

        assert result.returncode == 2
        assert "report directory overlaps" in result.stderr
        assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize("invalid_hash", [[], {}])
def test_unhashable_geometry_hash_fails_deterministically_without_traceback(
    tmp_path, invalid_hash
):
    record = moving_record([[0, 0], [10, 0]], geometrySha256=invalid_hash)

    result, *_ = run_checker(tmp_path, {"A|||B": record})

    assert result.returncode == 1
    assert "geometrySha256" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr


def test_nonmoving_fallback_requires_hash_bound_fallback_approval(tmp_path):
    colocated = {
        "floor": "1F",
        "image": "/assets/floor-maps/1F.jpg",
        "imageSize": [100, 100],
        "points": [[10, 10]],
        "routeLength": 0,
        "coLocated": True,
    }
    fallback = moving_record(
        [[10, 10]],
        solver_status="fallbackCandidateLimit",
        coLocated=True,
        routeLength=0,
    )

    ordinary, *_ = run_checker(tmp_path / "ordinary", {"A|||B": colocated})
    unapproved, *_ = run_checker(tmp_path / "unapproved", {"A|||B": fallback})
    approved, *_ = run_checker(
        tmp_path / "approved",
        {"A|||B": fallback},
        reviews=[
            review(
                fallback["geometrySha256"],
                decision="approvedFallbackAfterVisualReview",
            )
        ],
    )

    assert ordinary.returncode == 0, ordinary.stdout + ordinary.stderr
    assert unapproved.returncode == 1
    assert "fallbackApprovalRequired" in unapproved.stdout
    assert approved.returncode == 0, approved.stdout + approved.stderr


def test_csv_formula_prefixes_are_escaped_without_changing_manifest_keys(tmp_path):
    high_key = "=2+2"
    high = moving_record(six_same_direction_turns(), floor="+SUM(A1)")
    low_key = "\tcmd"
    low = moving_record(
        [[0, 0], [10, 0]],
        floor="\rfloor",
        solver_status="@SUM(1,1)",
    )
    floor_dir = tmp_path / "maps"
    report_dir = tmp_path / "report"
    floor_dir.mkdir()
    Image.new("RGB", (100, 100), "white").save(floor_dir / "1F.jpg")

    result, *_ = run_checker(
        tmp_path / "inputs",
        {high_key: high, low_key: low},
        reviews=[review(high["geometrySha256"])],
        extra_args=["--floor-dir", floor_dir, "--report-dir", report_dir],
    )

    assert result.returncode == 1
    with (report_dir / "all-routes.csv").open(encoding="utf-8", newline="") as handle:
        rows = {row["key"]: row for row in csv.DictReader(handle)}
    assert rows["'=2+2"]["floor"] == "'+SUM(A1)"
    assert rows["'\tcmd"]["floor"] == "'\rfloor"
    assert rows["'\tcmd"]["solverQualityStatus"] == "'@SUM(1,1)"
    manifest = json.loads((report_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["routes"][0]["routeKeys"] == [high_key]


def test_boolean_review_schema_version_is_not_integer_one(tmp_path):
    same_path = tmp_path / "same.js"
    floor_path = tmp_path / "floor.js"
    review_path = tmp_path / "reviews.json"
    write_commonjs(same_path, {})
    write_commonjs(floor_path, {})
    review_path.write_text('{"schemaVersion":true,"reviews":[]}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--same-floor-paths",
            str(same_path),
            "--floor-nav-paths",
            str(floor_path),
            "--review-decisions",
            str(review_path),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "schemaVersion 1" in result.stderr


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_commonjs_parser_rejects_nonstandard_json_constants(tmp_path, constant):
    same_path = tmp_path / "same.js"
    floor_path = tmp_path / "floor.js"
    review_path = tmp_path / "reviews.json"
    same_path.write_text(
        "// generated fixture\n"
        f"// route-provenance: {provenance_json()}\n"
        f"module.exports = {{\"bad\":{constant}}};",
        encoding="utf-8",
    )
    write_commonjs(floor_path, {})
    review_path.write_text('{"schemaVersion":1,"reviews":[]}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--same-floor-paths",
            str(same_path),
            "--floor-nav-paths",
            str(floor_path),
            "--review-decisions",
            str(review_path),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "non-standard JSON constant" in result.stderr
