from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PLUGIN = {
    "version": "0.3.5",
    "provider": "wx069ba97219f66d99",
}


def copy_release_fixture(target: Path) -> Path:
    """Copy all verifier dependencies, including every subordinate release gate."""

    for directory in ("config", "miniprogram", "scripts", "web-demo"):
        shutil.copytree(
            PROJECT_ROOT / directory,
            target / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copy2(PROJECT_ROOT / "project.config.json", target / "project.config.json")
    # Keep the complete file layout while replacing unrelated external gates
    # with deterministic successes for fast, attribution-safe mutations.
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


def run_verifier(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/verify-release.py", "--candidate"],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def update_app_json(project: Path, mutate: Callable[[dict], None]) -> None:
    path = project / "miniprogram/app.json"
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    mutate(manifest)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def remove_plugin(project: Path) -> None:
    update_app_json(project, lambda app: app.pop("plugins"))


def extra_plugin(project: Path) -> None:
    update_app_json(
        project,
        lambda app: app["plugins"].update(
            {"OtherPlugin": {"version": "1.0.0", "provider": "wx0000000000000000"}}
        ),
    )


def wrong_provider(project: Path) -> None:
    update_app_json(project, lambda app: app["plugins"]["WechatSI"].update(provider="wrong"))


def wrong_version(project: Path) -> None:
    update_app_json(project, lambda app: app["plugins"]["WechatSI"].update(version="0.3.4"))


def add_permission(project: Path) -> None:
    update_app_json(
        project,
        lambda app: app.update(permission={"scope.record": {"desc": "record"}}),
    )


def add_nested_mp3(project: Path) -> None:
    path = project / "miniprogram/pages/navigation/hidden/escape.mp3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not-a-real-mp3")


def add_nested_webm(project: Path) -> None:
    path = project / "miniprogram/utils/hidden/escape.webm"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not-a-real-webm")


def call_callback(project: Path) -> None:
    path = project / "miniprogram/pages/navigation/navigation.js"
    source = path.read_text(encoding="utf-8")
    old = "    manager.onStart = handlers.onStart;"
    assert source.count(old) == 1
    path.write_text(source.replace(old, "    manager.onStart(handlers.onStart);", 1), encoding="utf-8")


def bind_after_start(project: Path) -> None:
    path = project / "miniprogram/pages/navigation/navigation.js"
    source = path.read_text(encoding="utf-8")
    bind = "      this.bindVoiceManagerSession(manager, session);\n"
    start = (
        "      manager.start({\n"
        "        duration: 30000,\n"
        "        lang: 'zh_CN'\n"
        "      });"
    )
    assert source.count(bind) == 1
    assert source.count(start) == 1
    source = source.replace(bind, "", 1)
    source = source.replace(start, start + "\n" + bind.rstrip(), 1)
    path.write_text(source, encoding="utf-8")


def add_legacy_audio_identifier(project: Path) -> None:
    path = project / "miniprogram/pages/navigation/navigation.js"
    source = path.read_text(encoding="utf-8")
    marker = "  onShow: function() {"
    assert source.count(marker) == 1
    path.write_text(
        source.replace(marker, marker + "\n    this.playCurrentLocalAudio();", 1),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("case_name", "mutation", "expected_message"),
    (
        ("remove-plugin", remove_plugin, "WechatSI"),
        ("extra-plugin", extra_plugin, "only WechatSI"),
        ("wrong-provider", wrong_provider, EXPECTED_PLUGIN["provider"]),
        ("wrong-version", wrong_version, EXPECTED_PLUGIN["version"]),
        ("permission", add_permission, "permission"),
        ("nested-mp3", add_nested_mp3, "local audio"),
        ("nested-webm", add_nested_webm, "local audio"),
        ("callback-call", call_callback, "callback property"),
        ("bind-after-start", bind_after_start, "before manager.start"),
        ("legacy-audio-identifier", add_legacy_audio_identifier, "local audio identifier"),
    ),
)
def test_voice_configuration_mutations_fail_verification(
    case_name: str,
    mutation: Callable[[Path], None],
    expected_message: str,
    tmp_path: Path,
) -> None:
    project = copy_release_fixture(tmp_path / case_name)
    mutation(project)
    result = run_verifier(project)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 1, output
    assert expected_message.casefold() in output.casefold(), output


def test_complete_unmodified_fixture_passes_as_a_candidate(tmp_path: Path) -> None:
    project = copy_release_fixture(tmp_path / "valid")
    result = run_verifier(project)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, output
    assert "NOT RELEASE AUTHORIZATION" in output
    assert "audio: 0 B (0 files)" in result.stdout
    assert "route-turn quality gate: passed" in result.stdout
