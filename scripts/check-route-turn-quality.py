#!/usr/bin/env python3
"""Validate generated route turns and optionally export local review evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple, Sequence

from PIL import Image, ImageDraw, ImageFont

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


SPOKEN_TURN_DEGREES = 25.0
MAX_ALLOWED_TURNS = 10
LOW_ANGLE_DEGREES = 5.0
MICRO_ZIGZAG_ALTERNATIONS = 5
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
DATE_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
REVIEW_FIELDS = {
    "geometrySha256",
    "decision",
    "reviewer",
    "reviewedAt",
    "reason",
}
REVIEW_DECISIONS = {
    "approvedNecessaryComplexGeometry",
    "approvedFallbackAfterVisualReview",
}
VALID_SOLVER_STATUSES = {"direct", "optimized"}
ROUTE_PROVENANCE_PREFIX = "// route-provenance: "
ROUTE_PROVENANCE_FIELDS = frozenset(
    {
        "algorithmVersion",
        "routingPolicySha256",
        "navigationPolicySha256",
        "autoValidationStatus",
        "reviewStatus",
    }
)
ROUTE_PROVENANCE_KEYS = frozenset({"schemaVersion", *ROUTE_PROVENANCE_FIELDS})
REPORT_FIELDS = (
    "key",
    "kind",
    "floor",
    "pointCount",
    "effectiveTurns",
    "lowAngleAlternations",
    "minClearancePx",
    "solverQualityStatus",
    "geometrySha256",
    "reviewRequired",
    "reviewDecision",
    "routeImage",
)


class GeometryMetrics(NamedTuple):
    spoken_turn_count: int
    semantic_point_indexes: tuple[int, ...]
    low_angle_alternations: int
    has_micro_zigzag: bool


class RouteRow(NamedTuple):
    kind: str
    key: str
    record: dict[str, Any]
    metrics: GeometryMetrics
    geometry_sha256: str
    review_required: bool
    review_decision: str


class InputError(ValueError):
    """An input, schema, or rendering usage error (CLI exit code 2)."""


def analyze_geometry(
    points: Sequence[Sequence[float]], image_size: Sequence[float]
) -> GeometryMetrics:
    """Return aspect-corrected turn metrics while preserving raw point indexes."""
    width, height = float(image_size[0]), float(image_size[1])
    unique: list[tuple[int, tuple[float, float]]] = []
    for raw_index, point in enumerate(points):
        pixel = (float(point[0]) * width / 100.0, float(point[1]) * height / 100.0)
        if not unique or pixel != unique[-1][1]:
            unique.append((raw_index, pixel))

    spoken_indexes: list[int] = []
    current_low_angle_sign: int | None = None
    current_low_angle_alternations = 0
    max_low_angle_alternations = 0
    for left, middle, right in zip(unique, unique[1:], unique[2:]):
        first_vector = (
            middle[1][0] - left[1][0],
            middle[1][1] - left[1][1],
        )
        second_vector = (
            right[1][0] - middle[1][0],
            right[1][1] - middle[1][1],
        )
        cross = first_vector[0] * second_vector[1] - first_vector[1] * second_vector[0]
        dot = first_vector[0] * second_vector[0] + first_vector[1] * second_vector[1]
        signed_angle = math.degrees(math.atan2(cross, dot))
        absolute_angle = abs(signed_angle)
        if absolute_angle >= SPOKEN_TURN_DEGREES:
            spoken_indexes.append(middle[0])
            current_low_angle_sign = None
            current_low_angle_alternations = 0
        elif absolute_angle >= LOW_ANGLE_DEGREES:
            sign = 1 if signed_angle > 0 else -1
            if current_low_angle_sign is not None and sign != current_low_angle_sign:
                current_low_angle_alternations += 1
            current_low_angle_sign = sign
            max_low_angle_alternations = max(
                max_low_angle_alternations,
                current_low_angle_alternations,
            )
    if not points:
        semantic_indexes: tuple[int, ...] = ()
    elif len(points) == 1:
        semantic_indexes = (0,)
    else:
        semantic_indexes = (0, *spoken_indexes, len(points) - 1)
    return GeometryMetrics(
        spoken_turn_count=len(spoken_indexes),
        semantic_point_indexes=semantic_indexes,
        low_angle_alternations=max_low_angle_alternations,
        has_micro_zigzag=(
            max_low_angle_alternations >= MICRO_ZIGZAG_ALTERNATIONS
        ),
    )


def canonical_json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def geometry_sha256(points: Sequence[Sequence[float]]) -> str:
    forward = json.dumps(points, ensure_ascii=False, separators=(",", ":"))
    reverse = json.dumps(
        list(reversed(points)), ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(min(forward, reverse).encode("utf-8")).hexdigest()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


def _reject_duplicate_object_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key == "__proto__":
            raise ValueError("forbidden JSON key '__proto__'")
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _strict_json_loads(text: str) -> Any:
    return json.loads(
        text,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_reject_duplicate_object_pairs,
    )


def _read_route_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise InputError(f"cannot read route input {path}: {error}") from error


def _parse_commonjs_object(path: Path, text: str) -> dict[str, Any]:
    text = text.strip()
    while text.startswith("//"):
        _, separator, text = text.partition("\n")
        if not separator:
            raise InputError(f"{path}: missing CommonJS export")
        text = text.lstrip()
    prefix = "module.exports ="
    if not text.startswith(prefix):
        raise InputError(f"{path}: expected JSON-compatible CommonJS export")
    body = text[len(prefix) :].strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    try:
        value = _strict_json_loads(body)
    except (json.JSONDecodeError, ValueError) as error:
        raise InputError(f"{path}: invalid JSON-compatible CommonJS object: {error}") from error
    if not isinstance(value, dict):
        raise InputError(f"{path}: CommonJS export must be an object")
    return value


def load_commonjs_object(path: Path) -> dict[str, Any]:
    return _parse_commonjs_object(path, _read_route_text(path))


def _validate_route_provenance(
    path: Path,
    text: str,
    policy_expectations: tuple[str, str, str],
    expected_review_status: str = "pending",
) -> None:
    marker = ROUTE_PROVENANCE_PREFIX.rstrip()
    lines = text.splitlines()
    header_indexes = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith(marker)
    ]
    if not header_indexes:
        raise InputError(f"{path}: missing route-provenance header")
    if len(header_indexes) != 1:
        raise InputError(f"{path}: duplicate route-provenance headers")
    header_index = header_indexes[0]
    header_line = lines[header_index]
    if not header_line.startswith(ROUTE_PROVENANCE_PREFIX):
        raise InputError(f"{path}: malformed route-provenance header")
    if (
        header_index + 1 >= len(lines)
        or not lines[header_index + 1].startswith("module.exports =")
    ):
        raise InputError(
            f"{path}: route-provenance header must appear immediately before module.exports"
        )
    try:
        provenance = _strict_json_loads(
            header_line.removeprefix(ROUTE_PROVENANCE_PREFIX)
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise InputError(f"{path}: invalid route-provenance JSON: {error}") from error
    if not isinstance(provenance, dict):
        raise InputError(f"{path}: route-provenance must be an object")
    if set(provenance) != ROUTE_PROVENANCE_KEYS:
        raise InputError(
            f"{path}: route-provenance must contain exactly {sorted(ROUTE_PROVENANCE_KEYS)}"
        )

    algorithm_version, routing_hash, navigation_hash = policy_expectations
    checks: tuple[tuple[bool, str], ...] = (
        (
            type(provenance.get("schemaVersion")) is int
            and provenance.get("schemaVersion") == 1,
            "schemaVersion must be integer 1",
        ),
        (
            isinstance(provenance.get("algorithmVersion"), str)
            and provenance.get("algorithmVersion") == algorithm_version,
            f"algorithmVersion must equal {algorithm_version}",
        ),
        (
            isinstance(provenance.get("routingPolicySha256"), str)
            and provenance.get("routingPolicySha256") == routing_hash,
            "routingPolicySha256 does not match the current policy",
        ),
        (
            isinstance(provenance.get("navigationPolicySha256"), str)
            and provenance.get("navigationPolicySha256") == navigation_hash,
            "navigationPolicySha256 does not match the current policy",
        ),
        (
            isinstance(provenance.get("autoValidationStatus"), str)
            and provenance.get("autoValidationStatus") == "passed",
            "autoValidationStatus must equal passed",
        ),
        (
            isinstance(provenance.get("reviewStatus"), str)
            and provenance.get("reviewStatus") == expected_review_status,
            f"reviewStatus must equal {expected_review_status}",
        ),
    )
    for passed, message in checks:
        if not passed:
            raise InputError(f"{path}: route-provenance {message}")


def load_route_file(
    path: Path,
    policy_expectations: tuple[str, str, str],
    expected_review_status: str = "pending",
) -> dict[str, Any]:
    text = _read_route_text(path)
    _validate_route_provenance(
        path,
        text,
        policy_expectations,
        expected_review_status,
    )
    return _parse_commonjs_object(path, text)


def load_json(path: Path, label: str) -> Any:
    try:
        return _strict_json_loads(path.read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise InputError(f"cannot read {label} {path}: {error}") from error
    except (json.JSONDecodeError, ValueError) as error:
        raise InputError(f"invalid {label} JSON in {path}: {error}") from error


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _positive_finite(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def _point_list(value: object, *, kind: str, key: str) -> list[list[float]]:
    if not isinstance(value, list) or not value:
        raise InputError(f"{kind}:{key}: points must be a non-empty array")
    result: list[list[float]] = []
    for index, point in enumerate(value):
        if not isinstance(point, list) or len(point) != 2:
            raise InputError(f"{kind}:{key}: points[{index}] must contain two numbers")
        if any(
            isinstance(component, bool)
            or not isinstance(component, (int, float))
            or not math.isfinite(component)
            for component in point
        ):
            raise InputError(f"{kind}:{key}: points[{index}] must contain finite numbers")
        result.append(point)
    return result


def _image_size(value: object, *, kind: str, key: str) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(not _positive_finite(component) for component in value)
    ):
        raise InputError(f"{kind}:{key}: imageSize must contain two positive numbers")
    return value


def load_review_decisions(path: Path) -> dict[str, dict[str, str]]:
    document = load_json(path, "review decisions")
    if not isinstance(document, dict) or set(document) != {"schemaVersion", "reviews"}:
        raise InputError(
            "review decisions must contain exactly schemaVersion and reviews"
        )
    if (
        type(document.get("schemaVersion")) is not int
        or document.get("schemaVersion") != 1
        or not isinstance(document.get("reviews"), list)
    ):
        raise InputError("review decisions require schemaVersion 1 and a reviews array")
    indexed: dict[str, dict[str, str]] = {}
    for index, item in enumerate(document["reviews"]):
        prefix = f"review decisions reviews[{index}]"
        if not isinstance(item, dict) or set(item) != REVIEW_FIELDS:
            raise InputError(f"{prefix} must contain exactly {sorted(REVIEW_FIELDS)}")
        geometry_hash = item.get("geometrySha256")
        if not isinstance(geometry_hash, str) or not HEX64.fullmatch(geometry_hash):
            raise InputError(f"{prefix} geometrySha256 must be 64 lowercase hex characters")
        if item.get("decision") not in REVIEW_DECISIONS:
            raise InputError(f"{prefix} decision is not recognized")
        for field in ("reviewer", "reason"):
            if not _nonempty(item.get(field)):
                raise InputError(f"{prefix} {field} must be a non-empty string")
        reviewed_at = item.get("reviewedAt")
        if not isinstance(reviewed_at, str) or not DATE_TEXT.fullmatch(reviewed_at):
            raise InputError(f"{prefix} reviewedAt must use YYYY-MM-DD")
        try:
            date.fromisoformat(reviewed_at)
        except ValueError as error:
            raise InputError(f"{prefix} reviewedAt is not a valid date") from error
        prior = indexed.get(geometry_hash)
        if prior:
            if prior["decision"] != item["decision"]:
                raise InputError(
                    f"review decisions contain conflicting decisions for {geometry_hash}"
                )
            raise InputError(
                f"review decisions contain a duplicate decision for {geometry_hash}"
            )
        indexed[geometry_hash] = item
    return indexed


def _load_policy_expectations(project_root: Path) -> tuple[str, str, str]:
    routing = load_json(project_root / "config" / "routing-policy.json", "routing policy")
    navigation = load_json(
        project_root / "config" / "navigation-policy.json", "navigation policy"
    )
    if not isinstance(routing, dict) or not _nonempty(routing.get("algorithmVersion")):
        raise InputError("routing policy lacks algorithmVersion")
    if not isinstance(navigation, dict):
        raise InputError("navigation policy must be an object")
    return (
        routing["algorithmVersion"],
        canonical_json_sha256(routing),
        canonical_json_sha256(navigation),
    )


def _semantic_indexes_are_strict(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        and all(left < right for left, right in zip(value, value[1:]))
    )


def _geometry_context(record: dict[str, Any]) -> tuple[object, ...]:
    image_size = record.get("imageSize")
    normalized_size = (
        tuple(float(value) for value in image_size)
        if isinstance(image_size, list)
        else ()
    )
    return (
        str(record.get("floor") or ""),
        str(record.get("image") or ""),
        str(record.get("sourceFloorMapSha256") or ""),
        normalized_size,
    )


def _evidence_group_key(row: RouteRow) -> tuple[object, ...]:
    return (row.geometry_sha256, *_geometry_context(row.record))


def validate_routes(
    records_by_kind: dict[str, dict[str, Any]],
    reviews: dict[str, dict[str, str]],
) -> tuple[list[RouteRow], list[str]]:
    rows: list[RouteRow] = []
    failures: list[str] = []
    encountered_hashes: set[str] = set()
    contexts_by_hash: dict[str, set[tuple[object, ...]]] = {}
    approval_bearing_hashes: set[str] = set()
    for kind in sorted(records_by_kind):
        records = records_by_kind[kind]
        for key in sorted(records):
            record = records[key]
            if not isinstance(key, str) or not isinstance(record, dict):
                raise InputError(f"{kind}: route keys must map to objects")
            points = _point_list(record.get("points"), kind=kind, key=key)
            image_size = _image_size(record.get("imageSize"), kind=kind, key=key)
            metrics = analyze_geometry(points, image_size)
            moving = len({(float(point[0]), float(point[1])) for point in points}) > 1
            raw_record_hash = record.get("geometrySha256", "")
            record_hash = raw_record_hash if isinstance(raw_record_hash, str) else ""
            fallback = str(record.get("solverQualityStatus", "")).startswith("fallback")
            review_required = fallback or (moving and metrics.spoken_turn_count > 5)
            decision = reviews.get(record_hash, {}).get("decision", "")
            prefix = f"{kind}:{key}"
            for field in sorted(ROUTE_PROVENANCE_FIELDS.intersection(record)):
                failures.append(
                    f"{prefix}: {field} must be declared only in route-provenance"
                )

            if not moving:
                if fallback:
                    expected_geometry_hash = geometry_sha256(points)
                    if (
                        not HEX64.fullmatch(record_hash)
                        or record_hash != expected_geometry_hash
                    ):
                        failures.append(
                            f"{prefix}: geometrySha256 does not match route points"
                        )
                    else:
                        encountered_hashes.add(record_hash)
                        contexts_by_hash.setdefault(record_hash, set()).add(
                            _geometry_context(record)
                        )
                        approval_bearing_hashes.add(record_hash)
                    if decision != "approvedFallbackAfterVisualReview":
                        failures.append(
                            f"{prefix}: fallbackApprovalRequired for {record_hash}"
                        )
                rows.append(
                    RouteRow(
                        kind,
                        key,
                        record,
                        metrics,
                        str(record_hash),
                        review_required,
                        decision,
                    )
                )
                continue

            expected_geometry_hash = geometry_sha256(points)
            checks: list[tuple[bool, str]] = [
                (
                    isinstance(raw_record_hash, str)
                    and HEX64.fullmatch(record_hash) is not None
                    and record_hash == expected_geometry_hash,
                    "geometrySha256 does not match route points",
                ),
                (
                    record.get("solverQualityStatus") in VALID_SOLVER_STATUSES or fallback,
                    "solverQualityStatus must be direct, optimized, or fallback-prefixed",
                ),
                (_positive_finite(record.get("routeLength")), "routeLength must be positive and finite"),
                (
                    _positive_finite(record.get("minClearancePx")),
                    "minClearancePx must be positive and finite",
                ),
                (
                    isinstance(record.get("effectiveTurnCount"), int)
                    and not isinstance(record.get("effectiveTurnCount"), bool)
                    and record.get("effectiveTurnCount") == metrics.spoken_turn_count,
                    f"effectiveTurnCount must equal recomputed value {metrics.spoken_turn_count}",
                ),
                (
                    _positive_finite(record.get("shortestSegment")),
                    "shortestSegment must be positive and finite",
                ),
                (
                    _semantic_indexes_are_strict(record.get("semanticPointIndexes"))
                    and tuple(record.get("semanticPointIndexes"))
                    == metrics.semantic_point_indexes,
                    f"semanticPointIndexes must equal {list(metrics.semantic_point_indexes)}",
                ),
            ]
            for passed, message in checks:
                if not passed:
                    failures.append(f"{prefix}: {message}")
            if metrics.has_micro_zigzag:
                failures.append(
                    f"{prefix}: microZigzag ({metrics.low_angle_alternations} low-angle alternations)"
                )
            if HEX64.fullmatch(record_hash):
                encountered_hashes.add(record_hash)
                contexts_by_hash.setdefault(record_hash, set()).add(
                    _geometry_context(record)
                )
                if review_required or record_hash in reviews:
                    approval_bearing_hashes.add(record_hash)
            if fallback and decision != "approvedFallbackAfterVisualReview":
                failures.append(f"{prefix}: fallbackApprovalRequired for {record_hash}")
            elif metrics.spoken_turn_count > MAX_ALLOWED_TURNS:
                failures.append(
                    f"{prefix}: excessiveTurns {metrics.spoken_turn_count} "
                    f"exceeds {MAX_ALLOWED_TURNS}; route must be replanned"
                )
            elif metrics.spoken_turn_count > 5 and decision != "approvedNecessaryComplexGeometry":
                failures.append(f"{prefix}: reviewRequired for {record_hash}")
            rows.append(
                RouteRow(
                    kind,
                    key,
                    record,
                    metrics,
                    str(record_hash),
                    review_required,
                    decision,
                )
            )
    for geometry_hash, contexts in sorted(contexts_by_hash.items()):
        if geometry_hash in approval_bearing_hashes and len(contexts) > 1:
            failures.append(
                f"geometry:{geometry_hash}: geometryContextCollision across {len(contexts)} map contexts"
            )
    for review_hash in sorted(set(reviews) - encountered_hashes):
        failures.append(f"reviews:{review_hash}: staleReview decision has no matching geometry")
    return rows, sorted(set(failures))


def _floor_sort(value: object) -> tuple[int, str]:
    text = str(value or "")
    match = re.search(r"\d+", text)
    return (int(match.group()) if match else 10**9, text)


def _row_sort(row: RouteRow) -> tuple[object, ...]:
    return (
        -row.metrics.spoken_turn_count,
        _floor_sort(row.record.get("floor")),
        row.kind,
        row.key,
    )


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path(r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _floor_map_path(floor_dir: Path, record: dict[str, Any]) -> Path:
    basename = Path(str(record.get("image") or "")).name
    if basename and (floor_dir / basename).is_file():
        return floor_dir / basename
    floor_match = re.search(r"\d+", str(record.get("floor") or ""))
    if floor_match:
        floor = floor_match.group()
        for suffix in ("F.jpg", "F.jpeg", "f.jpg", "f.jpeg"):
            candidate = floor_dir / f"{floor}{suffix}"
            if candidate.is_file():
                return candidate
    raise InputError(
        f"missing floor map for {record.get('floor')!r} in {floor_dir}"
    )


def _crop_box(
    pixel_points: list[tuple[float, float]], width: int, height: int
) -> tuple[int, int, int, int]:
    xs = [point[0] for point in pixel_points]
    ys = [point[1] for point in pixel_points]
    route_width = max(max(xs) - min(xs), width * 0.12)
    route_height = max(max(ys) - min(ys), height * 0.12)
    crop_width = min(width, max(route_width * 1.45, width * 0.28))
    crop_height = min(height, max(route_height * 1.45, height * 0.28))
    center_x = (min(xs) + max(xs)) / 2
    center_y = (min(ys) + max(ys)) / 2
    left = max(0.0, min(width - crop_width, center_x - crop_width / 2))
    top = max(0.0, min(height - crop_height, center_y - crop_height / 2))
    return (
        int(round(left)),
        int(round(top)),
        int(round(left + crop_width)),
        int(round(top + crop_height)),
    )


def _draw_arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], size: int) -> None:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < size * 3:
        return
    ux, uy = dx / length, dy / length
    center = (start[0] + dx * 0.62, start[1] + dy * 0.62)
    tip = (center[0] + ux * size, center[1] + uy * size)
    back = (center[0] - ux * size, center[1] - uy * size)
    perpendicular = (-uy * size * 0.65, ux * size * 0.65)
    draw.polygon(
        [
            tip,
            (back[0] + perpendicular[0], back[1] + perpendicular[1]),
            (back[0] - perpendicular[0], back[1] - perpendicular[1]),
        ],
        fill=(202, 25, 32),
        outline=(255, 255, 255),
    )


def _route_card(base: Image.Image, row: RouteRow, route_keys: list[str]) -> Image.Image:
    record = row.record
    points = record["points"]
    full_points = [
        (float(point[0]) * base.width / 100, float(point[1]) * base.height / 100)
        for point in points
    ]
    box = _crop_box(full_points, base.width, base.height)
    crop = base.crop(box).convert("RGB")
    scale = min(700 / crop.width, 560 / crop.height)
    rendered_size = (
        max(1, int(round(crop.width * scale))),
        max(1, int(round(crop.height * scale))),
    )
    if rendered_size != crop.size:
        crop = crop.resize(rendered_size, Image.Resampling.LANCZOS)
    scale_x = crop.width / (box[2] - box[0])
    scale_y = crop.height / (box[3] - box[1])
    local_points = [
        (
            int(round((x - box[0]) * scale_x)),
            int(round((y - box[1]) * scale_y)),
        )
        for x, y in full_points
    ]
    draw = ImageDraw.Draw(crop)
    line_width = max(6, int(round(max(crop.width, crop.height) / 250)))
    if len(local_points) >= 2:
        draw.line(local_points, fill="white", width=line_width + 7, joint="curve")
        draw.line(local_points, fill=(224, 31, 38), width=line_width, joint="curve")
        longest_segments = sorted(
            zip(local_points, local_points[1:]),
            key=lambda pair: -math.dist(pair[0], pair[1]),
        )[:3]
        for start, end in longest_segments:
            _draw_arrow(draw, start, end, max(8, line_width + 2))
    marker_radius = max(7, line_width)
    for index in row.metrics.semantic_point_indexes[1:-1]:
        x, y = local_points[index]
        draw.ellipse(
            (x - marker_radius, y - marker_radius, x + marker_radius, y + marker_radius),
            fill=(255, 216, 20),
            outline=(40, 40, 40),
            width=max(2, line_width // 3),
        )
    endpoint_radius = max(10, line_width + 3)
    for point, color in ((local_points[0], (30, 164, 78)), (local_points[-1], (38, 103, 219))):
        x, y = point
        draw.ellipse(
            (x - endpoint_radius, y - endpoint_radius, x + endpoint_radius, y + endpoint_radius),
            fill=color,
            outline="white",
            width=max(3, line_width // 2),
        )
    card = Image.new("RGB", (720, 700), (248, 250, 252))
    card.paste(crop, ((720 - crop.width) // 2, 130 + (560 - crop.height) // 2))
    card_draw = ImageDraw.Draw(card)
    title_font = _font(25, bold=True)
    text_font = _font(17)
    small_font = _font(14)
    card_draw.text(
        (14, 10),
        f"{record.get('floor', '')}  {row.kind}  turns={row.metrics.spoken_turn_count}  points={len(points)}",
        fill=(20, 55, 88),
        font=title_font,
    )
    card_draw.text(
        (14, 46),
        f"alternations={row.metrics.low_angle_alternations}  clearance={record.get('minClearancePx')}  solver={record.get('solverQualityStatus')}",
        fill=(55, 55, 60),
        font=text_font,
    )
    card_draw.text(
        (14, 75),
        f"geometry={row.geometry_sha256[:16]}  keys={'; '.join(route_keys)}",
        fill=(80, 45, 45),
        font=small_font,
    )
    card_draw.text(
        (14, 102),
        "green=start  blue=end  yellow=semantic turn  arrows=direction",
        fill=(65, 75, 80),
        font=small_font,
    )
    card_draw.rectangle((0, 0, 719, 699), outline=(165, 175, 185), width=2)
    return card


def _csv_record(row: RouteRow, route_image: str = "") -> dict[str, object]:
    return {
        "key": row.key,
        "kind": row.kind,
        "floor": row.record.get("floor", ""),
        "pointCount": len(row.record.get("points", [])),
        "effectiveTurns": row.metrics.spoken_turn_count,
        "lowAngleAlternations": row.metrics.low_angle_alternations,
        "minClearancePx": row.record.get("minClearancePx", ""),
        "solverQualityStatus": row.record.get("solverQualityStatus", ""),
        "geometrySha256": row.geometry_sha256,
        "reviewRequired": str(row.review_required).lower(),
        "reviewDecision": row.review_decision,
        "routeImage": route_image,
    }


def _write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    def safe(value: object) -> object:
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value
        return value

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(
            {field: safe(value) for field, value in row.items()} for row in rows
        )


def export_report(report_dir: Path, floor_dir: Path, rows: list[RouteRow]) -> None:
    ordered = sorted(rows, key=_row_sort)
    required = [row for row in ordered if row.review_required]
    high_turn = [row for row in ordered if row.metrics.spoken_turn_count > 5]
    groups: dict[tuple[object, ...], list[RouteRow]] = {}
    for row in required:
        groups.setdefault(_evidence_group_key(row), []).append(row)
    canonical = sorted((items for items in groups.values()), key=lambda items: _row_sort(items[0]))

    # Validate every required floor map before creating any report output.
    map_paths = {
        _evidence_group_key(group[0]): _floor_map_path(floor_dir, group[0].record)
        for group in canonical
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    route_dir = report_dir / "route-images"
    sheet_dir = report_dir / "sheets"
    route_dir.mkdir(exist_ok=True)
    sheet_dir.mkdir(exist_ok=True)
    for directory, pattern in ((route_dir, "route-*.jpg"), (sheet_dir, "sheet-*.jpg")):
        for stale in directory.glob(pattern):
            stale.unlink()

    route_image_by_group: dict[tuple[object, ...], str] = {}
    cards: list[Image.Image] = []
    manifest_routes: list[dict[str, object]] = []
    map_cache: dict[Path, Image.Image] = {}
    for index, group in enumerate(canonical, start=1):
        representative = group[0]
        group_key = _evidence_group_key(representative)
        map_path = map_paths[group_key]
        if map_path not in map_cache:
            try:
                map_cache[map_path] = Image.open(map_path).convert("RGB")
            except (OSError, ValueError) as error:
                raise InputError(f"cannot open floor map {map_path}: {error}") from error
        route_keys = sorted(item.key for item in group)
        card = _route_card(map_cache[map_path], representative, route_keys)
        route_name = f"route-{index:03d}.jpg"
        relative_image = f"route-images/{route_name}"
        card.save(route_dir / route_name, quality=92, subsampling=0)
        cards.append(card)
        route_image_by_group[group_key] = relative_image
        manifest_routes.append(
            {
                "index": index,
                "geometrySha256": representative.geometry_sha256,
                "routeKeys": route_keys,
                "kind": representative.kind,
                "floor": representative.record.get("floor", ""),
                "pointCount": len(representative.record.get("points", [])),
                "effectiveTurns": representative.metrics.spoken_turn_count,
                "lowAngleAlternations": representative.metrics.low_angle_alternations,
                "minClearancePx": representative.record.get("minClearancePx"),
                "solverQualityStatus": representative.record.get("solverQualityStatus"),
                "reviewDecision": representative.review_decision,
                "routeImage": relative_image,
                "sheet": (index - 1) // 16 + 1,
                "cell": (index - 1) % 16 + 1,
            }
        )

    sheet_width, sheet_height = 4 * 720 + 5 * 18, 4 * 700 + 5 * 18
    for page_start in range(0, len(cards), 16):
        sheet = Image.new("RGB", (sheet_width, sheet_height), (235, 239, 243))
        for offset, card in enumerate(cards[page_start : page_start + 16]):
            column, row_number = offset % 4, offset // 4
            sheet.paste(card, (18 + column * 738, 18 + row_number * 718))
        sheet.save(
            sheet_dir / f"sheet-{page_start // 16 + 1:02d}.jpg",
            quality=90,
            subsampling=0,
        )

    all_csv = [
        _csv_record(row, route_image_by_group.get(_evidence_group_key(row), ""))
        for row in ordered
    ]
    high_turn_csv = [
        _csv_record(row, route_image_by_group.get(_evidence_group_key(row), ""))
        for row in high_turn
    ]
    _write_csv(report_dir / "all-routes.csv", all_csv)
    _write_csv(report_dir / "high-turn-routes.csv", high_turn_csv)
    summary = {
        "schemaVersion": 1,
        "routeCount": len(ordered),
        "highTurnRouteCount": len(high_turn),
        "reviewRequiredRouteCount": len(required),
        "canonicalGeometryCount": len(canonical),
        "sheetCount": math.ceil(len(cards) / 16),
        "thresholds": {
            "spokenTurnDegrees": SPOKEN_TURN_DEGREES,
            "microZigzagBendDegrees": LOW_ANGLE_DEGREES,
            "microZigzagAlternations": MICRO_ZIGZAG_ALTERNATIONS,
            "reviewTurnCountAbove": 5,
        },
    }
    (report_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (report_dir / "manifest.json").write_text(
        json.dumps(
            {"schemaVersion": 1, "routes": manifest_routes},
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def build_parser(project_root: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--same-floor-paths",
        type=Path,
        default=project_root / "miniprogram" / "data" / "sameFloorPaths.js",
    )
    parser.add_argument(
        "--floor-nav-paths",
        type=Path,
        default=project_root / "miniprogram" / "data" / "floorNavPaths.js",
    )
    parser.add_argument(
        "--review-decisions",
        type=Path,
        default=project_root / "config" / "route-turn-reviews.json",
    )
    parser.add_argument(
        "--floor-dir",
        type=Path,
        default=project_root / "miniprogram" / "assets" / "floor-maps",
    )
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument(
        "--expected-review-status",
        choices=("pending", "approved"),
        default="pending",
        help="required file-level route provenance state",
    )
    return parser


def _is_within(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def _validate_report_path_safety(
    report_dir: Path,
    floor_dir: Path,
    input_paths: Sequence[Path],
) -> None:
    for input_path in input_paths:
        if _is_within(input_path, report_dir):
            raise InputError(
                f"report directory overlaps route/review input: {input_path}"
            )
    if _is_within(floor_dir, report_dir) or _is_within(report_dir, floor_dir):
        raise InputError(f"report directory overlaps floor map context: {floor_dir}")


def main(argv: Sequence[str] | None = None) -> int:
    project_root = Path(__file__).resolve().parent.parent
    args = build_parser(project_root).parse_args(argv)
    try:
        same_floor_path = args.same_floor_paths.resolve()
        floor_nav_path = args.floor_nav_paths.resolve()
        review_path = args.review_decisions.resolve()
        floor_dir = args.floor_dir.resolve()
        report_dir = args.report_dir.resolve() if args.report_dir is not None else None
        if report_dir is not None:
            _validate_report_path_safety(
                report_dir,
                floor_dir,
                (same_floor_path, floor_nav_path, review_path),
            )
        reviews = load_review_decisions(review_path)
        policy_expectations = _load_policy_expectations(project_root)
        records_by_kind = {
            "floorNav": load_route_file(
                floor_nav_path,
                policy_expectations,
                args.expected_review_status,
            ),
            "sameFloor": load_route_file(
                same_floor_path,
                policy_expectations,
                args.expected_review_status,
            ),
        }
        rows, failures = validate_routes(
            records_by_kind,
            reviews,
        )
        if report_dir is not None:
            export_report(report_dir, floor_dir, rows)
        if failures:
            for failure in failures:
                print(f"ERROR {failure}")
            print(f"route-turn quality failed: failures={len(failures)} routes={len(rows)}")
            return 1
        required = sum(row.review_required for row in rows)
        print(
            f"route-turn quality passed: routes={len(rows)} "
            f"reviewRequired={required} reports={'written' if args.report_dir else 'disabled'}"
        )
        return 0
    except InputError as error:
        print(f"route-turn quality input error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
