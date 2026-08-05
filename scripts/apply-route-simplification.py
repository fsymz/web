#!/usr/bin/env python3
"""Apply the reviewed equal-cost route simplification change idempotently."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OLD_VERSION = "grid-a-star-visible-local-v1"
NEW_VERSION = "grid-a-star-visible-local-v2"


def replace_once_or_verify(
    path: Path,
    old: str,
    new: str,
    *,
    label: str,
) -> bool:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
        return True
    if new in text:
        return False
    raise RuntimeError(f"{path}: cannot locate {label}")


def replace_all_or_verify(
    path: Path,
    old: str,
    new: str,
    *,
    label: str,
) -> bool:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")
        return True
    if new in text:
        return False
    raise RuntimeError(f"{path}: cannot locate {label}")


def patch_solver() -> bool:
    path = ROOT / "scripts" / "safe_path_solver.py"
    changed = False
    changed |= replace_once_or_verify(
        path,
        """            tied_better_path = (\n                candidate_distance == distances[edge.target]\n                and (known_path is None or candidate_path < known_path)\n            )\n""",
        """            tied_better_path = (\n                candidate_distance == distances[edge.target]\n                and (\n                    known_path is None\n                    or len(candidate_path) < len(known_path)\n                    or (\n                        len(candidate_path) == len(known_path)\n                        and candidate_path < known_path\n                    )\n                )\n            )\n""",
        label="equal-distance candidate-DAG tie breaker",
    )
    changed |= replace_once_or_verify(
        path,
        """def _label_is_better(candidate: TurnLabel, existing: TurnLabel) -> bool:\n    if candidate.distance != existing.distance:\n        return candidate.distance < existing.distance\n    if candidate.min_clearance_px != existing.min_clearance_px:\n        return candidate.min_clearance_px > existing.min_clearance_px\n    return candidate.path < existing.path\n""",
        """def _label_is_better(candidate: TurnLabel, existing: TurnLabel) -> bool:\n    if candidate.distance != existing.distance:\n        return candidate.distance < existing.distance\n    if candidate.min_clearance_px != existing.min_clearance_px:\n        return candidate.min_clearance_px > existing.min_clearance_px\n    if len(candidate.path) != len(existing.path):\n        return len(candidate.path) < len(existing.path)\n    return candidate.path < existing.path\n""",
        label="equal-quality label tie breaker",
    )
    return changed


def patch_policy() -> bool:
    path = ROOT / "config" / "routing-policy.json"
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    current = document.get("algorithmVersion")
    if current == NEW_VERSION:
        return False
    if current != OLD_VERSION:
        raise RuntimeError(f"unexpected routing algorithm version: {current!r}")
    document["algorithmVersion"] = NEW_VERSION
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return True


def patch_versioned_loader_and_tests() -> bool:
    changed = False
    changed |= replace_all_or_verify(
        ROOT / "scripts" / "routing_surface.py",
        OLD_VERSION,
        NEW_VERSION,
        label="routing-surface algorithm version",
    )
    changed |= replace_all_or_verify(
        ROOT / "tests" / "python" / "test_routing_surface.py",
        OLD_VERSION,
        NEW_VERSION,
        label="routing-surface test version fixtures",
    )
    return changed


def patch_regression_expectation() -> bool:
    path = ROOT / "tests" / "python" / "test_safe_path_solver.py"
    return replace_once_or_verify(
        path,
        """def test_equal_distance_dag_paths_choose_lexicographically_smallest_indexes():\n    safe = surface(np.ones((3, 7), dtype=bool))\n    seed = [(0, 1), (2, 1), (4, 1), (6, 1)]\n    dag = build_local_candidate_dag(\n        SolveContext(safe, SolverDiagnostics()),\n        seed,\n        seed,\n        candidate_limit=96,\n        seed_index_radius=1,\n    )\n    assert dag is not None\n    assert dag.shortest_distance == pytest.approx(6.0, abs=1e-9)\n    assert dag.shortest_path == (0, 1, 2, 3)\n""",
        """def test_equal_distance_dag_paths_choose_fewest_segments_before_indexes():\n    safe = surface(np.ones((3, 7), dtype=bool))\n    seed = [(0, 1), (2, 1), (4, 1), (6, 1)]\n    dag = build_local_candidate_dag(\n        SolveContext(safe, SolverDiagnostics()),\n        seed,\n        seed,\n        candidate_limit=96,\n        seed_index_radius=1,\n    )\n    assert dag is not None\n    assert dag.shortest_distance == pytest.approx(6.0, abs=1e-9)\n    assert dag.shortest_path == (0, 3)\n""",
        label="equal-distance DAG regression expectation",
    )


def patch_existing_route_postprocessor() -> bool:
    path = ROOT / "scripts" / "simplify-existing-route-paths.py"
    return replace_once_or_verify(
        path,
        """        canonical_updated = update_record(\n            representative,\n            surface=surface,\n            pixels=simplified_pixels,\n            turn_angle_degrees=turn_angle_degrees,\n        )\n        canonical_updated_points = canonical_updated[\"points\"]\n        changed_geometry = canonical_updated_points != canonical\n        if changed_geometry:\n            summary[\"changedUniqueGeometryCount\"] += 1\n\n        for key, record, reversed_from_record in members:\n            points_before = normalized_points(record.get(\"points\"), label=key)\n            updated = dict(canonical_updated)\n            if reversed_from_record:\n                updated[\"points\"] = [list(point) for point in reversed(canonical_updated_points)]\n                indexes = canonical_updated[\"semanticPointIndexes\"]\n                updated[\"semanticPointIndexes\"] = [\n                    len(canonical_updated_points) - 1 - index\n                    for index in reversed(indexes)\n                ]\n            else:\n                updated[\"points\"] = [list(point) for point in canonical_updated_points]\n                updated[\"semanticPointIndexes\"] = list(canonical_updated[\"semanticPointIndexes\"])\n            output[key] = updated\n            summary[\"pointsBefore\"] += len(points_before)\n            summary[\"pointsAfter\"] += len(updated[\"points\"])\n            summary[\"turnsAfter\"] += int(updated.get(\"effectiveTurnCount\") or 0)\n            if updated[\"points\"] != points_before:\n                summary[\"changedRouteCount\"] += 1\n""",
        """        canonical_updated_points = [\n            list(point)\n            for point in _quality(\n                surface,\n                simplified_pixels,\n                turn_angle_degrees=turn_angle_degrees,\n            )[0]\n        ]\n        changed_geometry = canonical_updated_points != canonical\n        if changed_geometry:\n            summary[\"changedUniqueGeometryCount\"] += 1\n\n        for key, record, reversed_from_record in members:\n            points_before = normalized_points(record.get(\"points\"), label=key)\n            oriented_pixels = (\n                list(reversed(simplified_pixels))\n                if reversed_from_record\n                else list(simplified_pixels)\n            )\n            updated = update_record(\n                record,\n                surface=surface,\n                pixels=oriented_pixels,\n                turn_angle_degrees=turn_angle_degrees,\n            )\n            output[key] = updated\n            summary[\"pointsBefore\"] += len(points_before)\n            summary[\"pointsAfter\"] += len(updated[\"points\"])\n            summary[\"turnsAfter\"] += int(updated.get(\"effectiveTurnCount\") or 0)\n            if updated[\"points\"] != points_before:\n                summary[\"changedRouteCount\"] += 1\n""",
        label="per-route metadata-preserving postprocessor block",
    )


def main() -> int:
    changed = {
        "solver": patch_solver(),
        "policy": patch_policy(),
        "versionedLoaderAndTests": patch_versioned_loader_and_tests(),
        "regression": patch_regression_expectation(),
        "postprocessorMetadata": patch_existing_route_postprocessor(),
    }
    print(json.dumps(changed, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
