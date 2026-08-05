#!/usr/bin/env python3
"""Apply the reviewed equal-cost route simplification change idempotently."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


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
    if current == "grid-a-star-visible-local-v2":
        return False
    if current != "grid-a-star-visible-local-v1":
        raise RuntimeError(f"unexpected routing algorithm version: {current!r}")
    document["algorithmVersion"] = "grid-a-star-visible-local-v2"
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return True


def patch_regression_expectation() -> bool:
    path = ROOT / "tests" / "python" / "test_safe_path_solver.py"
    return replace_once_or_verify(
        path,
        """def test_equal_distance_dag_paths_choose_lexicographically_smallest_indexes():\n    safe = surface(np.ones((3, 7), dtype=bool))\n    seed = [(0, 1), (2, 1), (4, 1), (6, 1)]\n    dag = build_local_candidate_dag(\n        SolveContext(safe, SolverDiagnostics()),\n        seed,\n        seed,\n        candidate_limit=96,\n        seed_index_radius=1,\n    )\n    assert dag is not None\n    assert dag.shortest_distance == pytest.approx(6.0, abs=1e-9)\n    assert dag.shortest_path == (0, 1, 2, 3)\n""",
        """def test_equal_distance_dag_paths_choose_fewest_segments_before_indexes():\n    safe = surface(np.ones((3, 7), dtype=bool))\n    seed = [(0, 1), (2, 1), (4, 1), (6, 1)]\n    dag = build_local_candidate_dag(\n        SolveContext(safe, SolverDiagnostics()),\n        seed,\n        seed,\n        candidate_limit=96,\n        seed_index_radius=1,\n    )\n    assert dag is not None\n    assert dag.shortest_distance == pytest.approx(6.0, abs=1e-9)\n    assert dag.shortest_path == (0, 3)\n""",
        label="equal-distance DAG regression expectation",
    )


def main() -> int:
    changed = {
        "solver": patch_solver(),
        "policy": patch_policy(),
        "regression": patch_regression_expectation(),
    }
    print(json.dumps(changed, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
