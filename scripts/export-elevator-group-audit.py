"""Inspect elevator groups; export data only with explicit --data-output."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
from pathlib import Path

from audit_common import (
    PROJECT_ROOT,
    create_report_dir,
    input_metadata,
    load_commonjs_json,
    load_json,
    point_valid,
    resolve_path,
    write_csv,
    write_json,
    write_metadata,
)


def audit(project_root: Path) -> tuple[dict[str, object], dict[str, object], list[Path]]:
    inputs = [
        project_root / "config" / "elevator-shafts.json",
        project_root / "miniprogram" / "data" / "elevatorGroups.js",
        project_root / "miniprogram" / "data" / "elevatorShafts.js",
    ]
    configured_shafts = load_json(inputs[0])
    groups = load_commonjs_json(inputs[1])
    runtime_shafts = load_commonjs_json(inputs[2])
    rows: list[dict[str, object]] = []
    issues: list[str] = []
    mapping_count = 0
    config_by_id = {shaft["shaftId"]: shaft for shaft in configured_shafts}
    for shaft in runtime_shafts:
        shaft_id = shaft.get("shaftId")
        configured = config_by_id.get(shaft_id)
        if not configured:
            issues.append(f"runtime shaft missing from config: {shaft_id}")
            continue
        for floor, mapping in (shaft.get("floorMappings") or {}).items():
            mapping_count += 1
            group_id = mapping.get("elevatorGroupId")
            floor_groups = groups.get(floor) or []
            group_exists = any(group.get("id") == group_id for group in floor_groups)
            config_mapping = (configured.get("floorMappings") or {}).get(floor)
            if not group_exists:
                issues.append(f"missing group {group_id} on {floor} for {shaft_id}")
            if not config_mapping or config_mapping.get("elevatorGroupId") != group_id:
                issues.append(f"config/runtime mapping mismatch: {shaft_id} {floor}")
            if not point_valid(mapping.get("elevatorAnchor")):
                issues.append(f"invalid navigable anchor: {shaft_id} {floor}")
            rows.append(
                {
                    "shaftId": shaft_id,
                    "floor": floor,
                    "elevatorGroupId": group_id,
                    "confirmed": bool(config_mapping and config_mapping.get("confirmed") is True),
                    "groupExists": group_exists,
                    "anchorX": (mapping.get("elevatorAnchor") or ["", ""])[0],
                    "anchorY": (mapping.get("elevatorAnchor") or ["", ""])[1],
                }
            )
    if len(runtime_shafts) != 7:
        issues.append(f"expected 7 shafts, got {len(runtime_shafts)}")
    if mapping_count != 53:
        issues.append(f"expected 53 mappings, got {mapping_count}")
    return (
        {"shaftCount": len(runtime_shafts), "mappingCount": mapping_count, "rows": rows, "issues": issues},
        groups,
        inputs,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--data-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        project_root = resolve_path(args.project_dir, PROJECT_ROOT)
        report_path = args.report_dir.resolve() if args.report_dir is not None else None
        data_path = args.data_output.resolve() if args.data_output is not None else None
        if report_path is not None and report_path.exists():
            raise FileExistsError(f"report directory already exists: {report_path}")
        if data_path is not None and data_path.exists():
            raise FileExistsError(f"data output already exists: {data_path}")
        summary, groups, inputs = audit(project_root)
        print("Elevator group audit")
        print(f'- shafts: {summary["shaftCount"]}')
        print(f'- mappings: {summary["mappingCount"]}')
        print(f'- issues: {len(summary["issues"])}')
        if summary["issues"]:
            for issue in summary["issues"]:
                print(f"ERROR: {issue}", file=sys.stderr)
            return 1
        if report_path is not None:
            report_dir = create_report_dir(report_path)
            write_csv(
                report_dir / "elevator-group-audit.csv", summary["rows"],
                [
                    "shaftId", "floor", "elevatorGroupId", "confirmed",
                    "groupExists", "anchorX", "anchorY",
                ],
            )
            write_json(
                report_dir / "elevator-group-summary.json",
                {"shaftCount": summary["shaftCount"], "mappingCount": summary["mappingCount"], "issues": []},
            )
            write_metadata(
                report_dir, Path(__file__).name,
                input_metadata(inputs, project_root),
            )
            print(f"- report: {report_dir}")
        if data_path is not None:
            data_path.parent.mkdir(parents=True, exist_ok=True)
            data_path.write_text(
                "module.exports = " + json.dumps(groups, ensure_ascii=False, indent=2) + ";\n",
                encoding="utf-8",
            )
            print(f"- data output: {data_path}")
        return 0
    except Exception as error:  # CLI boundary
        print(f"export-elevator-group-audit failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
