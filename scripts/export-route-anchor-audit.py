"""Inspect route-anchor coverage; write reports only with --report-dir."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
from pathlib import Path

from audit_common import (
    PROJECT_ROOT,
    create_report_dir,
    department_endpoint_residual,
    department_semantic_endpoint,
    input_metadata,
    load_commonjs_json,
    load_json,
    max_anchor_snap_px_for_floor,
    point_valid,
    resolve_path,
    write_csv,
    write_json,
    write_metadata,
)


def audit(project_root: Path) -> tuple[dict[str, object], list[Path]]:
    inputs = [
        project_root / "config" / "department-anchors.json",
        project_root / "config" / "routing-policy.json",
        project_root / "miniprogram" / "data" / "floorNavPaths.js",
        project_root / "miniprogram" / "data" / "sameFloorPaths.js",
    ]
    anchors = load_json(inputs[0])
    routing_policy = load_json(inputs[1])
    floor_nav = load_commonjs_json(inputs[2])
    same_floor = load_commonjs_json(inputs[3])
    rows: list[dict[str, object]] = []
    issues: list[str] = []
    for anchor in anchors:
        name = anchor.get("name")
        floor = anchor.get("floor")
        point = anchor.get("anchor")
        to_paths = [
            item for key, item in floor_nav.items()
            if key.startswith(f"{name}|||") and key.endswith("|||toElevator")
        ]
        from_paths = [
            item for key, item in floor_nav.items()
            if key.startswith(f"{name}|||") and key.endswith("|||fromElevator")
        ]
        same_paths = [
            item for key, item in same_floor.items()
            if key.startswith(f"{name}|||") or key.endswith(f"|||{name}")
        ]
        max_anchor_snap_px: int | None = None
        if not name:
            issues.append(f"invalid department anchor: {name!r}")
        try:
            department_semantic_endpoint(anchor)
            max_anchor_snap_px = max_anchor_snap_px_for_floor(routing_policy, floor)
        except (TypeError, ValueError) as error:
            issues.append(f"invalid department endpoint contract for {name!r}: {error}")
        for item in to_paths:
            try:
                residual, tolerance, endpoint_type = department_endpoint_residual(
                    item.get("points", [])[0],
                    anchor,
                    item.get("imageSize"),
                    max_anchor_snap_px,
                )
                if residual > tolerance:
                    issues.append(
                        f"toElevator {endpoint_type} residual {residual:.3f} px "
                        f"exceeds {tolerance} px: {name}"
                    )
            except (IndexError, TypeError, ValueError) as error:
                issues.append(f"invalid toElevator endpoint for {name}: {error}")
        for item in from_paths:
            try:
                residual, tolerance, endpoint_type = department_endpoint_residual(
                    item.get("points", [])[-1],
                    anchor,
                    item.get("imageSize"),
                    max_anchor_snap_px,
                )
                if residual > tolerance:
                    issues.append(
                        f"fromElevator {endpoint_type} residual {residual:.3f} px "
                        f"exceeds {tolerance} px: {name}"
                    )
            except (IndexError, TypeError, ValueError) as error:
                issues.append(f"invalid fromElevator endpoint for {name}: {error}")
        rows.append(
            {
                "department": name,
                "floor": floor,
                "anchorX": point[0] if point_valid(point) else "",
                "anchorY": point[1] if point_valid(point) else "",
                "toElevatorPaths": len(to_paths),
                "fromElevatorPaths": len(from_paths),
                "sameFloorReferences": len(same_paths),
            }
        )
    if len(anchors) != 42:
        issues.append(f"expected 42 anchors, got {len(anchors)}")
    return {"anchorCount": len(anchors), "rows": rows, "issues": issues}, inputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument("--report-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        project_root = resolve_path(args.project_dir, PROJECT_ROOT)
        if args.report_dir is not None and args.report_dir.resolve().exists():
            raise FileExistsError(f"report directory already exists: {args.report_dir.resolve()}")
        summary, inputs = audit(project_root)
        print("Route anchor audit")
        print(f'- anchors: {summary["anchorCount"]}')
        print(f'- issues: {len(summary["issues"])}')
        if args.report_dir is not None:
            report_dir = create_report_dir(args.report_dir)
            write_csv(
                report_dir / "route-anchor-audit.csv", summary["rows"],
                [
                    "department", "floor", "anchorX", "anchorY",
                    "toElevatorPaths", "fromElevatorPaths", "sameFloorReferences",
                ],
            )
            write_json(
                report_dir / "route-anchor-summary.json",
                {"anchorCount": summary["anchorCount"], "issues": summary["issues"]},
            )
            write_metadata(
                report_dir, Path(__file__).name,
                input_metadata(inputs, project_root),
            )
            print(f"- report: {report_dir}")
        for issue in summary["issues"]:
            print(f"ERROR: {issue}", file=sys.stderr)
        return 1 if summary["issues"] else 0
    except Exception as error:  # CLI boundary
        print(f"export-route-anchor-audit failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
