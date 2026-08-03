"""Build compact file-level provenance for generated route maps."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROUTE_PROVENANCE_FIELDS = frozenset(
    {
        "algorithmVersion",
        "routingPolicySha256",
        "navigationPolicySha256",
        "autoValidationStatus",
        "reviewStatus",
    }
)


def canonical_json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def current_route_provenance(project_root: Path) -> dict[str, Any]:
    routing_policy = json.loads(
        (project_root / "config" / "routing-policy.json").read_text(
            encoding="utf-8-sig"
        )
    )
    navigation_policy = json.loads(
        (project_root / "config" / "navigation-policy.json").read_text(
            encoding="utf-8-sig"
        )
    )
    algorithm_version = (
        routing_policy.get("algorithmVersion")
        if isinstance(routing_policy, dict)
        else None
    )
    if not isinstance(algorithm_version, str) or not algorithm_version.strip():
        raise ValueError("routing policy lacks algorithmVersion")
    if not isinstance(navigation_policy, dict):
        raise ValueError("navigation policy must be an object")
    return {
        "schemaVersion": 1,
        "algorithmVersion": algorithm_version,
        "routingPolicySha256": canonical_json_sha256(routing_policy),
        "navigationPolicySha256": canonical_json_sha256(navigation_policy),
        "autoValidationStatus": "passed",
        "reviewStatus": "pending",
    }


def render_commonjs_export(
    value: object,
    description: str,
    *,
    provenance: dict[str, Any] | None = None,
) -> str:
    lines = [f"// Auto-generated {description}. Do not edit."]
    if provenance is not None:
        lines.append(
            "// route-provenance: "
            + json.dumps(
                provenance,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    lines.append(
        "module.exports = "
        + json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + ";"
    )
    return "\n".join(lines) + "\n"
