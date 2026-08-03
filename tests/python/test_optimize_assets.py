from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "optimize-assets.py"
REQUIREMENTS_PATH = PROJECT_ROOT / "requirements.txt"
EXPECTED_REQUIREMENTS = [
    "numpy==1.26.4",
    "opencv-python-headless==4.10.0.84",
    "Pillow==10.4.0",
    "pytest==7.4.4",
]


def load_optimizer():
    assert SCRIPT_PATH.is_file(), "optimize-assets.py must exist"
    spec = importlib.util.spec_from_file_location("optimize_assets", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_floor_sources(directory: Path) -> None:
    directory.mkdir(parents=True)
    for floor in range(1, 14):
        image = Image.new("RGB", (1000, 1282), (248, 248, 244))
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 80, 960, 1200), outline=(30, 80, 120), width=8)
        draw.line((80, 640, 920, 640), fill=(190, 45, 40), width=10)
        draw.text((80, 100), f"{floor}F FLOOR MAP", fill=(10, 10, 10))
        image.save(directory / f"{floor}F.jpg", quality=95)


def run_optimizer(
    source_floors: Path,
    output: Path,
    *extra_args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--source-floor-dir", str(source_floors),
            "--output-assets-dir", str(output),
            "--width", "960",
            "--quality", "65",
            "--min-quality", "55",
            "--max-quality", "75",
            "--min-width", "800",
            "--max-image-kib", "90",
            *extra_args,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_requirements_are_exactly_version_locked():
    assert REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines() == EXPECTED_REQUIREMENTS


def test_cli_builds_deterministic_bounded_maps_only(tmp_path: Path):
    source_floors = tmp_path / "source-floor"
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    create_floor_sources(source_floors)

    first = run_optimizer(source_floors, first_output)
    assert first.returncode == 0, first.stdout + first.stderr
    second = run_optimizer(source_floors, second_output)
    assert second.returncode == 0, second.stdout + second.stderr

    maps = sorted(
        (first_output / "floor-maps").glob("*.jpg"),
        key=lambda path: int(path.stem.removesuffix("F")),
    )
    assert [path.name for path in maps] == [f"{floor}F.jpg" for floor in range(1, 14)]
    assert not (first_output / "audio").exists()

    for floor_map in maps:
        with Image.open(floor_map) as image:
            assert image.mode == "RGB"
            assert image.width == 960
            assert image.info.get("progressive") or image.info.get("progression")
        assert floor_map.stat().st_size <= 90 * 1024
        assert sha256(floor_map) == sha256(second_output / "floor-maps" / floor_map.name)


def test_cli_rejects_removed_source_audio_option(tmp_path: Path):
    source_floors = tmp_path / "source-floor"
    create_floor_sources(source_floors)

    result = run_optimizer(
        source_floors,
        tmp_path / "output",
        "--source-audio-dir",
        str(tmp_path / "source-audio"),
    )

    assert result.returncode == 2
    assert "unrecognized arguments: --source-audio-dir" in result.stderr


def test_map_optimizer_fails_instead_of_crossing_the_minimum_width(tmp_path: Path):
    optimizer = load_optimizer()
    random_pixels = np.random.default_rng(7).integers(0, 256, (1024, 800, 3), dtype=np.uint8)
    source = tmp_path / "noise.jpg"
    Image.fromarray(random_pixels, "RGB").save(source, quality=100)

    with pytest.raises(optimizer.AssetOptimizationError, match="size limit"):
        optimizer.optimize_floor_map(
            source,
            tmp_path / "output.jpg",
            width=800,
            quality=65,
            min_quality=55,
            max_quality=75,
            min_width=800,
            max_bytes=256,
        )
