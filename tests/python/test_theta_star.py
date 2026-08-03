from __future__ import annotations

import heapq
from math import hypot

import numpy as np
import pytest

from routing_surface import RoutingSurface, line_is_safe
from theta_star import (
    SearchLabel,
    _direction_change_degrees,
    _path_from_label,
    is_better_label,
    solve_safe_path,
    theta_star,
)


def surface(mask: np.ndarray) -> RoutingSurface:
    return RoutingSurface(
        safe_mask=mask,
        raw_obstacle_mask=~mask,
        clearance_field=mask.astype(np.float32) * 10,
        buffer_margin_field=mask.astype(np.float32) * 10,
        hard_forbidden_mask=~mask,
        cell_size_px=1,
    )


def test_open_space_is_one_direct_segment_and_deterministic():
    safe = surface(np.ones((20, 20), dtype=bool))
    first = solve_safe_path(safe, (5.0, 5.0), (95.0, 95.0), max_anchor_snap_px=1)
    second = solve_safe_path(safe, (5.0, 5.0), (95.0, 95.0), max_anchor_snap_px=1)
    assert first is not None
    assert first.points == second.points
    assert len(first.points) == 2


def test_obstacle_route_is_safe_and_has_no_visible_redundant_waypoint():
    mask = np.ones((30, 30), dtype=bool)
    mask[5:25, 14:16] = False
    mask[22:25, 14:16] = True
    safe = surface(mask)
    result = solve_safe_path(safe, (10.0, 30.0), (90.0, 30.0), max_anchor_snap_px=1)
    assert result is not None
    pixels = result.pixel_points
    assert all(line_is_safe(safe, left, right) for left, right in zip(pixels, pixels[1:]))
    assert all(
        not line_is_safe(safe, pixels[index], pixels[index + 2])
        for index in range(len(pixels) - 2)
    )


def test_closed_wall_returns_no_path_instead_of_a_direct_fallback():
    mask = np.ones((20, 20), dtype=bool)
    mask[:, 9:11] = False
    result = solve_safe_path(surface(mask), (10.0, 50.0), (90.0, 50.0), max_anchor_snap_px=1)
    assert result is None


def test_same_snapped_pixel_returns_none_for_co_located_pair():
    safe = surface(np.ones((5, 5), dtype=bool))
    result = solve_safe_path(
        safe,
        (50, 50),
        (50, 50),
        max_anchor_snap_px=0,
    )
    assert result is None


def test_multi_expansion_skips_parent_back_edges_and_zero_length_turns():
    mask = np.ones((20, 20), dtype=bool)
    mask[2:18, 9:11] = False
    mask[15:18, 9:11] = True
    path = theta_star(
        surface(mask),
        (2, 8),
        (17, 8),
        distance_tolerance_px=0,
        turn_angle_degrees=25,
    )
    assert path is not None
    assert len(path) >= 3
    assert _direction_change_degrees((0, 0), (1, 0), (1, 0)) == 0


def test_label_tolerance_prefers_turns_then_clearance_then_stable_key():
    current = SearchLabel(100.0, 3, 2.0, (3, 3, 4, 4))
    assert is_better_label(
        SearchLabel(105.0, 2, 1.0, (9, 9, 9, 9)), current, 6.0
    )
    assert not is_better_label(
        SearchLabel(107.0, 0, 9.0, (0, 0, 0, 0)), current, 6.0
    )
    assert is_better_label(
        SearchLabel(100.0, 3, 3.0, (9, 9, 9, 9)), current, 6.0
    )
    assert is_better_label(
        SearchLabel(100.0, 3, 2.0, (1, 1, 2, 2)), current, 6.0
    )


def test_goal_reconstruction_keeps_the_parent_snapshot_that_won():
    start = SearchLabel(0.0, 0, 10.0, (0, 0, 0, 0), point=(0, 0))
    winning_parent = SearchLabel(
        1.0, 0, 9.0, (0, 0, 0, 1), point=(1, 0), parent=start
    )
    goal = SearchLabel(
        2.0, 0, 8.0, (0, 1, 0, 2), point=(2, 0), parent=winning_parent
    )
    reopened_parent = SearchLabel(
        1.1,
        0,
        10.0,
        (1, 0, 0, 1),
        point=(1, 0),
        parent=SearchLabel(0.5, 0, 10.0, (0, 0, 1, 0), point=(0, 1), parent=start),
    )
    mutable_registry = {(1, 0): reopened_parent}
    assert mutable_registry[(1, 0)] is reopened_parent
    assert _path_from_label(goal) == [(0, 0), (1, 0), (2, 0)]


def reference_visibility_distance(safe, start, goal):
    height, width = safe.safe_mask.shape
    nodes = [
        (x, y)
        for y in range(height)
        for x in range(width)
        if safe.safe_mask[y, x]
    ]
    distances = {start: 0.0}
    queue = [(0.0, start)]
    while queue:
        distance, current = heapq.heappop(queue)
        if distance != distances[current]:
            continue
        if current == goal:
            return distance
        for candidate in nodes:
            if candidate == current or not line_is_safe(safe, current, candidate):
                continue
            next_distance = distance + hypot(
                candidate[0] - current[0], candidate[1] - current[1]
            )
            if next_distance < distances.get(candidate, float("inf")):
                distances[candidate] = next_distance
                heapq.heappush(queue, (next_distance, candidate))
    raise AssertionError("reference graph has no path")


def test_theta_star_matches_reference_visibility_graph_distance():
    mask = np.ones((12, 12), dtype=bool)
    mask[3:9, 5:7] = False
    safe = surface(mask)
    start, goal = (1, 6), (10, 6)
    reference_distance = reference_visibility_distance(safe, start, goal)
    path = theta_star(
        safe,
        start,
        goal,
        distance_tolerance_px=0,
        turn_angle_degrees=25,
    )
    assert path is not None
    actual = sum(hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:]))
    assert actual == pytest.approx(reference_distance, abs=0.01)


def test_distance_tolerance_stays_within_the_absolute_shortest_window():
    rows = [
        "########",
        "#.##...#",
        "#....#.#",
        "##.....#",
        "#.#....#",
        "#..#..##",
        "#......#",
        "########",
    ]
    mask = np.array([[c == "." for c in row] for row in rows], dtype=bool)
    safe = surface(mask)
    start, goal = (1, 4), (6, 4)
    reference = reference_visibility_distance(safe, start, goal)
    path = theta_star(
        safe,
        start,
        goal,
        distance_tolerance_px=6,
        turn_angle_degrees=25,
    )
    actual = sum(hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:]))
    assert reference == pytest.approx(7.472135955)
    assert actual <= reference + 6
