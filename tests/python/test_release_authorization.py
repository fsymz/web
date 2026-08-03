from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROVENANCE_FILES = (
    Path("miniprogram/data/sameFloorPaths.js"),
    Path("miniprogram/data/floorNavPaths.js"),
)


def copy_release_fixture(target: Path) -> Path:
    """Copy all release-verifier inputs while stubbing unrelated subprocess gates."""

    for directory in ("config", "miniprogram", "scripts", "web-demo"):
        shutil.copytree(
            PROJECT_ROOT / directory,
            target / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copy2(PROJECT_ROOT / "project.config.json", target / "project.config.json")
    (target / "scripts/check-route-turn-quality.py").write_text(
        "print('route-turn quality passed: fixture')\n",
        encoding="utf-8",
    )
    for name in ("build-web-bundle.js", "check-syntax.js", "check-routes.js"):
        (target / "scripts" / name).write_text(
            "'use strict';\nprocess.exitCode = 0;\n",
            encoding="utf-8",
        )
    return target


def run_verifier(
    project: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/verify-release.py", *arguments],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def output_of(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout or "") + (result.stderr or "")


def approve_floor_reviews(project: Path) -> list[str]:
    policy_path = project / "config/routing-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8-sig"))
    floor_names = list(policy["floors"])
    for index, floor_name in enumerate(floor_names, start=1):
        floor = policy["floors"][floor_name]
        floor.update(
            clearanceReviewStatus="approved",
            clearanceEvidenceId=f"clearance-evidence-{index:02d}",
            clearanceReviewer="Hospital route QA reviewer",
            clearanceReviewedAt="2026-08-02T12:00:00Z",
        )
    policy_path.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return floor_names


def approve_file_provenance(project: Path) -> None:
    for relative in PROVENANCE_FILES:
        path = project / relative
        source = path.read_text(encoding="utf-8")
        pending = '"reviewStatus":"pending"'
        assert source.count(pending) == 1, relative
        path.write_text(
            source.replace(pending, '"reviewStatus":"approved"', 1),
            encoding="utf-8",
        )


def test_pending_project_passes_only_as_an_explicit_non_release_candidate(
    tmp_path: Path,
) -> None:
    project = copy_release_fixture(tmp_path / "candidate")
    result = run_verifier(project, "--candidate")
    output = output_of(result)
    assert result.returncode == 0, output
    assert "NOT RELEASE AUTHORIZATION" in output


@pytest.mark.parametrize("arguments", [(), ("--release",)], ids=["default", "explicit"])
def test_pending_project_cannot_pass_the_strict_release_gate(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    project = copy_release_fixture(tmp_path / ("default" if not arguments else "explicit"))
    result = run_verifier(project, *arguments)
    output = output_of(result)
    assert result.returncode == 1, output
    assert "PASS:" not in output


INVALID_FLOOR_REVIEW_CASES = (
    ("pending", None, "approved"),
    ("rejected", None, "approved"),
    ("approved", "clearanceEvidenceId", "evidence"),
    ("approved", "clearanceReviewer", "reviewer"),
    ("approved", "clearanceReviewedAt", "reviewed"),
)


def test_every_floor_blocks_release_when_its_manual_review_is_incomplete(
    tmp_path: Path,
) -> None:
    project = copy_release_fixture(tmp_path / "incomplete-floor-reviews")
    floor_names = approve_floor_reviews(project)
    assert len(floor_names) == 13

    policy_path = project / "config/routing-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    expected_diagnostics = []
    for floor_index, floor_name in enumerate(floor_names):
        status, missing_field, expected_word = INVALID_FLOOR_REVIEW_CASES[
            floor_index % len(INVALID_FLOOR_REVIEW_CASES)
        ]
        floor = policy["floors"][floor_name]
        floor["clearanceReviewStatus"] = status
        if missing_field is not None:
            floor[missing_field] = ""
        expected_diagnostics.append((floor_name, expected_word))
    policy_path.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = run_verifier(project, "--release")
    output = output_of(result)
    assert result.returncode == 1, output
    assert "PASS:" not in output
    for floor_name, expected_word in expected_diagnostics:
        assert re.search(
            rf"(?:{re.escape(floor_name)}[^\r\n]*{re.escape(expected_word)}|"
            rf"{re.escape(expected_word)}[^\r\n]*{re.escape(floor_name)})",
            output,
            re.IGNORECASE,
        ), output


def test_file_level_approval_cannot_replace_hash_bound_release_registries(
    tmp_path: Path,
) -> None:
    project = copy_release_fixture(tmp_path / "missing-release-registries")
    approve_floor_reviews(project)
    approve_file_provenance(project)

    result = run_verifier(project, "--release")
    output = output_of(result)
    assert result.returncode == 1, output
    assert "PASS:" not in output
    for expected_count, expected_label in (
        (730, "route"),
        (95, "anchor"),
        (1462, "cross-floor"),
    ):
        assert re.search(
            rf"(?:{expected_count}.*{re.escape(expected_label)}|"
            rf"{re.escape(expected_label)}.*{expected_count})",
            output,
            re.IGNORECASE | re.DOTALL,
        ), output
