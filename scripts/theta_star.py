from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import heapq
import itertools
import json
from math import acos, degrees, hypot

from routing_surface import RoutingSurface, line_is_safe, snap_anchor, supercover_pixels


GridPoint = tuple[int, int]
IncomingState = tuple[GridPoint, GridPoint]
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


def _distance(left: GridPoint, right: GridPoint) -> float:
    return hypot(right[0] - left[0], right[1] - left[1])


def _direction_change_degrees(
    first: GridPoint,
    middle: GridPoint,
    last: GridPoint,
) -> float:
    ax, ay = middle[0] - first[0], middle[1] - first[1]
    bx, by = last[0] - middle[0], last[1] - middle[1]
    first_length = _distance(first, middle)
    second_length = _distance(middle, last)
    if first_length == 0 or second_length == 0:
        return 0.0
    cosine = max(
        -1.0,
        min(1.0, (ax * bx + ay * by) / (first_length * second_length)),
    )
    return degrees(acos(cosine))


def _inside(surface: RoutingSurface, point: GridPoint) -> bool:
    x, y = point
    height, width = surface.safe_mask.shape
    return 0 <= x < width and 0 <= y < height and bool(surface.safe_mask[y, x])


def _neighbors(surface: RoutingSurface, current: GridPoint):
    for dx, dy in NEIGHBORS:
        candidate = (
            current[0] + dx * surface.cell_size_px,
            current[1] + dy * surface.cell_size_px,
        )
        if not _inside(surface, candidate):
            continue
        if dx and dy:
            if not _inside(surface, (candidate[0], current[1])):
                continue
            if not _inside(surface, (current[0], candidate[1])):
                continue
        if line_is_safe(surface, current, candidate):
            yield candidate


@dataclass(frozen=True)
class SearchLabel:
    distance: float
    turns: int
    min_clearance_px: float
    stable_key: tuple[int, int, int, int]
    point: GridPoint = (0, 0)
    parent: "SearchLabel | None" = field(default=None, compare=False, repr=False)


def _incoming_state(label: SearchLabel) -> IncomingState:
    if label.parent is None:
        return (label.point, label.point)
    parent = label.parent
    predecessor = parent.parent.point if parent.parent is not None else parent.point
    return (parent.point, predecessor)


def _dominates(left: SearchLabel, right: SearchLabel) -> bool:
    if not (
        left.distance <= right.distance
        and left.turns <= right.turns
        and left.min_clearance_px >= right.min_clearance_px
    ):
        return False
    return (
        left.distance < right.distance
        or left.turns < right.turns
        or left.min_clearance_px > right.min_clearance_px
        or left.stable_key <= right.stable_key
    )


def is_better_label(
    candidate: SearchLabel,
    current: SearchLabel | None,
    distance_tolerance_px: float,
) -> bool:
    if current is None:
        return True
    if candidate.distance < current.distance - distance_tolerance_px:
        return True
    if candidate.distance > current.distance + distance_tolerance_px:
        return False
    return (
        candidate.turns,
        -candidate.min_clearance_px,
        candidate.stable_key,
    ) < (
        current.turns,
        -current.min_clearance_px,
        current.stable_key,
    )


def _extend_label(
    surface: RoutingSurface,
    source_label: SearchLabel,
    candidate: GridPoint,
    turn_angle_degrees: float,
) -> SearchLabel:
    source = source_label.point
    previous = source_label.parent.point if source_label.parent is not None else source
    turn = 0 if previous == source else int(
        _direction_change_degrees(previous, source, candidate) >= turn_angle_degrees
    )
    segment_clearance = min(
        float(surface.clearance_field[y, x])
        for x, y in supercover_pixels(source, candidate)
    )
    return SearchLabel(
        distance=source_label.distance + _distance(source, candidate),
        turns=source_label.turns + turn,
        min_clearance_px=min(source_label.min_clearance_px, segment_clearance),
        stable_key=(source[1], source[0], candidate[1], candidate[0]),
        point=candidate,
        parent=source_label,
    )


def _path_from_label(goal_label: SearchLabel) -> list[GridPoint]:
    path: list[GridPoint] = []
    cursor: SearchLabel | None = goal_label
    while cursor is not None:
        path.append(cursor.point)
        cursor = cursor.parent
    path.reverse()
    return path


def theta_star(
    surface: RoutingSurface,
    start: GridPoint,
    goal: GridPoint,
    *,
    distance_tolerance_px: float,
    turn_angle_degrees: float,
) -> list[GridPoint] | None:
    start_clearance = float(surface.clearance_field[start[1], start[0]])
    start_label = SearchLabel(
        0.0,
        0,
        start_clearance,
        (start[1], start[0], start[1], start[0]),
        point=start,
    )
    frontiers: dict[
        GridPoint,
        dict[IncomingState, list[SearchLabel]],
    ] = {}
    minimum_distances: dict[GridPoint, float] = {}
    active_labels: dict[int, SearchLabel] = {}
    shortest_goal_distance: float | None = None
    popped_goals: list[SearchLabel] = []
    sequence = itertools.count()
    heap = []

    def deactivate(label: SearchLabel) -> None:
        if active_labels.get(id(label)) is label:
            del active_labels[id(label)]

    def prune_distance_window(point: GridPoint, cutoff: float) -> None:
        point_frontiers = frontiers.get(point)
        if point_frontiers is None:
            return
        for state, frontier in list(point_frontiers.items()):
            retained = []
            for existing in frontier:
                if existing.distance <= cutoff:
                    retained.append(existing)
                else:
                    deactivate(existing)
            if retained:
                point_frontiers[state] = retained
            else:
                del point_frontiers[state]

    def register(label: SearchLabel) -> bool:
        nonlocal shortest_goal_distance
        point = label.point
        minimum = minimum_distances.get(point)
        if minimum is None or label.distance < minimum:
            minimum = label.distance
            minimum_distances[point] = minimum
            prune_distance_window(
                point,
                minimum + distance_tolerance_px,
            )
            if point == goal:
                shortest_goal_distance = minimum
        if label.distance > minimum + distance_tolerance_px:
            return False

        state = _incoming_state(label)
        point_frontiers = frontiers.setdefault(point, {})
        frontier = point_frontiers.setdefault(state, [])
        if any(_dominates(existing, label) for existing in frontier):
            return False

        retained = []
        for existing in frontier:
            if _dominates(label, existing):
                deactivate(existing)
            else:
                retained.append(existing)
        retained.append(label)
        point_frontiers[state] = retained
        active_labels[id(label)] = label
        return True

    def push(point: GridPoint, label: SearchLabel) -> None:
        heapq.heappush(
            heap,
            (
                label.distance + _distance(point, goal),
                label.distance,
                label.turns,
                -label.min_clearance_px,
                point[1],
                point[0],
                next(sequence),
                point,
                label,
            ),
        )

    register(start_label)
    push(start, start_label)
    while heap:
        estimate, _, _, _, _, _, _, current, popped_label = heapq.heappop(heap)
        if active_labels.get(id(popped_label)) is not popped_label:
            continue
        if (
            shortest_goal_distance is not None
            and estimate > shortest_goal_distance + distance_tolerance_px
        ):
            break
        if current == goal:
            popped_goals.append(popped_label)
            continue
        candidates = list(_neighbors(surface, current))
        if goal not in candidates and line_is_safe(surface, current, goal):
            candidates.append(goal)
        for candidate in candidates:
            parent_label = popped_label.parent
            parent = parent_label.point if parent_label is not None else current
            if candidate == current or (
                parent_label is not None and candidate == parent
            ):
                continue
            if parent_label is not None and line_is_safe(surface, parent, candidate):
                source_label = parent_label
            else:
                source_label = popped_label
            candidate_label = _extend_label(
                surface,
                source_label,
                candidate,
                turn_angle_degrees,
            )
            if not register(candidate_label):
                continue
            push(candidate, candidate_label)
    if shortest_goal_distance is None:
        return None
    eligible_goals = [
        label
        for label in popped_goals
        if active_labels.get(id(label)) is label
        and label.distance <= shortest_goal_distance + distance_tolerance_px
    ]
    if not eligible_goals:
        return None
    best_goal = min(
        eligible_goals,
        key=lambda label: (
            label.turns,
            -label.min_clearance_px,
            label.stable_key,
            label.distance,
        ),
    )
    return _path_from_label(best_goal)


def simplify_visible_path(
    surface: RoutingSurface,
    path: list[GridPoint],
) -> list[GridPoint]:
    if len(path) <= 2:
        return list(path)
    result = [path[0]]
    cursor = 0
    while cursor < len(path) - 1:
        target = len(path) - 1
        while target > cursor + 1 and not line_is_safe(
            surface, path[cursor], path[target]
        ):
            target -= 1
        result.append(path[target])
        cursor = target
    return result


def _to_percent(point: GridPoint, width: int, height: int) -> tuple[float, float]:
    return (
        round(point[0] / (width - 1) * 100, 3),
        round(point[1] / (height - 1) * 100, 3),
    )


def build_semantic_point_indexes(
    pixels: list[GridPoint],
    *,
    turn_angle_degrees: float,
) -> list[int]:
    if len(pixels) <= 2:
        return list(range(len(pixels)))
    indexes = [0]
    for index in range(1, len(pixels) - 1):
        first, middle, last = pixels[index - 1 : index + 2]
        ax, ay = middle[0] - first[0], middle[1] - first[1]
        bx, by = last[0] - middle[0], last[1] - middle[1]
        cosine = max(
            -1.0,
            min(
                1.0,
                (ax * bx + ay * by)
                / (_distance(first, middle) * _distance(middle, last)),
            ),
        )
        if degrees(acos(cosine)) >= turn_angle_degrees:
            indexes.append(index)
    indexes.append(len(pixels) - 1)
    return indexes


def _quality(
    surface: RoutingSurface,
    pixels: list[GridPoint],
    *,
    turn_angle_degrees: float,
):
    height, width = surface.safe_mask.shape
    aspect = height / width
    percentages = [_to_percent(point, width, height) for point in pixels]
    lengths = [
        hypot(right[0] - left[0], (right[1] - left[1]) * aspect)
        for left, right in zip(percentages, percentages[1:])
    ]
    turns = 0
    for first, middle, last in zip(pixels, pixels[1:], pixels[2:]):
        ax, ay = middle[0] - first[0], middle[1] - first[1]
        bx, by = last[0] - middle[0], last[1] - middle[1]
        cosine = max(
            -1.0,
            min(
                1.0,
                (ax * bx + ay * by)
                / (_distance(first, middle) * _distance(middle, last)),
            ),
        )
        if degrees(acos(cosine)) >= turn_angle_degrees:
            turns += 1
    clearances = [
        float(surface.clearance_field[y, x])
        for left, right in zip(pixels, pixels[1:])
        for x, y in supercover_pixels(left, right)
    ]
    forward = json.dumps(percentages, ensure_ascii=False, separators=(",", ":"))
    reverse = json.dumps(
        list(reversed(percentages)), ensure_ascii=False, separators=(",", ":")
    )
    canonical = min(forward, reverse).encode("utf-8")
    return (
        percentages,
        lengths,
        turns,
        min(clearances),
        hashlib.sha256(canonical).hexdigest(),
    )


def solve_safe_path(
    surface: RoutingSurface,
    source_percent: tuple[float, float],
    target_percent: tuple[float, float],
    *,
    max_anchor_snap_px: int,
    path_distance_tie_tolerance_px: float = 6,
    turn_angle_degrees: float = 25,
) -> PathResult | None:
    source = snap_anchor(
        surface, source_percent, max_distance_px=max_anchor_snap_px
    )
    target = snap_anchor(
        surface, target_percent, max_distance_px=max_anchor_snap_px
    )
    if source is None or target is None:
        return None
    if source.pixel == target.pixel:
        return None
    raw = theta_star(
        surface,
        source.pixel,
        target.pixel,
        distance_tolerance_px=path_distance_tie_tolerance_px,
        turn_angle_degrees=turn_angle_degrees,
    )
    if raw is None:
        return None
    pixels = simplify_visible_path(surface, raw)
    percentages, lengths, turns, clearance, geometry_hash = _quality(
        surface,
        pixels,
        turn_angle_degrees=turn_angle_degrees,
    )
    semantic_indexes = build_semantic_point_indexes(
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
    )
