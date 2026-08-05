from __future__ import annotations

from collections import Counter
import heapq
from math import atan2, degrees, hypot

import numpy as np
import pytest

import safe_path_solver
from routing_surface import RoutingSurface, line_is_safe
from safe_path_solver import (
    CandidateDag,
    CandidateEdge,
    PathResult,
    SolveContext,
    SolverDiagnostics,
    _quality,
    build_local_candidate_dag,
    build_semantic_point_indexes,
    endpoint_bridges,
    grid_a_star,
    optimize_visible_path,
    select_bounded_turn_path,
    solve_safe_path,
)


def surface(mask, cell_size=1):
    return RoutingSurface(
        safe_mask=mask,
        raw_obstacle_mask=~mask,
        clearance_field=mask.astype(np.float32) * 10,
        buffer_margin_field=mask.astype(np.float32) * 10,
        hard_forbidden_mask=~mask,
        cell_size_px=cell_size,
    )


def constant_surface(width, height):
    safe = np.broadcast_to(np.array(True), (height, width))
    blocked = np.broadcast_to(np.array(False), (height, width))
    clearance = np.broadcast_to(np.array(10, dtype=np.float32), (height, width))
    return RoutingSurface(
        safe_mask=safe,
        raw_obstacle_mask=blocked,
        clearance_field=clearance,
        buffer_margin_field=clearance,
        hard_forbidden_mask=blocked,
        cell_size_px=1,
    )


def bend_angle(points, *, aspect=1.0):
    first, middle, last = points
    ax = middle[0] - first[0]
    ay = (middle[1] - first[1]) * aspect
    bx = last[0] - middle[0]
    by = (last[1] - middle[1]) * aspect
    return degrees(atan2(abs(ax * by - ay * bx), ax * bx + ay * by))


def path_length(path):
    return sum(hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:]))


def independent_bridges(safe, point, radius):
    step = safe.cell_size_px
    gx0, gy0 = round(point[0] / step), round(point[1] / step)
    result = []
    height, width = safe.safe_mask.shape
    for gy in range(gy0 - radius, gy0 + radius + 1):
        for gx in range(gx0 - radius, gx0 + radius + 1):
            candidate = (gx * step, gy * step)
            x, y = candidate
            if not (0 <= x < width and 0 <= y < height):
                continue
            if max(abs(x - point[0]), abs(y - point[1])) > radius * step:
                continue
            if safe.safe_mask[y, x] and line_is_safe(safe, point, candidate):
                result.append(candidate)
    return tuple(
        sorted(
            result,
            key=lambda p: (
                hypot(p[0] - point[0], p[1] - point[1]),
                p[1],
                p[0],
            ),
        )
    )


def independent_grid_distance(safe, start, goal, radius):
    start_bridges = independent_bridges(safe, start, radius)
    goal_bridges = set(independent_bridges(safe, goal, radius))
    if not start_bridges or not goal_bridges:
        return None
    start_node = ("start", *start)
    goal_node = ("goal", *goal)
    distances = {start_node: 0.0}
    queue = [(0.0, start_node)]
    step = safe.cell_size_px
    height, width = safe.safe_mask.shape
    neighbors = (
        (1, 0),
        (0, 1),
        (-1, 0),
        (0, -1),
        (1, 1),
        (-1, 1),
        (-1, -1),
        (1, -1),
    )
    while queue:
        distance, current_node = heapq.heappop(queue)
        if distance != distances[current_node]:
            continue
        kind, current_x, current_y = current_node
        current = (current_x, current_y)
        if kind == "goal":
            return distance
        successors = []
        if kind == "start":
            successors.extend(("grid", *point) for point in start_bridges)
        else:
            for dx, dy in neighbors:
                candidate = (current[0] + dx * step, current[1] + dy * step)
                x, y = candidate
                if not (
                    0 <= x < width
                    and 0 <= y < height
                    and safe.safe_mask[y, x]
                ):
                    continue
                if dx and dy and (
                    not safe.safe_mask[current[1], x]
                    or not safe.safe_mask[y, current[0]]
                ):
                    continue
                if line_is_safe(safe, current, candidate):
                    successors.append(("grid", *candidate))
            if current in goal_bridges:
                successors.append(goal_node)
        for candidate_node in successors:
            candidate = (candidate_node[1], candidate_node[2])
            next_distance = distance + hypot(
                candidate[0] - current[0],
                candidate[1] - current[1],
            )
            if next_distance < distances.get(candidate_node, float("inf")):
                distances[candidate_node] = next_distance
                heapq.heappush(queue, (next_distance, candidate_node))
    return None


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


def test_grid_a_star_matches_independent_dijkstra_distance():
    mask = np.ones((17, 17), dtype=bool)
    mask[2:15, 7:10] = False
    mask[12:15, 7:10] = True
    safe = surface(mask, cell_size=2)
    start, goal = (1, 7), (15, 7)
    expected = independent_grid_distance(safe, start, goal, 2)
    path = grid_a_star(
        SolveContext(safe, SolverDiagnostics()),
        start,
        goal,
        endpoint_bridge_radius_cells=2,
    )
    assert expected is not None and path is not None
    assert path_length(path) == pytest.approx(expected, abs=1e-9)


def test_endpoint_bridge_includes_an_aligned_grid_point_at_zero_cost():
    safe = surface(np.ones((9, 9), dtype=bool), cell_size=2)
    context = SolveContext(safe, SolverDiagnostics())
    bridges = endpoint_bridges(context, (4, 4), radius_cells=1)
    assert bridges[0] == (4, 4)
    assert path_length(((4, 4), bridges[0])) == 0


def test_endpoint_without_a_safe_bridge_returns_none():
    mask = np.zeros((7, 7), dtype=bool)
    mask[1, 1] = mask[5, 5] = True
    safe = surface(mask, cell_size=2)
    diagnostics = SolverDiagnostics(start_bridge_count=99, goal_bridge_count=99)
    assert (
        grid_a_star(
            SolveContext(safe, diagnostics),
            (1, 1),
            (5, 5),
            endpoint_bridge_radius_cells=1,
        )
        is None
    )
    assert diagnostics.start_bridge_count == 0
    assert diagnostics.goal_bridge_count == 0


def test_bridge_counts_are_overwritten_when_diagnostics_are_reused():
    mask = np.zeros((7, 7), dtype=bool)
    mask[2, 2] = mask[5, 5] = True
    safe = surface(mask, cell_size=2)
    diagnostics = SolverDiagnostics(start_bridge_count=99, goal_bridge_count=99)
    assert (
        grid_a_star(
            SolveContext(safe, diagnostics),
            (2, 2),
            (5, 5),
            endpoint_bridge_radius_cells=1,
        )
        is None
    )
    assert diagnostics.start_bridge_count == 1
    assert diagnostics.goal_bridge_count == 0


def test_diagonal_corner_cut_is_rejected():
    mask = np.ones((3, 3), dtype=bool)
    mask[0, 1] = mask[1, 0] = False
    safe = surface(mask)
    assert (
        grid_a_star(
            SolveContext(safe, SolverDiagnostics()),
            (0, 0),
            (2, 2),
            endpoint_bridge_radius_cells=1,
        )
        is None
    )


def test_grid_a_star_is_deterministic():
    mask = np.ones((15, 15), dtype=bool)
    mask[3:12, 7] = False
    mask[10, 7] = True
    safe = surface(mask)
    paths = [
        grid_a_star(
            SolveContext(safe, SolverDiagnostics()),
            (1, 6),
            (13, 6),
            endpoint_bridge_radius_cells=2,
        )
        for _ in range(2)
    ]
    assert paths[0] is not None and paths[0] == paths[1]


def test_epsilon_close_but_longer_stable_parent_does_not_replace_minimum_g():
    known_parent = safe_path_solver._TaggedNode("grid", (5, 5))
    stable_parent = safe_path_solver._TaggedNode("grid", (4, 5))
    candidate_g = 10.0 + 5e-10

    accepted, stored_g = safe_path_solver._relaxation_result(
        candidate_g=candidate_g,
        known_g=10.0,
        current=stable_parent,
        existing_parent=known_parent,
        candidate_expanded=False,
    )

    assert not accepted
    assert stored_g == 10.0


def test_epsilon_close_but_shorter_candidate_updates_the_minimum_g():
    known_parent = safe_path_solver._TaggedNode("grid", (4, 5))
    candidate_parent = safe_path_solver._TaggedNode("grid", (5, 5))
    candidate_g = 10.0 - 5e-10

    accepted, stored_g = safe_path_solver._relaxation_result(
        candidate_g=candidate_g,
        known_g=10.0,
        current=candidate_parent,
        existing_parent=known_parent,
        candidate_expanded=False,
    )

    assert accepted
    assert stored_g == candidate_g


def test_exact_equal_cost_uses_the_stable_parent_key():
    known_parent = safe_path_solver._TaggedNode("grid", (5, 5))
    stable_parent = safe_path_solver._TaggedNode("grid", (4, 5))

    accepted, stored_g = safe_path_solver._relaxation_result(
        candidate_g=10.0,
        known_g=10.0,
        current=stable_parent,
        existing_parent=known_parent,
        candidate_expanded=False,
    )

    assert accepted
    assert stored_g == 10.0


def test_aligned_goal_finishes_equal_cost_frontier_before_returning(monkeypatch):
    safe = surface(np.ones((13, 13), dtype=bool))
    captured_goal_parents = []
    original = safe_path_solver._reconstruct_path

    def capture(came_from, start_node, goal_node):
        captured_goal_parents.append(came_from[goal_node].point)
        return original(came_from, start_node, goal_node)

    monkeypatch.setattr(safe_path_solver, "_reconstruct_path", capture)
    path = grid_a_star(
        SolveContext(safe, SolverDiagnostics()),
        (0, 2),
        (3, 0),
        endpoint_bridge_radius_cells=2,
    )

    assert path is not None
    assert captured_goal_parents == [(3, 0)]


def test_expanded_node_rejects_late_relaxation():
    parent = safe_path_solver._TaggedNode("grid", (4, 5))

    accepted, stored_g = safe_path_solver._relaxation_result(
        candidate_g=9.0,
        known_g=10.0,
        current=parent,
        existing_parent=parent,
        candidate_expanded=True,
    )

    assert not accepted
    assert stored_g == 10.0


def test_replaced_close_label_is_stale_even_when_scores_are_close():
    assert not safe_path_solver._heap_entry_is_current(
        queued_g=10.0,
        queued_revision=1,
        current_g=10.0 - 5e-10,
        current_revision=2,
    )
    assert safe_path_solver._heap_entry_is_current(
        queued_g=10.0 - 5e-10,
        queued_revision=2,
        current_g=10.0 - 5e-10,
        current_revision=2,
    )


def test_grid_a_star_never_uses_an_unsafe_direct_fallback():
    mask = np.ones((9, 9), dtype=bool)
    mask[:, 4] = False
    safe = surface(mask)
    assert (
        grid_a_star(
            SolveContext(safe, SolverDiagnostics()),
            (1, 4),
            (7, 4),
            endpoint_bridge_radius_cells=2,
        )
        is None
    )


def test_grid_search_scans_each_undirected_segment_once(monkeypatch):
    mask = np.ones((20, 20), dtype=bool)
    mask[4:17, 9:11] = False
    mask[14:17, 9:11] = True
    safe = surface(mask)
    scans = Counter()
    original = safe_path_solver.line_is_safe

    def tracked(surface, left, right):
        key = (left, right) if left <= right else (right, left)
        scans[key] += 1
        return original(surface, left, right)

    monkeypatch.setattr(safe_path_solver, "line_is_safe", tracked)
    assert (
        grid_a_star(
            SolveContext(safe, SolverDiagnostics()),
            (1, 8),
            (18, 8),
            endpoint_bridge_radius_cells=2,
        )
        is not None
    )
    assert scans and max(scans.values()) == 1


def turn_fixture(long_edge_distance):
    points = ((0, 0), (2, 0), (2, 2), (3, 3), (6, 0))
    edges = (
        (
            CandidateEdge(1, 2.0, 10.0),
            CandidateEdge(3, long_edge_distance, 10.0),
        ),
        (CandidateEdge(2, 2.0, 10.0),),
        (CandidateEdge(4, 3.0, 10.0),),
        (CandidateEdge(4, long_edge_distance, 10.0),),
        (),
    )
    return CandidateDag(
        points=points,
        seed_indexes=(0, 1, 2, 3, 4),
        edges=edges,
        shortest_distance=7.0,
        reverse_distances=(7.0, 5.0, 3.0, long_edge_distance, 0.0),
        shortest_path=(0, 1, 2, 4),
    )


def test_visibility_checks_are_not_assumed_monotone():
    rows = [
        ".#.#..#",
        "..#.#..",
        "##..#..",
        ".....#.",
        "..#....",
        "#...#..",
        "...#...",
    ]
    safe = surface(np.array([[c == "." for c in row] for row in rows], dtype=bool))
    seed = [
        (0, 3),
        (1, 3),
        (2, 3),
        (3, 3),
        (3, 4),
        (4, 4),
        (5, 4),
        (6, 4),
        (6, 3),
    ]
    context = SolveContext(safe, SolverDiagnostics())
    assert not context.segment_is_safe((1, 3), (4, 4))
    assert context.segment_is_safe((1, 3), (6, 4))
    dag = build_local_candidate_dag(
        context,
        seed,
        seed,
        candidate_limit=96,
        seed_index_radius=1,
    )
    assert dag is not None
    source = dag.points.index((1, 3))
    target = dag.points.index((6, 4))
    assert target in {edge.target for edge in dag.edges[source]}


def test_candidate_dag_never_exceeds_96_points_or_4560_los_checks():
    safe = surface(np.ones((3, 160), dtype=bool))
    seed = [(x, 1) for x in range(150)]
    greedy = seed[::2]
    context = SolveContext(safe, SolverDiagnostics())
    dag = build_local_candidate_dag(
        context,
        seed,
        greedy,
        candidate_limit=96,
        seed_index_radius=12,
    )
    assert dag is not None and len(dag.points) == 96
    assert context.diagnostics.candidate_los_checks <= 4560


def test_candidate_selection_is_ranked_then_reordered_by_seed_index():
    safe = surface(np.ones((3, 11), dtype=bool))
    seed = [(x, 1) for x in range(11)]
    greedy = [seed[0], seed[5], seed[10]]
    dag = build_local_candidate_dag(
        SolveContext(safe, SolverDiagnostics()),
        seed,
        greedy,
        candidate_limit=5,
        seed_index_radius=2,
    )
    assert dag is not None
    assert dag.seed_indexes == (0, 1, 4, 5, 10)
    assert dag.points == tuple(seed[index] for index in dag.seed_indexes)


def test_duplicate_seed_coordinate_keeps_the_actual_simplified_index(monkeypatch):
    safe = surface(np.ones((4, 5), dtype=bool))
    seed = [(0, 0), (1, 0), (2, 0), (1, 0), (3, 0)]
    context = SolveContext(safe, SolverDiagnostics())

    def visibility(left, right):
        return (left, right) != ((0, 0), (3, 0))

    monkeypatch.setattr(context, "segment_is_safe", visibility)
    greedy = safe_path_solver.simplify_visible_path(context, seed)
    dag = build_local_candidate_dag(
        context,
        seed,
        greedy,
        candidate_limit=3,
        seed_index_radius=1,
    )

    assert greedy == [(0, 0), (1, 0), (3, 0)]
    assert dag is not None
    assert dag.seed_indexes == (0, 3, 4)


def test_optional_candidates_never_escape_the_greedy_radius_windows():
    safe = surface(np.ones((3, 11), dtype=bool))
    seed = [(x, 1) for x in range(11)]
    dag = build_local_candidate_dag(
        SolveContext(safe, SolverDiagnostics()),
        seed,
        [seed[0], seed[10]],
        candidate_limit=10,
        seed_index_radius=1,
    )
    assert dag is not None
    assert dag.seed_indexes == (0, 1, 9, 10)
    assert all(index in {0, 1, 9, 10} for index in dag.seed_indexes)


def test_equal_distance_dag_paths_choose_fewest_segments_before_indexes():
    safe = surface(np.ones((3, 7), dtype=bool))
    seed = [(0, 1), (2, 1), (4, 1), (6, 1)]
    dag = build_local_candidate_dag(
        SolveContext(safe, SolverDiagnostics()),
        seed,
        seed,
        candidate_limit=96,
        seed_index_radius=1,
    )
    assert dag is not None
    assert dag.shortest_distance == pytest.approx(6.0, abs=1e-9)
    assert dag.shortest_path == (0, 3)


def test_epsilon_close_longer_dag_path_cannot_replace_strict_shortest_path():
    edges = (
        (
            CandidateEdge(1, 5.0, 10.0),
            CandidateEdge(2, 10.0, 10.0),
        ),
        (CandidateEdge(2, 5.0 + 5e-10, 10.0),),
        (),
    )

    _, shortest_path = safe_path_solver._forward_shortest_path(edges)

    assert shortest_path == (0, 2)


def test_epsilon_close_longer_turn_label_cannot_replace_shorter_label():
    shorter = safe_path_solver.TurnLabel(10.0, 1.0, (0, 2))
    longer = safe_path_solver.TurnLabel(10.0 + 5e-10, 2.0, (0, 1, 2))

    assert not safe_path_solver._label_is_better(longer, shorter)


def test_zero_length_turn_edge_fails_closed():
    with pytest.raises(AssertionError, match="zero-length"):
        safe_path_solver._adds_effective_turn(
            (0, 0),
            (0, 0),
            (1, 0),
            25,
        )


def test_missing_required_greedy_edge_fails_closed(monkeypatch):
    safe = surface(np.ones((3, 5), dtype=bool))
    context = SolveContext(safe, SolverDiagnostics())
    original = context.segment_is_safe

    def reject_required_edge(left, right):
        if (left, right) in {((0, 1), (2, 1)), ((2, 1), (0, 1))}:
            return False
        return original(left, right)

    monkeypatch.setattr(context, "segment_is_safe", reject_required_edge)
    with pytest.raises(AssertionError, match="greedy"):
        build_local_candidate_dag(
            context,
            [(0, 1), (1, 1), (2, 1), (3, 1), (4, 1)],
            [(0, 1), (2, 1), (4, 1)],
            candidate_limit=96,
            seed_index_radius=1,
        )


def test_route_with_fewer_turns_wins_inside_d_plus_6():
    diagnostics = SolverDiagnostics()
    path, status = select_bounded_turn_path(
        turn_fixture(6.0),
        distance_tolerance_px=6,
        turn_angle_degrees=25,
        max_turns=8,
        diagnostics=diagnostics,
    )
    assert path == [(0, 0), (3, 3), (6, 0)]
    assert status == "optimized"


def test_route_over_d_plus_6_cannot_win_on_turns():
    path, status = select_bounded_turn_path(
        turn_fixture(7.0),
        distance_tolerance_px=6,
        turn_angle_degrees=25,
        max_turns=8,
        diagnostics=SolverDiagnostics(),
    )
    assert path == [(0, 0), (2, 0), (2, 2), (6, 0)]
    assert status == "optimized"


def test_more_than_96_required_greedy_points_returns_candidate_limit_fallback(
    monkeypatch,
):
    safe = surface(np.ones((3, 110), dtype=bool))
    seed = [(x, 1) for x in range(100)]
    diagnostics = SolverDiagnostics(local_states_peak=99, local_transitions=99)
    monkeypatch.setattr(
        safe_path_solver,
        "simplify_visible_path",
        lambda _context, path: list(path),
    )
    path, status = optimize_visible_path(
        SolveContext(safe, diagnostics),
        seed,
        candidate_limit=96,
        seed_index_radius=12,
        max_turns=8,
        distance_tolerance_px=6,
        turn_angle_degrees=25,
    )
    assert path == seed
    assert status == "fallbackCandidateLimit"
    assert diagnostics.local_states_peak == 0
    assert diagnostics.local_transitions == 0


def zigzag_dag():
    points = tuple((index, index % 2) for index in range(11))
    edges = tuple(
        (CandidateEdge(index + 1, 1.0, 10.0),) if index < 10 else ()
        for index in range(11)
    )
    return CandidateDag(
        points=points,
        seed_indexes=tuple(range(11)),
        edges=edges,
        shortest_distance=10.0,
        reverse_distances=tuple(float(10 - index) for index in range(11)),
        shortest_path=tuple(range(11)),
    )


def test_more_than_8_turns_returns_turn_limit_fallback():
    path, status = select_bounded_turn_path(
        zigzag_dag(),
        distance_tolerance_px=6,
        turn_angle_degrees=25,
        max_turns=8,
        diagnostics=SolverDiagnostics(),
    )
    assert path == list(zigzag_dag().points)
    assert status == "fallbackTurnLimit"


def test_local_state_count_never_exceeds_formula():
    dag = turn_fixture(6.0)
    diagnostics = SolverDiagnostics()
    select_bounded_turn_path(
        dag,
        distance_tolerance_px=6,
        turn_angle_degrees=25,
        max_turns=8,
        diagnostics=diagnostics,
    )
    assert diagnostics.local_states_peak <= 9 * len(dag.points) ** 2
    assert diagnostics.local_states_peak <= 82_944


SOLVER_OPTIONS = {
    "max_anchor_snap_px": 120,
    "endpoint_bridge_radius_cells": 4,
    "local_candidate_limit": 96,
    "local_seed_index_radius": 12,
    "local_max_turns": 8,
    "path_distance_tie_tolerance_px": 6,
    "turn_angle_degrees": 25,
}


def solve(surface, source, target, *, diagnostics=None):
    return solve_safe_path(
        surface,
        source,
        target,
        diagnostics=diagnostics,
        **SOLVER_OPTIONS,
    )


def test_direct_path_has_direct_quality_status():
    safe = surface(np.ones((20, 20), dtype=bool))
    result = solve(safe, (5.0, 5.0), (95.0, 95.0))
    assert result is not None
    assert len(result.pixel_points) == 2
    assert result.solver_quality_status == "direct"


@pytest.mark.parametrize(
    ("mask", "source", "target", "expected_reason"),
    [
        (np.zeros((9, 9), dtype=bool), (10.0, 10.0), (90.0, 90.0), "sourceAnchorSnapFailed"),
        (np.ones((9, 9), dtype=bool), (50.0, 50.0), (50.0, 50.0), "coLocated"),
    ],
)
def test_public_solver_returns_explicit_early_failure_reason(
    mask, source, target, expected_reason
):
    diagnostics = SolverDiagnostics()
    assert solve(surface(mask), source, target, diagnostics=diagnostics) is None
    assert diagnostics.failure_reason == expected_reason


def test_target_snap_failure_is_distinguished_from_source_failure():
    mask = np.zeros((400, 400), dtype=bool)
    mask[40, 40] = True
    diagnostics = SolverDiagnostics()
    assert solve(
        surface(mask), (10.0, 10.0), (90.0, 90.0), diagnostics=diagnostics
    ) is None
    assert diagnostics.failure_reason == "targetAnchorSnapFailed"


@pytest.mark.parametrize(
    ("source_pixel", "expected_reason"),
    [
        ((1, 1), "startEndpointBridgeUnavailable"),
        ((2, 2), "goalEndpointBridgeUnavailable"),
    ],
)
def test_endpoint_bridge_failure_reason_identifies_the_side(
    source_pixel, expected_reason
):
    mask = np.zeros((7, 7), dtype=bool)
    mask[source_pixel[1], source_pixel[0]] = True
    mask[5, 5] = True
    safe = surface(mask, cell_size=2)
    source = (source_pixel[0] / 6 * 100, source_pixel[1] / 6 * 100)
    target = (5 / 6 * 100, 5 / 6 * 100)
    diagnostics = SolverDiagnostics()
    assert solve(safe, source, target, diagnostics=diagnostics) is None
    assert diagnostics.failure_reason == expected_reason


def test_closed_grid_reports_no_path_instead_of_direct_fallback():
    mask = np.ones((20, 20), dtype=bool)
    mask[:, 9:11] = False
    diagnostics = SolverDiagnostics()
    assert solve(
        surface(mask), (10.0, 50.0), (90.0, 50.0), diagnostics=diagnostics
    ) is None
    assert diagnostics.failure_reason == "gridPathUnavailable"


def test_final_audit_calls_uncached_full_resolution_check(monkeypatch):
    safe = surface(np.ones((20, 20), dtype=bool))
    calls = []
    original = safe_path_solver.line_is_safe

    def fail_only_the_second_check(surface_value, left, right):
        calls.append((left, right))
        if len(calls) == 2:
            return False
        return original(surface_value, left, right)

    monkeypatch.setattr(
        safe_path_solver, "line_is_safe", fail_only_the_second_check
    )
    diagnostics = SolverDiagnostics()
    with pytest.raises(AssertionError, match="final"):
        solve(safe, (5.0, 5.0), (95.0, 95.0), diagnostics=diagnostics)
    assert len(calls) == 2
    assert diagnostics.failure_reason == "finalAuditFailed"


def test_obstacle_route_has_bounded_quality_status_and_safe_edges():
    mask = np.ones((30, 30), dtype=bool)
    mask[5:25, 14:16] = False
    mask[22:25, 14:16] = True
    safe = surface(mask)
    result = solve(safe, (10.0, 30.0), (90.0, 30.0))
    assert result is not None
    assert result.solver_quality_status in {
        "optimized", "fallbackCandidateLimit", "fallbackTurnLimit"
    }
    assert all(
        line_is_safe(safe, left, right)
        for left, right in zip(result.pixel_points, result.pixel_points[1:])
    )


def test_fixed_12_by_12_visibility_reference_remains_a_regression():
    mask = np.ones((12, 12), dtype=bool)
    mask[3:9, 5:7] = False
    safe = surface(mask)
    start, goal = (1, 6), (10, 6)
    reference_distance = reference_visibility_distance(safe, start, goal)
    options = {**SOLVER_OPTIONS, "path_distance_tie_tolerance_px": 0}
    result = solve_safe_path(
        safe,
        (start[0] / 11 * 100, start[1] / 11 * 100),
        (goal[0] / 11 * 100, goal[1] / 11 * 100),
        **options,
    )
    assert result is not None
    assert path_length(result.pixel_points) == pytest.approx(
        reference_distance, abs=0.01
    )


def test_fixed_8_by_8_route_stays_inside_the_absolute_d_plus_6_window():
    rows = [
        "########", "#.##...#", "#....#.#", "##.....#",
        "#.#....#", "#..#..##", "#......#", "########",
    ]
    mask = np.array([[c == "." for c in row] for row in rows], dtype=bool)
    safe = surface(mask)
    start, goal = (1, 4), (6, 4)
    reference = reference_visibility_distance(safe, start, goal)
    result = solve(
        safe,
        (start[0] / 7 * 100, start[1] / 7 * 100),
        (goal[0] / 7 * 100, goal[1] / 7 * 100),
    )
    assert result is not None
    assert reference == pytest.approx(7.472135955)
    assert path_length(result.pixel_points) <= reference + 6 + 1e-9


def test_public_metrics_hash_and_semantic_indexes_are_deterministic():
    safe = surface(np.ones((20, 10), dtype=bool))
    first = solve(safe, (0.0, 0.0), (100.0, 100.0))
    second = solve(safe, (0.0, 0.0), (100.0, 100.0))
    assert first is not None and first == second
    assert first.route_length == pytest.approx(223.606798)
    assert first.shortest_segment == first.route_length
    assert first.min_clearance_px == 10.0
    assert first.effective_turn_count == 0
    assert first.semantic_point_indexes == (0, 1)
    assert len(first.geometry_sha256) == 64
    reverse_hash = _quality(
        safe,
        list(reversed(first.pixel_points)),
        turn_angle_degrees=25,
    )[-1]
    assert reverse_hash == first.geometry_sha256


def test_semantic_indexes_keep_only_endpoints_and_effective_turns():
    pixels = [(0, 0), (3, 0), (3, 3), (6, 3)]
    assert build_semantic_point_indexes(
        pixels, turn_angle_degrees=25
    ) == [0, 1, 2, 3]
    assert build_semantic_point_indexes(
        [(0, 0), (3, 0), (6, 0)], turn_angle_degrees=25
    ) == [0, 2]


@pytest.mark.parametrize(
    ("pixels", "raw_angle", "runtime_angle"),
    [
        (
            [(168, 1), (79, 25), (16, 14)],
            24.995755891568770,
            25.008278517716550,
        ),
        (
            [(38, 14), (2, 13), (0, 12)],
            24.973910905883411,
            25.017776702033938,
        ),
        (
            [(3670, 4557), (3621, 4593), (3616, 4594)],
            24.994564648555031,
            25.049497745515733,
        ),
    ],
)
def test_quality_turn_metadata_uses_serialized_runtime_geometry(
    pixels, raw_angle, runtime_angle
):
    width, height = 5587, 7163
    assert bend_angle(pixels) == pytest.approx(raw_angle, abs=1e-12)
    quality = _quality(
        constant_surface(width, height),
        pixels,
        turn_angle_degrees=25,
    )
    percentages = quality[0]
    assert bend_angle(percentages, aspect=height / width) == pytest.approx(
        runtime_angle, abs=1e-10
    )
    assert raw_angle < 25 <= runtime_angle
    assert quality[2] == 1
    assert quality[4] == (0, 1, 2)
