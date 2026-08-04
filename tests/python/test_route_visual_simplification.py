from __future__ import annotations

import numpy as np

from routing_surface import RoutingSurface, line_is_safe
from safe_path_solver import (
    SolveContext,
    SolverDiagnostics,
    optimize_visible_path,
)


def _open_surface(width: int, height: int) -> RoutingSurface:
    safe = np.ones((height, width), dtype=bool)
    blocked = np.zeros((height, width), dtype=bool)
    clearance = np.full((height, width), 10, dtype=np.float32)
    return RoutingSurface(
        safe_mask=safe,
        raw_obstacle_mask=blocked,
        clearance_field=clearance,
        buffer_margin_field=clearance,
        hard_forbidden_mask=blocked,
        cell_size_px=1,
    )


def test_optimizer_collapses_visible_collinear_seed_points() -> None:
    surface = _open_surface(9, 3)
    seed = [(x, 1) for x in range(9)]

    path, status = optimize_visible_path(
        SolveContext(surface, SolverDiagnostics()),
        seed,
        candidate_limit=96,
        seed_index_radius=12,
        distance_tolerance_px=6,
        turn_angle_degrees=25,
        max_turns=8,
    )

    assert status == "optimized"
    assert path == [seed[0], seed[-1]]
    assert line_is_safe(surface, path[0], path[1])
