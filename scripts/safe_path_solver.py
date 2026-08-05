"""Deterministic full-resolution-safe hospital path solving primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import heapq
import json
from itertools import count
from math import atan2, degrees, hypot
from typing import Literal, Sequence

from routing_surface import (
    RoutingSurface,
    iter_supercover_pixels,
    line_is_safe,
    snap_anchor,
    supercover_pixels,
)


GridPoint = tuple[int, int]
SolverQualityStatus = Literal[
    "direct",
    "optimized",
    "fallbackCandidateLimit",
    "fallbackTurnLimit",
]
NEIGHBORS = (
    (1, 0),
    (0, 1),
    (-1, 0),
    (0, -1),
    (1, 1),
    (-1, 1),
    (-1, -1),
    (1, -1),
)


@dataclass(frozen=True)
class PathResult:
    points: tuple[tuple[float, float], ...]
    pixel_points: tuple[GridPoint, ...]
    route_length: float
    min_clearance_px: float
    effective_turn_count: int
    shortest_segment: float
    semantic_point_indexes: tuple[int, ...]
    geometry_sha256: str
    source_snap_distance_px: float
    target_snap_distance_px: float
    solver_quality_status: SolverQualityStatus


@dataclass
class SolverDiagnostics:
    failure_reason: str | None = None
    line_safety_requests: int = 0
    line_safety_cache_hits: int = 0
    line_safety_cache_entries_peak: int = 0
    clearance_requests: int = 0
    clearance_cache_hits: int = 0
    clearance_cache_entries_peak: int = 0
    start_bridge_count: int = 0
    goal_bridge_count: int = 0
    grid_expanded_nodes: int = 0
    grid_heap_entries_peak: int = 0
    candidate_points: int = 0
    candidate_los_checks: int = 0
    local_states_peak: int = 0
    local_transitions: int = 0


@dataclass(frozen=True)
class CandidateEdge:
    target: int
    distance: float
    min_clearance_px: float


@dataclass(frozen=True)
class CandidateDag:
    points: tuple[GridPoint, ...]
    seed_indexes: tuple[int, ...]
    edges: tuple[tuple[CandidateEdge, ...], ...]
    shortest_distance: float
    reverse_distances: tuple[float, ...]
    shortest_path: tuple[int, ...]


@dataclass(frozen=True)
class TurnLabel:
    distance: float
    min_clearance_px: float
    path: tuple[int, ...]


@dataclass
class SolveContext:
    surface: RoutingSurface
    diagnostics: SolverDiagnostics
    line_cache: dict[tuple[int, GridPoint, GridPoint], bool] = field(
        default_factory=dict
    )
    clearance_cache: dict[tuple[int, GridPoint, GridPoint], float] = field(
        default_factory=dict
    )

    def key(
        self,
        left: GridPoint,
        right: GridPoint,
    ) -> tuple[int, GridPoint, GridPoint]:
        first, second = (left, right) if left <= right else (right, left)
        return (id(self.surface), first, second)

    def segment_is_safe(self, left: GridPoint, right: GridPoint) -> bool:
        self.diagnostics.line_safety_requests += 1
        key = self.key(left, right)
        if key in self.line_cache:
            self.diagnostics.line_safety_cache_hits += 1
            return self.line_cache[key]
        value = line_is_safe(self.surface, left, right)
        self.line_cache[key] = value
        self.diagnostics.line_safety_cache_entries_peak = max(
            self.diagnostics.line_safety_cache_entries_peak,
            len(self.line_cache),
        )
        return value

    def segment_clearance(self, left: GridPoint, right: GridPoint) -> float:
        self.diagnostics.clearance_requests += 1
        key = self.key(left, right)
        if key in self.clearance_cache:
            self.diagnostics.clearance_cache_hits += 1
            return self.clearance_cache[key]
        value = min(
            float(self.surface.clearance_field[y, x])
            for x, y in iter_supercover_pixels(left, right)
        )
        self.clearance_cache[key] = value
        self.diagnostics.clearance_cache_entries_peak = max(
            self.diagnostics.clearance_cache_entries_peak,
            len(self.clearance_cache),
        )
        return value


def endpoint_bridges(
    context: SolveContext,
    point: GridPoint,
    *,
    radius_cells: int,
) -> tuple[GridPoint, ...]:
    surface = context.surface
    step = surface.cell_size_px
    center_x = round(point[0] / step)
    center_y = round(point[1] / step)
    radius_px = radius_cells * step
    height, width = surface.safe_mask.shape
    bridges: set[GridPoint] = set()

    for grid_y in range(center_y - radius_cells, center_y + radius_cells + 1):
        for grid_x in range(center_x - radius_cells, center_x + radius_cells + 1):
            candidate = (grid_x * step, grid_y * step)
            x, y = candidate
            if not (0 <= x < width and 0 <= y < height):
                continue
            if max(abs(x - point[0]), abs(y - point[1])) > radius_px:
                continue
            if not bool(surface.safe_mask[y, x]):
                continue
            if context.segment_is_safe(point, candidate):
                bridges.add(candidate)

    return tuple(
        sorted(
            bridges,
            key=lambda candidate: (
                hypot(candidate[0] - point[0], candidate[1] - point[1]),
                candidate[1],
                candidate[0],
            ),
        )
    )


@dataclass(frozen=True)
class _TaggedNode:
    kind: Literal["start", "grid", "goal"]
    point: GridPoint

    @property
    def kind_order(self) -> int:
        return {"start": 0, "grid": 1, "goal": 2}[self.kind]


def stable_node_key(node: _TaggedNode) -> tuple[int, int, int]:
    return (node.point[1], node.point[0], node.kind_order)


def _relaxation_result(
    *,
    candidate_g: float,
    known_g: float,
    current: _TaggedNode,
    existing_parent: _TaggedNode | None,
    candidate_expanded: bool,
) -> tuple[bool, float]:
    if candidate_expanded:
        return False, known_g
    better = candidate_g < known_g
    tied_better_parent = (
        candidate_g == known_g
        and existing_parent is not None
        and stable_node_key(current) < stable_node_key(existing_parent)
    )
    if not (better or tied_better_parent):
        return False, known_g
    return True, candidate_g


def _heap_entry_is_current(
    *,
    queued_g: float,
    queued_revision: int,
    current_g: float,
    current_revision: int,
) -> bool:
    return queued_revision == current_revision and queued_g == current_g


def _heuristic(node: _TaggedNode, goal: GridPoint) -> float:
    if node.kind == "goal":
        return 0.0
    return hypot(goal[0] - node.point[0], goal[1] - node.point[1])


def _reconstruct_path(
    came_from: dict[_TaggedNode, _TaggedNode],
    start_node: _TaggedNode,
    goal_node: _TaggedNode,
) -> list[GridPoint]:
    nodes = [goal_node]
    current = goal_node
    while current != start_node:
        current = came_from[current]
        nodes.append(current)
    nodes.reverse()

    points: list[GridPoint] = []
    for node in nodes:
        if not points or points[-1] != node.point:
            points.append(node.point)
    return points


def grid_a_star(
    context: SolveContext,
    start: GridPoint,
    goal: GridPoint,
    *,
    endpoint_bridge_radius_cells: int,
) -> list[GridPoint] | None:
    diagnostics = context.diagnostics
    diagnostics.start_bridge_count = 0
    diagnostics.goal_bridge_count = 0
    start_bridges = endpoint_bridges(
        context,
        start,
        radius_cells=endpoint_bridge_radius_cells,
    )
    goal_bridges = endpoint_bridges(
        context,
        goal,
        radius_cells=endpoint_bridge_radius_cells,
    )
    diagnostics.start_bridge_count = len(start_bridges)
    diagnostics.goal_bridge_count = len(goal_bridges)
    if not start_bridges or not goal_bridges:
        return None

    goal_bridge_set = set(goal_bridges)
    start_node = _TaggedNode("start", start)
    goal_node = _TaggedNode("goal", goal)
    g_score: dict[_TaggedNode, float] = {start_node: 0.0}
    came_from: dict[_TaggedNode, _TaggedNode] = {}
    revisions: dict[_TaggedNode, int] = {start_node: 0}
    entry_revisions: dict[int, int] = {}
    sequence = count()
    queue: list[tuple[float, float, int, int, int, int, int, _TaggedNode]] = []

    def push(node: _TaggedNode, distance: float, parent: _TaggedNode) -> None:
        entry_sequence = next(sequence)
        entry_revisions[entry_sequence] = revisions[node]
        heapq.heappush(
            queue,
            (
                distance + _heuristic(node, goal),
                distance,
                node.point[1],
                node.point[0],
                parent.point[1],
                parent.point[0],
                entry_sequence,
                node,
            ),
        )
        diagnostics.grid_heap_entries_peak = max(
            diagnostics.grid_heap_entries_peak,
            len(queue),
        )

    push(start_node, 0.0, start_node)
    expanded: set[_TaggedNode] = set()
    surface = context.surface
    height, width = surface.safe_mask.shape
    step = surface.cell_size_px
    incumbent_goal_g: float | None = None

    while queue:
        if incumbent_goal_g is not None and queue[0][0] > incumbent_goal_g:
            return _reconstruct_path(came_from, start_node, goal_node)
        _, queued_g, _, _, _, _, entry_sequence, current = heapq.heappop(queue)
        queued_revision = entry_revisions.pop(entry_sequence)
        known_current_g = g_score.get(current)
        current_revision = revisions.get(current)
        if (
            known_current_g is None
            or current_revision is None
            or not _heap_entry_is_current(
                queued_g=queued_g,
                queued_revision=queued_revision,
                current_g=known_current_g,
                current_revision=current_revision,
            )
        ):
            continue
        if current in expanded:
            continue
        if current == goal_node:
            incumbent_goal_g = known_current_g
            continue
        expanded.add(current)

        successors: list[tuple[_TaggedNode, float]] = []
        if current.kind == "start":
            successors.extend(
                (
                    _TaggedNode("grid", bridge),
                    hypot(bridge[0] - start[0], bridge[1] - start[1]),
                )
                for bridge in start_bridges
            )
        else:
            diagnostics.grid_expanded_nodes += 1
            current_x, current_y = current.point
            for delta_x, delta_y in NEIGHBORS:
                candidate = (
                    current_x + delta_x * step,
                    current_y + delta_y * step,
                )
                candidate_x, candidate_y = candidate
                if not (
                    0 <= candidate_x < width
                    and 0 <= candidate_y < height
                    and bool(surface.safe_mask[candidate_y, candidate_x])
                ):
                    continue
                if delta_x and delta_y and (
                    not bool(surface.safe_mask[current_y, candidate_x])
                    or not bool(surface.safe_mask[candidate_y, current_x])
                ):
                    continue
                if not context.segment_is_safe(current.point, candidate):
                    continue
                successors.append(
                    (
                        _TaggedNode("grid", candidate),
                        hypot(
                            candidate_x - current_x,
                            candidate_y - current_y,
                        ),
                    )
                )
            if current.point in goal_bridge_set:
                successors.append(
                    (
                        goal_node,
                        hypot(goal[0] - current_x, goal[1] - current_y),
                    )
                )

        for candidate, edge_cost in successors:
            candidate_g = known_current_g + edge_cost
            known_g = g_score.get(candidate, float("inf"))
            existing_parent = came_from.get(candidate)
            accepted, stored_g = _relaxation_result(
                candidate_g=candidate_g,
                known_g=known_g,
                current=current,
                existing_parent=existing_parent,
                candidate_expanded=candidate in expanded,
            )
            if not accepted:
                continue
            g_score[candidate] = stored_g
            came_from[candidate] = current
            revisions[candidate] = revisions.get(candidate, 0) + 1
            push(candidate, stored_g, current)

    if incumbent_goal_g is not None:
        return _reconstruct_path(came_from, start_node, goal_node)
    return None


def _simplify_visible_indexes(
    context: SolveContext,
    seed: tuple[GridPoint, ...],
) -> list[int]:
    if len(seed) < 2:
        return list(range(len(seed)))

    simplified = [0]
    current_index = 0
    goal_index = len(seed) - 1
    while current_index < goal_index:
        next_index = None
        for candidate_index in range(goal_index, current_index, -1):
            if context.segment_is_safe(
                seed[current_index],
                seed[candidate_index],
            ):
                next_index = candidate_index
                break
        if next_index is None:
            raise AssertionError("seed path has no safe visible successor")
        simplified.append(next_index)
        current_index = next_index
    return simplified


def simplify_visible_path(
    context: SolveContext,
    seed_path: Sequence[GridPoint],
) -> list[GridPoint]:
    seed = tuple(seed_path)
    return [
        seed[index]
        for index in _simplify_visible_indexes(context, seed)
    ]


def _greedy_seed_indexes(
    seed: tuple[GridPoint, ...],
    greedy: tuple[GridPoint, ...],
) -> tuple[int, ...]:
    indexes = []
    search_start = 0
    for point in greedy:
        match = next(
            (
                index
                for index in range(search_start, len(seed))
                if seed[index] == point
            ),
            None,
        )
        if match is None:
            raise AssertionError("greedy point is not an ordered seed point")
        indexes.append(match)
        search_start = match + 1
    return tuple(indexes)


def _forward_shortest_path(
    edges: tuple[tuple[CandidateEdge, ...], ...],
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    count_points = len(edges)
    distances = [float("inf")] * count_points
    paths: list[tuple[int, ...] | None] = [None] * count_points
    distances[0] = 0.0
    paths[0] = (0,)
    for source, outgoing in enumerate(edges):
        source_path = paths[source]
        if source_path is None:
            continue
        for edge in outgoing:
            candidate_distance = distances[source] + edge.distance
            candidate_path = source_path + (edge.target,)
            known_path = paths[edge.target]
            better = candidate_distance < distances[edge.target]
            tied_better_path = (
                candidate_distance == distances[edge.target]
                and (
                    known_path is None
                    or len(candidate_path) < len(known_path)
                    or (
                        len(candidate_path) == len(known_path)
                        and candidate_path < known_path
                    )
                )
            )
            if better or tied_better_path:
                distances[edge.target] = candidate_distance
                paths[edge.target] = candidate_path
    if paths[-1] is None:
        raise AssertionError("candidate DAG has no path to the goal")
    return tuple(distances), paths[-1]


def _reverse_shortest_distances(
    edges: tuple[tuple[CandidateEdge, ...], ...],
) -> tuple[float, ...]:
    reverse = [float("inf")] * len(edges)
    reverse[-1] = 0.0
    for source in range(len(edges) - 2, -1, -1):
        reverse[source] = min(
            (
                edge.distance + reverse[edge.target]
                for edge in edges[source]
            ),
            default=float("inf"),
        )
    return tuple(reverse)


def build_local_candidate_dag(
    context: SolveContext,
    seed_path: Sequence[GridPoint],
    greedy_path: Sequence[GridPoint],
    *,
    candidate_limit: int,
    seed_index_radius: int,
) -> CandidateDag | None:
    if not 3 <= candidate_limit <= 96:
        raise ValueError("candidate_limit must be in 3..96")
    if not 1 <= seed_index_radius <= 64:
        raise ValueError("seed_index_radius must be in 1..64")
    seed = tuple(seed_path)
    greedy = tuple(greedy_path)
    if len(seed) < 2 or len(greedy) < 2:
        raise AssertionError("seed and greedy paths must contain both endpoints")
    actual_greedy_indexes = tuple(_simplify_visible_indexes(context, seed))
    actual_greedy_points = tuple(seed[index] for index in actual_greedy_indexes)
    mapped_greedy_indexes = (
        actual_greedy_indexes
        if actual_greedy_points == greedy
        else _greedy_seed_indexes(seed, greedy)
    )
    greedy_indexes = tuple(
        sorted({0, len(seed) - 1, *mapped_greedy_indexes})
    )

    diagnostics = context.diagnostics
    diagnostics.candidate_points = 0
    diagnostics.candidate_los_checks = 0
    required_indexes = set(greedy_indexes)
    if len(required_indexes) > candidate_limit:
        return None

    permitted_optional_indexes: set[int] = set()
    for greedy_index in greedy_indexes:
        permitted_optional_indexes.update(
            range(
                max(0, greedy_index - seed_index_radius),
                min(len(seed), greedy_index + seed_index_radius + 1),
            )
        )
    permitted_optional_indexes.difference_update(required_indexes)
    ranked_optional_indexes = sorted(
        permitted_optional_indexes,
        key=lambda index: (
            min(abs(index - greedy_index) for greedy_index in greedy_indexes),
            index,
            seed[index][1],
            seed[index][0],
        ),
    )
    remaining_capacity = candidate_limit - len(required_indexes)
    selected_indexes = tuple(
        sorted(
            required_indexes
            | set(ranked_optional_indexes[:remaining_capacity])
        )
    )
    points = tuple(seed[index] for index in selected_indexes)
    if len(set(points)) != len(points):
        raise AssertionError("candidate points contain duplicate coordinates")
    diagnostics.candidate_points = len(points)
    assert len(points) <= 96

    edge_lists: list[list[CandidateEdge]] = [[] for _ in points]
    for source in range(len(points)):
        for target in range(source + 1, len(points)):
            diagnostics.candidate_los_checks += 1
            if not context.segment_is_safe(points[source], points[target]):
                continue
            edge_lists[source].append(
                CandidateEdge(
                    target=target,
                    distance=hypot(
                        points[target][0] - points[source][0],
                        points[target][1] - points[source][1],
                    ),
                    min_clearance_px=context.segment_clearance(
                        points[source],
                        points[target],
                    ),
                )
            )
    assert diagnostics.candidate_los_checks <= 4560
    edges = tuple(tuple(outgoing) for outgoing in edge_lists)

    candidate_position = {
        seed_index: position
        for position, seed_index in enumerate(selected_indexes)
    }
    for left_seed_index, right_seed_index in zip(
        greedy_indexes,
        greedy_indexes[1:],
    ):
        left = candidate_position[left_seed_index]
        right = candidate_position[right_seed_index]
        if right not in {edge.target for edge in edges[left]}:
            raise AssertionError("required greedy edge is not safe in candidate DAG")

    _, shortest_path = _forward_shortest_path(edges)
    reverse_distances = _reverse_shortest_distances(edges)
    if reverse_distances[0] == float("inf"):
        raise AssertionError("candidate DAG has no finite shortest path")
    return CandidateDag(
        points=points,
        seed_indexes=selected_indexes,
        edges=edges,
        shortest_distance=reverse_distances[0],
        reverse_distances=reverse_distances,
        shortest_path=shortest_path,
    )


def _adds_effective_turn(
    previous: GridPoint,
    current: GridPoint,
    target: GridPoint,
    threshold_degrees: float,
) -> bool:
    incoming_x = current[0] - previous[0]
    incoming_y = current[1] - previous[1]
    outgoing_x = target[0] - current[0]
    outgoing_y = target[1] - current[1]
    if (incoming_x == 0 and incoming_y == 0) or (
        outgoing_x == 0 and outgoing_y == 0
    ):
        raise AssertionError("zero-length candidate edge cannot define a turn")
    cross = incoming_x * outgoing_y - incoming_y * outgoing_x
    dot = incoming_x * outgoing_x + incoming_y * outgoing_y
    angle = degrees(atan2(abs(cross), dot))
    return angle >= threshold_degrees


def _label_is_better(candidate: TurnLabel, existing: TurnLabel) -> bool:
    if candidate.distance != existing.distance:
        return candidate.distance < existing.distance
    if candidate.min_clearance_px != existing.min_clearance_px:
        return candidate.min_clearance_px > existing.min_clearance_px
    if len(candidate.path) != len(existing.path):
        return len(candidate.path) < len(existing.path)
    return candidate.path < existing.path


def _goal_label_is_better(
    candidate_key: tuple[int, int, int],
    candidate: TurnLabel,
    existing_key: tuple[int, int, int],
    existing: TurnLabel,
) -> bool:
    if candidate_key[0] != existing_key[0]:
        return candidate_key[0] < existing_key[0]
    return _label_is_better(candidate, existing)


def select_bounded_turn_path(
    dag: CandidateDag,
    *,
    distance_tolerance_px: float,
    turn_angle_degrees: float,
    max_turns: int,
    diagnostics: SolverDiagnostics,
) -> tuple[list[GridPoint], SolverQualityStatus]:
    if distance_tolerance_px < 0:
        raise ValueError("distance_tolerance_px must be non-negative")
    if not 0 < turn_angle_degrees <= 180:
        raise ValueError("turn_angle_degrees must be in 0..180")
    if not 0 <= max_turns <= 8:
        raise ValueError("max_turns must be in 0..8")

    diagnostics.local_states_peak = 0
    diagnostics.local_transitions = 0
    point_count = len(dag.points)
    if point_count < 2:
        raise AssertionError("candidate DAG must contain both endpoints")
    if len(set(dag.points)) != point_count:
        raise AssertionError("candidate DAG contains duplicate points")
    state_limit = (max_turns + 1) * point_count ** 2
    distance_limit = dag.shortest_distance + distance_tolerance_px
    epsilon = 1e-9
    labels: dict[tuple[int, int, int], TurnLabel] = {}
    states_by_current: list[set[tuple[int, int, int]]] = [
        set() for _ in dag.points
    ]

    def store(key: tuple[int, int, int], label: TurnLabel) -> None:
        existing = labels.get(key)
        if existing is not None and not _label_is_better(label, existing):
            return
        labels[key] = label
        states_by_current[key[2]].add(key)
        diagnostics.local_states_peak = max(
            diagnostics.local_states_peak,
            len(labels),
        )
        assert diagnostics.local_states_peak <= state_limit

    for edge in dag.edges[0]:
        diagnostics.local_transitions += 1
        if (
            edge.distance + dag.reverse_distances[edge.target]
            > distance_limit + epsilon
        ):
            continue
        store(
            (0, 0, edge.target),
            TurnLabel(
                distance=edge.distance,
                min_clearance_px=edge.min_clearance_px,
                path=(0, edge.target),
            ),
        )

    goal_index = point_count - 1
    for current in range(1, goal_index):
        for key in sorted(states_by_current[current]):
            label = labels[key]
            _, previous, _ = key
            for edge in dag.edges[current]:
                diagnostics.local_transitions += 1
                candidate_distance = label.distance + edge.distance
                if (
                    candidate_distance + dag.reverse_distances[edge.target]
                    > distance_limit + epsilon
                ):
                    continue
                turns = key[0] + int(
                    _adds_effective_turn(
                        dag.points[previous],
                        dag.points[current],
                        dag.points[edge.target],
                        turn_angle_degrees,
                    )
                )
                if turns > max_turns:
                    continue
                store(
                    (turns, current, edge.target),
                    TurnLabel(
                        distance=candidate_distance,
                        min_clearance_px=min(
                            label.min_clearance_px,
                            edge.min_clearance_px,
                        ),
                        path=label.path + (edge.target,),
                    ),
                )

    best_key = None
    best_label = None
    for key in sorted(states_by_current[goal_index]):
        label = labels[key]
        if label.distance > distance_limit + epsilon:
            continue
        if (
            best_label is None
            or best_key is None
            or _goal_label_is_better(key, label, best_key, best_label)
        ):
            best_key = key
            best_label = label

    if best_label is None:
        return (
            [dag.points[index] for index in dag.shortest_path],
            "fallbackTurnLimit",
        )
    return (
        [dag.points[index] for index in best_label.path],
        "optimized",
    )


def optimize_visible_path(
    context: SolveContext,
    seed_path: Sequence[GridPoint],
    *,
    candidate_limit: int,
    seed_index_radius: int,
    distance_tolerance_px: float,
    turn_angle_degrees: float,
    max_turns: int,
) -> tuple[list[GridPoint], SolverQualityStatus]:
    context.diagnostics.local_states_peak = 0
    context.diagnostics.local_transitions = 0
    greedy_path = simplify_visible_path(context, seed_path)
    dag = build_local_candidate_dag(
        context,
        seed_path,
        greedy_path,
        candidate_limit=candidate_limit,
        seed_index_radius=seed_index_radius,
    )
    if dag is None:
        return greedy_path, "fallbackCandidateLimit"
    return select_bounded_turn_path(
        dag,
        distance_tolerance_px=distance_tolerance_px,
        turn_angle_degrees=turn_angle_degrees,
        max_turns=max_turns,
        diagnostics=context.diagnostics,
    )


def _to_percent(
    point: GridPoint, width: int, height: int
) -> tuple[float, float]:
    return (
        round(point[0] / (width - 1) * 100, 3),
        round(point[1] / (height - 1) * 100, 3),
    )


def _analyze_turns(
    points: Sequence[tuple[float, float]],
    *,
    aspect: float,
    turn_angle_degrees: float,
) -> tuple[int, tuple[int, ...]]:
    unique: list[tuple[int, tuple[float, float]]] = []
    for raw_index, point in enumerate(points):
        scaled = (float(point[0]), float(point[1]) * aspect)
        if not unique or scaled != unique[-1][1]:
            unique.append((raw_index, scaled))

    turn_indexes: list[int] = []
    for left, middle, right in zip(unique, unique[1:], unique[2:]):
        ax = middle[1][0] - left[1][0]
        ay = middle[1][1] - left[1][1]
        bx = right[1][0] - middle[1][0]
        by = right[1][1] - middle[1][1]
        angle = degrees(atan2(abs(ax * by - ay * bx), ax * bx + ay * by))
        if angle >= turn_angle_degrees:
            turn_indexes.append(middle[0])

    if not points:
        semantic_indexes: tuple[int, ...] = ()
    elif len(points) == 1:
        semantic_indexes = (0,)
    else:
        semantic_indexes = (0, *turn_indexes, len(points) - 1)
    return len(turn_indexes), semantic_indexes


def build_semantic_point_indexes(
    pixels: Sequence[GridPoint],
    *,
    turn_angle_degrees: float,
) -> list[int]:
    _, indexes = _analyze_turns(
        pixels,
        aspect=1.0,
        turn_angle_degrees=turn_angle_degrees,
    )
    return list(indexes)


def _quality(
    surface: RoutingSurface,
    pixels: Sequence[GridPoint],
    *,
    turn_angle_degrees: float,
) -> tuple[
    list[tuple[float, float]],
    list[float],
    int,
    float,
    tuple[int, ...],
    str,
]:
    height, width = surface.safe_mask.shape
    aspect = height / width
    percentages = [_to_percent(point, width, height) for point in pixels]
    lengths = [
        hypot(right[0] - left[0], (right[1] - left[1]) * aspect)
        for left, right in zip(percentages, percentages[1:])
    ]
    turns, semantic_indexes = _analyze_turns(
        percentages,
        aspect=aspect,
        turn_angle_degrees=turn_angle_degrees,
    )
    clearances = [
        float(surface.clearance_field[y, x])
        for left, right in zip(pixels, pixels[1:])
        for x, y in supercover_pixels(left, right)
    ]
    forward = json.dumps(
        percentages, ensure_ascii=False, separators=(",", ":")
    )
    reverse = json.dumps(
        list(reversed(percentages)),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    canonical = min(forward, reverse).encode("utf-8")
    return (
        percentages,
        lengths,
        turns,
        min(clearances),
        semantic_indexes,
        hashlib.sha256(canonical).hexdigest(),
    )


def solve_safe_path(
    surface: RoutingSurface,
    source_percent: tuple[float, float],
    target_percent: tuple[float, float],
    *,
    max_anchor_snap_px: int,
    endpoint_bridge_radius_cells: int,
    local_candidate_limit: int,
    local_seed_index_radius: int,
    local_max_turns: int,
    path_distance_tie_tolerance_px: float = 6,
    turn_angle_degrees: float = 25,
    diagnostics: SolverDiagnostics | None = None,
) -> PathResult | None:
    active = diagnostics if diagnostics is not None else SolverDiagnostics()
    active.failure_reason = None
    active.start_bridge_count = 0
    active.goal_bridge_count = 0

    source = snap_anchor(
        surface, source_percent, max_distance_px=max_anchor_snap_px
    )
    if source is None:
        active.failure_reason = "sourceAnchorSnapFailed"
        return None
    target = snap_anchor(
        surface, target_percent, max_distance_px=max_anchor_snap_px
    )
    if target is None:
        active.failure_reason = "targetAnchorSnapFailed"
        return None
    if source.pixel == target.pixel:
        active.failure_reason = "coLocated"
        return None

    context = SolveContext(surface, active)
    if context.segment_is_safe(source.pixel, target.pixel):
        pixels = [source.pixel, target.pixel]
        quality_status: SolverQualityStatus = "direct"
    else:
        seed = grid_a_star(
            context,
            source.pixel,
            target.pixel,
            endpoint_bridge_radius_cells=endpoint_bridge_radius_cells,
        )
        if seed is None:
            if active.start_bridge_count == 0:
                active.failure_reason = "startEndpointBridgeUnavailable"
            elif active.goal_bridge_count == 0:
                active.failure_reason = "goalEndpointBridgeUnavailable"
            else:
                active.failure_reason = "gridPathUnavailable"
            return None
        pixels, quality_status = optimize_visible_path(
            context,
            seed,
            candidate_limit=local_candidate_limit,
            seed_index_radius=local_seed_index_radius,
            distance_tolerance_px=path_distance_tie_tolerance_px,
            turn_angle_degrees=turn_angle_degrees,
            max_turns=local_max_turns,
        )

    if len(pixels) < 2 or pixels[0] == pixels[-1]:
        active.failure_reason = "finalPathUnavailable"
        return None
    for left, right in zip(pixels, pixels[1:]):
        if not line_is_safe(surface, left, right):
            active.failure_reason = "finalAuditFailed"
            raise AssertionError(f"final route edge is unsafe: {left!r} -> {right!r}")

    percentages, lengths, turns, clearance, semantic_indexes, geometry_hash = _quality(
        surface,
        pixels,
        turn_angle_degrees=turn_angle_degrees,
    )
    return PathResult(
        points=tuple(percentages),
        pixel_points=tuple(pixels),
        route_length=round(sum(lengths), 6),
        min_clearance_px=round(clearance, 3),
        effective_turn_count=turns,
        shortest_segment=round(min(lengths), 6),
        semantic_point_indexes=tuple(semantic_indexes),
        geometry_sha256=geometry_hash,
        source_snap_distance_px=round(source.distance_px, 3),
        target_snap_distance_px=round(target.distance_px, 3),
        solver_quality_status=quality_status,
    )
