from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from route_source_images import (
    build_source_image_manifest,
    copy_authoritative_floor_maps,
)


def _write_jpeg(path: Path, size: tuple[int, int], value: int) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (value, value, value)).save(path, format="JPEG", quality=94)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy(expected_hash: str) -> dict:
    return {
        "schemaVersion": 1,
        "algorithmVersion": "grid-a-star-visible-local-v2",
        "defaults": {},
        "floors": {
            "1楼": {
                "sourceFloorMapSha256": expected_hash,
            }
        },
    }


def test_manifest_matches_policy_hash_not_canonical_filename(tmp_path: Path) -> None:
    _write_jpeg(tmp_path / "miniprogram/assets/floor-maps/1F.jpg", (96, 123), 220)
    source_hash = _write_jpeg(tmp_path / "maintenance/original-plan.jpg", (558, 716), 180)

    manifest = build_source_image_manifest(tmp_path, _policy(source_hash))

    match = manifest["floors"]["1楼"]
    assert match["status"] == "matched"
    assert match["selectedPath"] == "maintenance/original-plan.jpg"
    assert match["width"] == 558
    assert match["height"] == 716
    assert match["sha256"] == source_hash


def test_manifest_reports_missing_policy_hash_even_when_1f_exists(tmp_path: Path) -> None:
    _write_jpeg(tmp_path / "miniprogram/assets/floor-maps/1F.jpg", (96, 123), 220)

    manifest = build_source_image_manifest(tmp_path, _policy("0" * 64))

    assert manifest["floors"]["1楼"]["status"] == "missing"
    assert manifest["missingFloors"] == ["1楼"]


def test_copy_authoritative_floor_maps_requires_every_floor(tmp_path: Path) -> None:
    manifest = {
        "floors": {"1楼": {"status": "missing", "matches": []}},
        "missingFloors": ["1楼"],
    }

    with pytest.raises(RuntimeError, match="1楼"):
        copy_authoritative_floor_maps(tmp_path, tmp_path / "output", manifest)


def test_copy_authoritative_floor_maps_uses_canonical_floor_name(tmp_path: Path) -> None:
    source = tmp_path / "source/original.jpg"
    source_hash = _write_jpeg(source, (558, 716), 180)
    manifest = {
        "floors": {
            "1楼": {
                "status": "matched",
                "selectedPath": "source/original.jpg",
                "sha256": source_hash,
            }
        },
        "missingFloors": [],
    }

    copied = copy_authoritative_floor_maps(tmp_path, tmp_path / "output", manifest)

    assert copied == {"1楼": "1F.jpg"}
    assert hashlib.sha256((tmp_path / "output/1F.jpg").read_bytes()).hexdigest() == source_hash
