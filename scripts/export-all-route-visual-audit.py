#!/usr/bin/env python3
"""Export one visual card per unique route geometry and one checklist row per route."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont


CARD_WIDTH = 960
CARD_HEIGHT = 760
MAP_TOP = 142
MAP_HEIGHT = 598
SHEET_COLUMNS = 4
SHEET_ROWS = 4
SHEET_GAP = 14
ROUTE_FIELDS = (
    "routeId",
    "geometryId",
    "kind",
    "routeKey",
    "floor",
    "image",
    "card",
    "sheet",
    "cell",
    "changed",
    "pointsBefore",
    "pointsAfter",
    "turnsBefore",
    "turnsAfter",
    "routeLengthBefore",
    "routeLengthAfter",
    "clearanceBefore",
    "clearanceAfter",
    "redundantCollinearPointsAfter",
    "lowAngleAlternationsAfter",
    "visualReviewStatus",
    "wallCrossing",
    "unnecessaryBend",
    "endpointIssue",
    "reviewNotes",
)
CHANGE_FIELDS = (
    "kind",
    "routeKey",
    "floor",
    "geometryId",
    "pointsBefore",
    "pointsAfter",
    "pointsRemoved",
    "turnsBefore",
    "turnsAfter",
    "turnsRemoved",
    "routeLengthBefore",
    "routeLengthAfter",
    "clearanceBefore",
    "clearanceAfter",
)


def strict_json(text: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(
        text,
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )


def load_commonjs(path: Path) -> dict[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig").strip()
    marker = "module.exports ="
    if marker not in text:
        raise ValueError(f"{path}: missing CommonJS export")
    body = text.split(marker, 1)[1].strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    value = strict_json(body)
    if not isinstance(value, dict):
        raise TypeError(f"{path}: route export must be an object")
    if any(not isinstance(key, str) or not isinstance(item, dict) for key, item in value.items()):
        raise TypeError(f"{path}: route keys must map to objects")
    return value


def safe_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def safe_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_points(value: object, *, label: str) -> list[list[float]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label}: points must be a non-empty list")
    points: list[list[float]] = []
    for index, point in enumerate(value):
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"{label}: points[{index}] must contain two numbers")
        normalized = [safe_float(point[0], float("nan")), safe_float(point[1], float("nan"))]
        if any(not math.isfinite(number) or not 0 <= number <= 100 for number in normalized):
            raise ValueError(f"{label}: points[{index}] is outside 0..100")
        if not points or normalized != points[-1]:
            points.append(normalized)
    return points


def canonical_geometry_hash(points: Sequence[Sequence[float]]) -> str:
    forward = json.dumps(points, ensure_ascii=False, separators=(",", ":"))
    reverse = json.dumps(list(reversed(points)), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(min(forward, reverse).encode("utf-8")).hexdigest()


def floor_number(value: object) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else 999


def image_size(record: dict[str, Any], image: Image.Image) -> tuple[float, float]:
    value = record.get("imageSize")
    if (
        isinstance(value, list)
        and len(value) == 2
        and safe_float(value[0]) > 0
        and safe_float(value[1]) > 0
    ):
        return safe_float(value[0]), safe_float(value[1])
    return float(image.width), float(image.height)


def geometry_angles(
    points: Sequence[Sequence[float]],
    size: tuple[float, float],
) -> list[float]:
    width, height = size
    pixels = [(float(point[0]) * width / 100, float(point[1]) * height / 100) for point in points]
    angles: list[float] = []
    for left, middle, right in zip(pixels, pixels[1:], pixels[2:]):
        ax, ay = middle[0] - left[0], middle[1] - left[1]
        bx, by = right[0] - middle[0], right[1] - middle[1]
        if (ax == 0 and ay == 0) or (bx == 0 and by == 0):
            continue
        angles.append(math.degrees(math.atan2(abs(ax * by - ay * bx), ax * bx + ay * by)))
    return angles


def geometry_flags(
    points: Sequence[Sequence[float]],
    size: tuple[float, float],
) -> tuple[int, int, int]:
    angles = geometry_angles(points, size)
    redundant = sum(angle < 1e-9 for angle in angles)
    low_sign: int | None = None
    alternations = 0
    maximum_alternations = 0
    pixels = [
        (float(point[0]) * size[0] / 100, float(point[1]) * size[1] / 100)
        for point in points
    ]
    for left, middle, right in zip(pixels, pixels[1:], pixels[2:]):
        ax, ay = middle[0] - left[0], middle[1] - left[1]
        bx, by = right[0] - middle[0], right[1] - middle[1]
        if (ax == 0 and ay == 0) or (bx == 0 and by == 0):
            continue
        signed = math.degrees(math.atan2(ax * by - ay * bx, ax * bx + ay * by))
        absolute = abs(signed)
        if absolute >= 25:
            low_sign = None
            alternations = 0
        elif absolute >= 5:
            sign = 1 if signed > 0 else -1
            if low_sign is not None and sign != low_sign:
                alternations += 1
            low_sign = sign
            maximum_alternations = max(maximum_alternations, alternations)
    u_turns = sum(angle >= 145 for angle in angles)
    return redundant, maximum_alternations, u_turns


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def pixel_points(points: Sequence[Sequence[float]], image: Image.Image) -> list[tuple[float, float]]:
    return [
        (float(point[0]) * image.width / 100, float(point[1]) * image.height / 100)
        for point in points
    ]


def crop_box(
    groups: Iterable[Sequence[tuple[float, float]]],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    all_points = [point for group in groups for point in group]
    xs = [point[0] for point in all_points]
    ys = [point[1] for point in all_points]
    route_width = max(max(xs) - min(xs), width * 0.10)
    route_height = max(max(ys) - min(ys), height * 0.10)
    crop_width = min(width, max(route_width * 1.45, width * 0.25))
    crop_height = min(height, max(route_height * 1.45, height * 0.25))
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


def localize(
    points: Sequence[tuple[float, float]],
    box: tuple[int, int, int, int],
    crop: Image.Image,
) -> list[tuple[int, int]]:
    scale_x = crop.width / max(1, box[2] - box[0])
    scale_y = crop.height / max(1, box[3] - box[1])
    return [
        (
            int(round((point[0] - box[0]) * scale_x)),
            int(round((point[1] - box[1]) * scale_y)),
        )
        for point in points
    ]


def draw_route(
    draw: ImageDraw.ImageDraw,
    points: Sequence[tuple[int, int]],
    *,
    color: tuple[int, int, int],
    width: int,
    halo: tuple[int, int, int] | None = None,
) -> None:
    if len(points) < 2:
        return
    if halo is not None:
        draw.line(points, fill=halo, width=width + 7, joint="curve")
    draw.line(points, fill=color, width=width, joint="curve")


def draw_arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    color: tuple[int, int, int],
    size: int,
) -> None:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < size * 4:
        return
    ux, uy = dx / length, dy / length
    center = (start[0] + dx * 0.62, start[1] + dy * 0.62)
    tip = (center[0] + ux * size, center[1] + uy * size)
    back = (center[0] - ux * size, center[1] - uy * size)
    side = (-uy * size * 0.65, ux * size * 0.65)
    draw.polygon(
        [
            tip,
            (back[0] + side[0], back[1] + side[1]),
            (back[0] - side[0], back[1] - side[1]),
        ],
        fill=color,
        outline=(255, 255, 255),
    )


def route_card(
    base: Image.Image,
    *,
    geometry_id: str,
    kind: str,
    floor: str,
    route_count: int,
    before: Sequence[Sequence[float]],
    after: Sequence[Sequence[float]],
    changed: bool,
    points_before: int,
    points_after: int,
    turns_before: int,
    turns_after: int,
    redundant_after: int,
    alternations_after: int,
    u_turns_after: int,
) -> Image.Image:
    before_full = pixel_points(before, base)
    after_full = pixel_points(after, base)
    box = crop_box((before_full, after_full), base.width, base.height)
    crop = base.crop(box).convert("RGB")
    scale = min(920 / crop.width, MAP_HEIGHT / crop.height)
    rendered = (
        max(1, int(round(crop.width * scale))),
        max(1, int(round(crop.height * scale))),
    )
    if rendered != crop.size:
        crop = crop.resize(rendered, Image.Resampling.LANCZOS)
    before_local = localize(before_full, box, crop)
    after_local = localize(after_full, box, crop)
    draw = ImageDraw.Draw(crop)
    line_width = max(5, int(round(max(crop.width, crop.height) / 190)))
    if changed:
        draw_route(
            draw,
            before_local,
            color=(221, 48, 48),
            width=line_width + 1,
            halo=(255, 255, 255),
        )
    draw_route(
        draw,
        after_local,
        color=(0, 201, 146),
        width=line_width,
        halo=(20, 36, 48),
    )
    for start, end in zip(after_local, after_local[1:]):
        draw_arrow(draw, start, end, color=(0, 120, 88), size=max(8, line_width + 2))
    radius = max(9, line_width + 3)
    if after_local:
        for point, color in (
            (after_local[0], (35, 110, 230)),
            (after_local[-1], (255, 190, 0)),
        ):
            x, y = point
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=color,
                outline=(255, 255, 255),
                width=max(3, line_width // 2),
            )

    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), (244, 247, 250))
    card.paste(crop, ((CARD_WIDTH - crop.width) // 2, MAP_TOP + (MAP_HEIGHT - crop.height) // 2))
    header = ImageDraw.Draw(card)
    title = font(27, True)
    detail = font(18)
    small = font(15)
    floor_text = f"{floor_number(floor)}F" if floor_number(floor) != 999 else "?F"
    header.text(
        (18, 10),
        f"{geometry_id}  {floor_text}  {kind}  routes={route_count}",
        fill=(20, 45, 72),
        font=title,
    )
    header.text(
        (18, 50),
        f"points {points_before}->{points_after}  turns {turns_before}->{turns_after}  changed={str(changed).lower()}",
        fill=(45, 55, 65),
        font=detail,
    )
    header.text(
        (18, 82),
        f"post flags: collinear={redundant_after}  low-angle-alt={alternations_after}  u-turns={u_turns_after}",
        fill=(45, 55, 65),
        font=detail,
    )
    header.text(
        (18, 112),
        "red=before  green=after/current  blue=start  yellow=end",
        fill=(80, 80, 85),
        font=small,
    )
    header.rectangle((0, 0, CARD_WIDTH - 1, CARD_HEIGHT - 1), outline=(150, 160, 170), width=2)
    return card


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-same-floor", type=Path, required=True)
    parser.add_argument("--baseline-floor-nav", type=Path, required=True)
    parser.add_argument(
        "--current-same-floor",
        type=Path,
        default=root / "miniprogram" / "data" / "sameFloorPaths.js",
    )
    parser.add_argument(
        "--current-floor-nav",
        type=Path,
        default=root / "miniprogram" / "data" / "floorNavPaths.js",
    )
    parser.add_argument(
        "--floor-dir",
        type=Path,
        default=root / "miniprogram" / "assets" / "floor-maps",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output_dir.resolve()
    card_dir = output / "all-route-cards"
    sheet_dir = output / "all-route-sheets"
    output.mkdir(parents=True, exist_ok=True)
    card_dir.mkdir(exist_ok=True)
    sheet_dir.mkdir(exist_ok=True)
    for directory, pattern in ((card_dir, "route-*.jpg"), (sheet_dir, "sheet-*.jpg")):
        for stale in directory.glob(pattern):
            stale.unlink()

    sources = (
        (
            "sameFloor",
            load_commonjs(args.baseline_same_floor.resolve()),
            load_commonjs(args.current_same_floor.resolve()),
        ),
        (
            "floorNav",
            load_commonjs(args.baseline_floor_nav.resolve()),
            load_commonjs(args.current_floor_nav.resolve()),
        ),
    )
    route_records: list[dict[str, object]] = []
    groups: dict[tuple[str, str, str, str], list[dict[str, object]]] = {}
    failures: list[str] = []
    totals = {
        "routeCount": 0,
        "changedRouteCount": 0,
        "reducedPointRouteCount": 0,
        "pointIncreaseRouteCount": 0,
        "turnReductionRouteCount": 0,
        "turnIncreaseRouteCount": 0,
        "lengthIncreaseRouteCount": 0,
        "clearanceDecreaseRouteCount": 0,
        "totalPointsBefore": 0,
        "totalPointsAfter": 0,
        "totalTurnsBefore": 0,
        "totalTurnsAfter": 0,
    }

    for kind, baseline, current in sources:
        if set(baseline) != set(current):
            failures.append(
                f"{kind}: route key drift missing={sorted(set(baseline) - set(current))} "
                f"extra={sorted(set(current) - set(baseline))}"
            )
            continue
        for key in sorted(current):
            before_record = baseline[key]
            after_record = current[key]
            before = normalize_points(before_record.get("points"), label=f"{kind}:{key}:before")
            after = normalize_points(after_record.get("points"), label=f"{kind}:{key}:after")
            floor = str(after_record.get("floor") or before_record.get("floor") or "")
            image = str(after_record.get("image") or before_record.get("image") or "")
            geometry_hash = canonical_geometry_hash(after)
            group_key = (kind, floor, image, geometry_hash)
            changed = before != after
            before_turns = safe_int(before_record.get("effectiveTurnCount"))
            after_turns = safe_int(after_record.get("effectiveTurnCount"))
            before_length = safe_float(before_record.get("routeLength"))
            after_length = safe_float(after_record.get("routeLength"))
            before_clearance = safe_float(before_record.get("minClearancePx"))
            after_clearance = safe_float(after_record.get("minClearancePx"))
            totals["routeCount"] += 1
            totals["changedRouteCount"] += int(changed)
            totals["reducedPointRouteCount"] += int(len(after) < len(before))
            totals["pointIncreaseRouteCount"] += int(len(after) > len(before))
            totals["turnReductionRouteCount"] += int(after_turns < before_turns)
            totals["turnIncreaseRouteCount"] += int(after_turns > before_turns)
            totals["lengthIncreaseRouteCount"] += int(after_length > before_length + 1e-6)
            totals["clearanceDecreaseRouteCount"] += int(
                before_clearance > 0 and after_clearance + 1e-3 < before_clearance
            )
            totals["totalPointsBefore"] += len(before)
            totals["totalPointsAfter"] += len(after)
            totals["totalTurnsBefore"] += before_turns
            totals["totalTurnsAfter"] += after_turns
            record = {
                "kind": kind,
                "routeKey": key,
                "floor": floor,
                "image": image,
                "before": before,
                "after": after,
                "changed": changed,
                "pointsBefore": len(before),
                "pointsAfter": len(after),
                "turnsBefore": before_turns,
                "turnsAfter": after_turns,
                "routeLengthBefore": before_length,
                "routeLengthAfter": after_length,
                "clearanceBefore": before_clearance,
                "clearanceAfter": after_clearance,
                "record": after_record,
                "groupKey": group_key,
            }
            route_records.append(record)
            groups.setdefault(group_key, []).append(record)

    for field in (
        "pointIncreaseRouteCount",
        "turnIncreaseRouteCount",
        "lengthIncreaseRouteCount",
        "clearanceDecreaseRouteCount",
    ):
        if totals[field]:
            failures.append(f"unexpected regression {field}={totals[field]}")
    if totals["changedRouteCount"] == 0:
        failures.append("simplification changed no route geometry")
    if totals["changedRouteCount"] != totals["reducedPointRouteCount"]:
        failures.append(
            "every changed route must remove points: "
            f"changed={totals['changedRouteCount']} reduced={totals['reducedPointRouteCount']}"
        )

    ordered_groups = sorted(
        groups.items(),
        key=lambda item: (
            floor_number(item[0][1]),
            item[0][0],
            min(str(record["routeKey"]) for record in item[1]),
            item[0][3],
        ),
    )
    map_cache: dict[Path, Image.Image] = {}
    cards: list[Image.Image] = []
    group_meta: dict[tuple[str, str, str, str], dict[str, object]] = {}
    geometry_manifest: list[dict[str, object]] = []
    total_redundant = 0
    total_low_alternations = 0
    total_u_turns = 0

    for index, (group_key, members) in enumerate(ordered_groups, start=1):
        representative = members[0]
        geometry_id = f"G{index:04d}"
        image_name = Path(str(representative["image"])).name
        map_path = args.floor_dir.resolve() / image_name
        if not map_path.is_file():
            failures.append(f"{geometry_id}: missing floor map {map_path}")
            continue
        if map_path not in map_cache:
            map_cache[map_path] = Image.open(map_path).convert("RGB")
        base = map_cache[map_path]
        size = image_size(representative["record"], base)
        redundant, alternations, u_turns = geometry_flags(representative["after"], size)
        total_redundant += redundant
        total_low_alternations += int(alternations >= 5)
        total_u_turns += u_turns
        if redundant:
            failures.append(f"{geometry_id}: {redundant} exactly collinear intermediate point(s) remain")
        if alternations >= 5:
            failures.append(f"{geometry_id}: micro-zigzag alternations={alternations}")
        changed = any(bool(member["changed"]) for member in members)
        card = route_card(
            base,
            geometry_id=geometry_id,
            kind=str(representative["kind"]),
            floor=str(representative["floor"]),
            route_count=len(members),
            before=representative["before"],
            after=representative["after"],
            changed=changed,
            points_before=int(representative["pointsBefore"]),
            points_after=int(representative["pointsAfter"]),
            turns_before=int(representative["turnsBefore"]),
            turns_after=int(representative["turnsAfter"]),
            redundant_after=redundant,
            alternations_after=alternations,
            u_turns_after=u_turns,
        )
        card_name = f"route-{index:04d}.jpg"
        card.save(card_dir / card_name, quality=92, subsampling=0)
        cards.append(card)
        sheet_number = (index - 1) // (SHEET_COLUMNS * SHEET_ROWS) + 1
        cell_number = (index - 1) % (SHEET_COLUMNS * SHEET_ROWS) + 1
        meta = {
            "geometryId": geometry_id,
            "card": f"all-route-cards/{card_name}",
            "sheet": f"all-route-sheets/sheet-{sheet_number:03d}.jpg",
            "cell": cell_number,
            "redundantCollinearPointsAfter": redundant,
            "lowAngleAlternationsAfter": alternations,
            "uTurnsAfter": u_turns,
        }
        group_meta[group_key] = meta
        geometry_manifest.append(
            {
                **meta,
                "kind": representative["kind"],
                "floor": representative["floor"],
                "image": representative["image"],
                "geometrySha256": group_key[3],
                "routeKeys": sorted(str(member["routeKey"]) for member in members),
                "routeCount": len(members),
                "changed": changed,
                "pointsBefore": representative["pointsBefore"],
                "pointsAfter": representative["pointsAfter"],
                "turnsBefore": representative["turnsBefore"],
                "turnsAfter": representative["turnsAfter"],
            }
        )

    sheet_width = SHEET_COLUMNS * CARD_WIDTH + (SHEET_COLUMNS + 1) * SHEET_GAP
    sheet_height = SHEET_ROWS * CARD_HEIGHT + (SHEET_ROWS + 1) * SHEET_GAP
    per_sheet = SHEET_COLUMNS * SHEET_ROWS
    for start in range(0, len(cards), per_sheet):
        sheet = Image.new("RGB", (sheet_width, sheet_height), (228, 233, 238))
        for offset, card in enumerate(cards[start : start + per_sheet]):
            column = offset % SHEET_COLUMNS
            row = offset // SHEET_COLUMNS
            sheet.paste(
                card,
                (
                    SHEET_GAP + column * (CARD_WIDTH + SHEET_GAP),
                    SHEET_GAP + row * (CARD_HEIGHT + SHEET_GAP),
                ),
            )
        sheet.save(
            sheet_dir / f"sheet-{start // per_sheet + 1:03d}.jpg",
            quality=90,
            subsampling=0,
        )

    checklist: list[dict[str, object]] = []
    change_rows: list[dict[str, object]] = []
    for index, record in enumerate(
        sorted(
            route_records,
            key=lambda item: (floor_number(item["floor"]), str(item["kind"]), str(item["routeKey"])),
        ),
        start=1,
    ):
        meta = group_meta.get(record["groupKey"])
        if meta is None:
            failures.append(f"route {record['kind']}:{record['routeKey']} has no visual card")
            continue
        checklist.append(
            {
                "routeId": f"R{index:05d}",
                "geometryId": meta["geometryId"],
                "kind": record["kind"],
                "routeKey": record["routeKey"],
                "floor": record["floor"],
                "image": record["image"],
                "card": meta["card"],
                "sheet": meta["sheet"],
                "cell": meta["cell"],
                "changed": str(bool(record["changed"])).lower(),
                "pointsBefore": record["pointsBefore"],
                "pointsAfter": record["pointsAfter"],
                "turnsBefore": record["turnsBefore"],
                "turnsAfter": record["turnsAfter"],
                "routeLengthBefore": record["routeLengthBefore"],
                "routeLengthAfter": record["routeLengthAfter"],
                "clearanceBefore": record["clearanceBefore"],
                "clearanceAfter": record["clearanceAfter"],
                "redundantCollinearPointsAfter": meta["redundantCollinearPointsAfter"],
                "lowAngleAlternationsAfter": meta["lowAngleAlternationsAfter"],
                "visualReviewStatus": "pending",
                "wallCrossing": "",
                "unnecessaryBend": "",
                "endpointIssue": "",
                "reviewNotes": "",
            }
        )
        if record["changed"]:
            change_rows.append(
                {
                    "kind": record["kind"],
                    "routeKey": record["routeKey"],
                    "floor": record["floor"],
                    "geometryId": meta["geometryId"],
                    "pointsBefore": record["pointsBefore"],
                    "pointsAfter": record["pointsAfter"],
                    "pointsRemoved": int(record["pointsBefore"]) - int(record["pointsAfter"]),
                    "turnsBefore": record["turnsBefore"],
                    "turnsAfter": record["turnsAfter"],
                    "turnsRemoved": int(record["turnsBefore"]) - int(record["turnsAfter"]),
                    "routeLengthBefore": record["routeLengthBefore"],
                    "routeLengthAfter": record["routeLengthAfter"],
                    "clearanceBefore": record["clearanceBefore"],
                    "clearanceAfter": record["clearanceAfter"],
                }
            )

    write_csv(output / "route-review-checklist.csv", ROUTE_FIELDS, checklist)
    write_csv(output / "route-changes.csv", CHANGE_FIELDS, change_rows)
    (output / "geometry-manifest.json").write_text(
        json.dumps(
            {"schemaVersion": 1, "geometries": geometry_manifest},
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    summary = {
        "schemaVersion": 1,
        **totals,
        "uniqueGeometryCount": len(geometry_manifest),
        "changedUniqueGeometryCount": sum(bool(item["changed"]) for item in geometry_manifest),
        "visualCardCount": len(cards),
        "visualSheetCount": math.ceil(len(cards) / per_sheet),
        "checklistRouteCount": len(checklist),
        "allRoutesAssignedToVisualCard": len(checklist) == totals["routeCount"],
        "remainingExactlyCollinearPointCount": total_redundant,
        "remainingMicroZigzagGeometryCount": total_low_alternations,
        "remainingUTurnCount": total_u_turns,
        "pointsRemoved": totals["totalPointsBefore"] - totals["totalPointsAfter"],
        "turnsRemoved": totals["totalTurnsBefore"] - totals["totalTurnsAfter"],
        "pointReductionPercent": round(
            (totals["totalPointsBefore"] - totals["totalPointsAfter"])
            / totals["totalPointsBefore"]
            * 100,
            3,
        )
        if totals["totalPointsBefore"]
        else 0.0,
        "automaticAuditFailures": sorted(set(failures)),
        "manualVisualReviewStatus": "pending",
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failures:
        for failure in sorted(set(failures)):
            print(f"ERROR {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
