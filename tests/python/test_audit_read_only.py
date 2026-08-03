from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = PROJECT_ROOT.parent
AUDIT_SCRIPTS = (
    "audit-route-connectivity.py",
    "export-route-anchor-audit.py",
    "export-elevator-group-audit.py",
)
IGNORED_PARTS = {".git", ".venv", "node_modules", ".superpowers"}


def snapshot_project() -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(PROJECT_ROOT.rglob("*")):
        relative = path.relative_to(PROJECT_ROOT)
        if not path.is_file() or any(part in IGNORED_PARTS for part in relative.parts):
            continue
        snapshot[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def git_status() -> str:
    return subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "status", "--short"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout


@pytest.mark.parametrize("script_name", AUDIT_SCRIPTS)
def test_default_audit_is_absolutely_read_only(script_name: str) -> None:
    before = snapshot_project()
    status_before = git_status()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / script_name)],
        cwd=PROJECT_ROOT.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert snapshot_project() == before
    assert git_status() == status_before


def run_audit(script_name: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / script_name), *arguments],
        cwd=PROJECT_ROOT.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )


@pytest.mark.parametrize("script_name", AUDIT_SCRIPTS)
def test_explicit_report_has_reproducibility_metadata(
    script_name: str, tmp_path: Path
) -> None:
    report_dir = tmp_path / script_name.removesuffix(".py")
    result = run_audit(script_name, "--report-dir", str(report_dir))
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")

    import json

    metadata = json.loads((report_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["generatedAtUtc"].endswith("Z")
    assert metadata["scriptVersion"]
    assert metadata["inputs"]
    for entry in metadata["inputs"]:
        assert not Path(entry["path"]).is_absolute()
        assert len(entry["sha256"]) == 64

    conflict = run_audit(script_name, "--report-dir", str(report_dir))
    assert conflict.returncode != 0
    assert "already exists" in ((conflict.stdout or "") + (conflict.stderr or ""))


def test_elevator_data_export_requires_an_explicit_output(tmp_path: Path) -> None:
    output = tmp_path / "elevator-groups.js"
    result = run_audit(
        "export-elevator-group-audit.py", "--data-output", str(output)
    )
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert output.is_file()
    assert output.read_text(encoding="utf-8").startswith("module.exports =")


def load_commonjs_json(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig").strip()
    while text.startswith("//"):
        _, _, text = text.partition("\n")
        text = text.lstrip()
    body = text.removeprefix("module.exports =").strip().removesuffix(";")
    return json.loads(body)


def write_commonjs_json(path: Path, value: object) -> None:
    path.write_text(
        "module.exports = " + json.dumps(value, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )


def copy_route_fixture(tmp_path: Path) -> Path:
    fixture = tmp_path / "route-gate"
    shutil.copytree(PROJECT_ROOT / "config", fixture / "config")
    shutil.copytree(PROJECT_ROOT / "miniprogram" / "data", fixture / "miniprogram" / "data")
    return fixture


def source_floor_dir() -> Path:
    return (
        PROJECT_ROOT.parent
        / "放入院内导航页面目录下"
        / "放入images文件夹"
        / "floor-maps"
    )


def run_connectivity_fixture(fixture: Path) -> subprocess.CompletedProcess[str]:
    return run_audit(
        "audit-route-connectivity.py",
        "--project-dir",
        str(fixture),
        "--floor-dir",
        str(source_floor_dir()),
    )


def test_connectivity_rejects_endpoint_outside_routing_safe_mask(tmp_path: Path) -> None:
    fixture = copy_route_fixture(tmp_path)
    data_file = fixture / "miniprogram" / "data" / "floorNavPaths.js"
    paths = load_commonjs_json(data_file)
    key = next(key for key in paths if key.endswith("|||toElevator"))
    paths[key]["points"][0] = [0, 0]
    write_commonjs_json(data_file, paths)

    result = run_connectivity_fixture(fixture)
    output = (result.stdout or "") + (result.stderr or "")

    assert result.returncode == 1, output
    assert "start endpoint is outside routing policy safe_mask" in output


def test_connectivity_rejects_forward_reverse_geometry_mismatch(tmp_path: Path) -> None:
    fixture = copy_route_fixture(tmp_path)
    data_file = fixture / "miniprogram" / "data" / "floorNavPaths.js"
    paths = load_commonjs_json(data_file)
    key = next(
        key
        for key, item in paths.items()
        if key.endswith("|||toElevator") and len(item["points"]) > 2
    )
    paths[key]["points"][1][0] += 0.001
    write_commonjs_json(data_file, paths)

    result = run_connectivity_fixture(fixture)
    output = (result.stdout or "") + (result.stderr or "")

    assert result.returncode == 1, output
    assert "forward/reverse geometry mismatch" in output


def test_connectivity_rejects_orphan_from_elevator_record(tmp_path: Path) -> None:
    fixture = copy_route_fixture(tmp_path)
    data_file = fixture / "miniprogram" / "data" / "floorNavPaths.js"
    paths = load_commonjs_json(data_file)
    to_elevator_key = "中医馆|||S1|||toElevator"
    from_elevator_key = "中医馆|||S1|||fromElevator"
    assert to_elevator_key in paths
    assert from_elevator_key in paths
    del paths[to_elevator_key]
    write_commonjs_json(data_file, paths)

    result = run_connectivity_fixture(fixture)
    output = (result.stdout or "") + (result.stderr or "")

    assert result.returncode == 1, output
    assert "missing forward route record" in output


def test_connectivity_rejects_non_colocated_pair_disguised_as_colocated(
    tmp_path: Path,
) -> None:
    fixture = copy_route_fixture(tmp_path)
    data_file = fixture / "miniprogram" / "data" / "sameFloorPaths.js"
    paths = load_commonjs_json(data_file)
    assert sum(item.get("coLocated") is True for item in paths.values()) == 10
    key = "儿科门诊|||挂号缴费"
    paths[key] = {
        **paths[key],
        "points": [paths[key]["points"][0]],
        "routeLength": 0,
        "coLocated": True,
    }
    write_commonjs_json(data_file, paths)
    result = run_connectivity_fixture(fixture)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 1, output
    assert "coLocated" in output and "destination" in output
