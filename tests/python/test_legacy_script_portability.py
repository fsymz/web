from __future__ import annotations

from pathlib import Path
import re


REPAIR_ROOT = Path(__file__).resolve().parents[3]
LEGACY_SCRIPTS = (
    REPAIR_ROOT
    / "院内导航_模拟导航整合版"
    / "替换到院内导航页面目录下"
    / "scripts"
)


def test_legacy_maintenance_scripts_do_not_embed_a_windows_user_profile() -> None:
    offenders: list[str] = []
    for script in sorted(LEGACY_SCRIPTS.glob("*.*")):
        if re.search(r"[A-Za-z]:\\Users\\", script.read_text(encoding="utf-8"), re.I):
            offenders.append(script.name)

    assert offenders == []
