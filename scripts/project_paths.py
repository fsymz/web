"""Project-relative path and source-integrity helpers for data generators."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


LEGACY_FLOOR_FILE_INDEX = {
    1: 1,
    2: 5,
    3: 11,
    4: 4,
    5: 7,
    6: 8,
    7: 9,
    8: 10,
    9: 13,
    10: 14,
    11: 15,
    12: 16,
    13: 17,
}


def project_root(script_file: str | Path | None = None) -> Path:
    """Return the mini-program project root from a script location."""

    source = Path(script_file) if script_file is not None else Path(__file__)
    return source.resolve().parent.parent


def resolve_cli_path(value: str | Path | None, default: str | Path) -> Path:
    """Resolve an explicit CLI path, otherwise a project-root-relative default."""

    if value is None:
        candidate = project_root() / Path(default)
    else:
        candidate = Path(value).expanduser()
    return candidate.resolve()


def parse_floor_number(value: object) -> int:
    match = re.search(r"(\d+)", str(value or ""))
    if not match:
        raise ValueError(f"cannot parse floor number from {value!r}")
    floor = int(match.group(1))
    if floor not in LEGACY_FLOOR_FILE_INDEX:
        raise ValueError(f"floor is outside the supported 1F-13F range: {value!r}")
    return floor


def resolve_floor_map(floor_dir: str | Path, floor: int | str) -> Path:
    """Resolve canonical ``1F.jpg`` names or the legacy maintenance names."""

    floor_number = parse_floor_number(floor)
    directory = Path(floor_dir).resolve()
    canonical_names = (
        f"{floor_number}F.jpg",
        f"{floor_number}F.jpeg",
        f"{floor_number}f.jpg",
        f"{floor_number}f.jpeg",
    )
    for name in canonical_names:
        candidate = directory / name
        if candidate.is_file():
            return candidate

    legacy_index = LEGACY_FLOOR_FILE_INDEX[floor_number]
    legacy_matches = sorted(
        (
            candidate
            for pattern in (f"*({legacy_index}).jpg", f"*({legacy_index}).jpeg")
            for candidate in directory.glob(pattern)
            if candidate.is_file()
        ),
        key=lambda item: item.name,
    )
    if legacy_matches:
        return legacy_matches[0]

    raise FileNotFoundError(
        f"missing floor map for {floor_number}F in {directory}; "
        f"expected {floor_number}F.jpg or legacy *({legacy_index}).jpg"
    )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_commonjs_json(path: str | Path) -> Any:
    source_path = Path(path)
    text = source_path.read_text(encoding="utf-8-sig").strip()
    while text.startswith("//"):
        _, separator, text = text.partition("\n")
        if not separator:
            break
        text = text.lstrip()
    prefix = "module.exports ="
    if not text.startswith(prefix):
        raise ValueError(f"{source_path} is not a JSON-compatible CommonJS export")
    body = text[len(prefix) :].strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    return json.loads(body)
