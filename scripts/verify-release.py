"""Deterministic publication gate for the patient-facing WeChat mini-program."""

from __future__ import annotations

import argparse
import sys

sys.dont_write_bytecode = True
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from collections import Counter
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MINIPROGRAM_ROOT = PROJECT_ROOT / "miniprogram"
NAVIGATION_RELATIVE = "pages/navigation/navigation.js"
NAVIGATION_WXML_RELATIVE = "pages/navigation/navigation.wxml"
PACKAGE_LIMIT_BYTES = 1_887_436
MAP_LIMIT_BYTES = 90 * 1024
EXPECTED_MAPS = {f"{floor}F.jpg" for floor in range(1, 14)}
EXPECTED_WECHAT_SI = {
    "version": "0.3.5",
    "provider": "wx069ba97219f66d99",
}
EXPECTED_FLOORS = {f"{floor}楼" for floor in range(1, 14)}
ROUTE_PROVENANCE_PREFIX = "// route-provenance: "
ROUTE_PROVENANCE_FILES = (
    Path("miniprogram/data/sameFloorPaths.js"),
    Path("miniprogram/data/floorNavPaths.js"),
)
RELEASE_REVIEW_REGISTRIES = (
    (Path("config/anchor-reviews.json"), 95, "anchor"),
    (Path("config/route-reviews.json"), 730, "route"),
    (Path("config/cross-floor-reviews.json"), 1462, "cross-floor"),
)
AUDIO_EXTENSIONS = {
    ".aac",
    ".amr",
    ".caf",
    ".flac",
    ".m4a",
    ".mp3",
    ".mp2",
    ".mpeg",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
    ".wma",
}
TEXT_EXTENSIONS = {".js", ".json", ".wxml", ".wxss"}
FORBIDDEN_FILE_EXTENSIONS = {".py", ".pyc"}
OLD_LOCAL_AUDIO_IDENTIFIERS = (
    "/assets/audio",
    "assets/audio",
    "audioRoutes",
    "getAudioForLeg",
    "currentAudioArray",
    "currentAudioIndex",
    "innerAudioContext",
    "agentAudioContext",
    "activeLocalAudioPlayback",
    "speakAgentReply",
    "localFallback",
    "getLocalNavigationAudio",
    "playLocalNavigationPrompt",
    "playAudioOnce",
    "playAudioArray",
    "playCurrentLocalAudio",
    "handleLocalAudioEnded",
    "clearActiveLocalAudioPlayback",
    "finishLocalAudioQueue",
)
FORBIDDEN_SOURCE_PATTERNS = {
    "location API": re.compile(r"\bwx\.(?:getLocation|chooseLocation|openLocation|startLocationUpdate)\b", re.I),
    "Bluetooth API": re.compile(r"\bwx\.\w*Bluetooth\w*\b", re.I),
    "Wi-Fi API": re.compile(r"\bwx\.(?:startWifi|stopWifi|connectWifi|getWifiList|onGetWifiList)\b", re.I),
    "UWB API": re.compile(r"\bwx\.\w*UWB\w*\b", re.I),
    "network request": re.compile(r"\bwx\.request\b"),
    "storage API": re.compile(r"\bwx\.(?:set|get|remove|clear)Storage(?:Sync)?\b"),
    "persistent file API": re.compile(r"\b(?:wx\.saveFile|wx\.getFileSystemManager|USER_DATA_PATH|FileSystemManager)\b"),
    "route history": re.compile(r"\b(?:routeHistory|navigationHistory|historyRoutes|savedRoutes)\b", re.I),
    "external URL": re.compile(r"https?://", re.I),
}
FORBIDDEN_DYNAMIC_JS_PATTERNS = {
    "eval": re.compile(r"(?<![\w$])eval(?![\w$])"),
    "Function constructor": re.compile(r"(?<![\w$])Function(?![\w$])"),
    "getCurrentPages": re.compile(r"(?<![\w$])getCurrentPages(?![\w$])"),
    "globalThis": re.compile(r"(?<![\w$])globalThis(?![\w$])"),
    "Reflect API": re.compile(r"(?<![\w$])Reflect(?![\w$])"),
}
FORBIDDEN_RAW_JS_PATTERNS = {
    "template interpolation (${) unsupported by static analysis": re.compile(r"\$\{"),
    "__proto__ access": re.compile(r"__proto__"),
    "wx.getRecorderManager": re.compile(r"getRecorderManager"),
}
DANGEROUS_COMPUTED_MEMBER = re.compile(
    r"\[\s*(['\"`])(?:requirePlugin|getRecordRecognitionManager|textToSpeech|"
    r"createInnerAudioContext|start|play)\1\s*\]"
)
DANGEROUS_COMPUTED_CONCAT = re.compile(
    r"\[[^\]\r\n]*['\"][^'\"]*['\"]\s*\+[^\]\r\n]*\]"
)
DANGEROUS_VOICE_MEMBER_NAMES = {
    "requirePlugin",
    "getRecordRecognitionManager",
    "textToSpeech",
    "createInnerAudioContext",
    "start",
    "play",
}
DANGEROUS_DYNAMIC_OBJECT_ACCESS = re.compile(
    r"(?<![\w$])(?:wx|plugin|context|this\.(?:voiceManager|wechatSIPlugin|"
    r"speechAudioContext))\s*\["
)
SAFE_BOUND_PAGE_CALLBACKS = {
    ("createSimNavEngine", "handleNavLegChange"),
    ("createSimNavEngine", "handleNavFrame"),
    ("createSimNavEngine", "handleNavStats"),
    ("createSimNavEngine", "handleNavLegComplete"),
    ("createSimNavEngine", "handleNavFinish"),
    ("createSimNavEngine", "handleNavStateChange"),
    ("createSimNavEngine", "handleNavError"),
}
CALLBACK_NAMES = ("onStart", "onStop", "onError", "onRecognize")
EXPLICIT_RECORD_BUTTONS = {
    "toggleVoiceInput": "destination",
    "toggleAgentVoiceInput": "agent",
}


def all_files(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda item: item.as_posix(),
    )


def format_bytes(value: int) -> str:
    return f"{value:,} B ({value / 1024 / 1024:.3f} MiB)"


def run_gate(command: list[str], label: str, errors: list[str]) -> bool:
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        details = "\n".join(
            part.strip()
            for part in (result.stdout, result.stderr)
            if part and part.strip()
        )
        errors.append(f"{label} failed (exit {result.returncode}): {details}")
        return False
    return True


def read_json(path: Path, errors: list[str]) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as error:
        try:
            relative = path.relative_to(PROJECT_ROOT)
        except ValueError:
            relative = path
        errors.append(f"invalid JSON {relative}: {error}")
        return None


def validate_floor_clearance_reviews(errors: list[str]) -> None:
    policy_path = PROJECT_ROOT / "config" / "routing-policy.json"
    policy = read_json(policy_path, errors)
    if not isinstance(policy, dict):
        errors.append("patient release clearance review: routing policy must be an object")
        return
    floors = policy.get("floors")
    if not isinstance(floors, dict):
        errors.append("patient release clearance review: floors must be an object")
        return

    actual_floor_names = set(floors)
    if actual_floor_names != EXPECTED_FLOORS:
        missing = sorted(EXPECTED_FLOORS - actual_floor_names)
        extra = sorted(actual_floor_names - EXPECTED_FLOORS)
        errors.append(
            "patient release clearance review: expected exactly 13 floors; "
            f"missing={missing} extra={extra}"
        )

    for floor_name in sorted(actual_floor_names & EXPECTED_FLOORS, key=lambda item: int(item[:-1])):
        review = floors.get(floor_name)
        if not isinstance(review, dict):
            errors.append(f"patient release clearance review {floor_name}: review must be an object")
            continue
        if review.get("clearanceReviewStatus") != "approved":
            errors.append(
                f"patient release clearance review {floor_name}: status must be approved; "
                f"found {review.get('clearanceReviewStatus')!r}"
            )
            continue
        if not isinstance(review.get("clearanceEvidenceId"), str) or not review["clearanceEvidenceId"].strip():
            errors.append(f"patient release clearance review {floor_name}: evidence is required")
        if not isinstance(review.get("clearanceReviewer"), str) or not review["clearanceReviewer"].strip():
            errors.append(f"patient release clearance review {floor_name}: reviewer is required")
        reviewed_at = review.get("clearanceReviewedAt")
        if not isinstance(reviewed_at, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
            reviewed_at.strip(),
        ):
            errors.append(
                f"patient release clearance review {floor_name}: reviewed timestamp is required"
            )


def validate_route_provenance_approval(errors: list[str]) -> None:
    for relative in ROUTE_PROVENANCE_FILES:
        path = PROJECT_ROOT / relative
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            errors.append(f"patient release route provenance {relative}: cannot read: {error}")
            continue
        headers = [line for line in lines if line.startswith(ROUTE_PROVENANCE_PREFIX)]
        if len(headers) != 1:
            errors.append(
                f"patient release route provenance {relative}: expected exactly one header"
            )
            continue
        try:
            provenance = json.loads(headers[0].removeprefix(ROUTE_PROVENANCE_PREFIX))
        except (json.JSONDecodeError, ValueError) as error:
            errors.append(f"patient release route provenance {relative}: invalid JSON: {error}")
            continue
        if not isinstance(provenance, dict) or provenance.get("reviewStatus") != "approved":
            found = provenance.get("reviewStatus") if isinstance(provenance, dict) else None
            errors.append(
                f"patient release route provenance {relative}: reviewStatus must be approved; "
                f"found {found!r}"
            )


def validate_release_review_registries(errors: list[str]) -> None:
    for relative, expected_count, label in RELEASE_REVIEW_REGISTRIES:
        path = PROJECT_ROOT / relative
        if not path.is_file():
            errors.append(
                f"patient release requires {expected_count} hash-bound {label} approvals; "
                f"missing {relative}"
            )
            continue
        errors.append(
            f"patient release requires independent validation of {expected_count} hash-bound "
            f"{label} approvals; registry validation is not yet implemented"
        )

    site_validation = PROJECT_ROOT / "config" / "site-validation.json"
    if not site_validation.is_file():
        errors.append(
            "patient release requires confirmed physical-site validation; "
            "missing config/site-validation.json"
        )
    else:
        errors.append(
            "patient release physical-site registry validation is not yet implemented"
        )


def validate_voice_device_acceptance(errors: list[str]) -> None:
    acceptance_path = PROJECT_ROOT / "docs" / "acceptance" / "voice-acceptance.md"
    try:
        acceptance = acceptance_path.read_text(encoding="utf-8")
    except OSError:
        errors.append(
            "patient release requires Android and iPhone voice acceptance; "
            "missing docs/acceptance/voice-acceptance.md"
        )
        return
    if "| 面向患者发布结论 | Pass |" not in acceptance:
        errors.append(
            "patient release requires Android and iPhone voice acceptance: "
            "release conclusion is not Pass"
        )


def validate_patient_release_authorization(errors: list[str]) -> None:
    validate_floor_clearance_reviews(errors)
    validate_route_provenance_approval(errors)
    validate_release_review_registries(errors)
    validate_voice_device_acceptance(errors)


def validate_json_files(errors: list[str]) -> None:
    json_files = {
        PROJECT_ROOT / "project.config.json",
        *sorted((PROJECT_ROOT / "config").glob("*.json")),
        *sorted(MINIPROGRAM_ROOT.rglob("*.json")),
    }
    for path in sorted(json_files, key=lambda item: item.as_posix()):
        if not path.is_file():
            errors.append(f"missing JSON file: {path.relative_to(PROJECT_ROOT)}")
            continue
        read_json(path, errors)


def validate_wechat_si_manifest(errors: list[str]) -> None:
    app_path = MINIPROGRAM_ROOT / "app.json"
    manifest = read_json(app_path, errors)
    if not isinstance(manifest, dict):
        errors.append("WechatSI configuration: miniprogram/app.json must be an object")
        return

    if "permission" in manifest:
        errors.append(
            "WechatSI configuration: top-level app.json permission is forbidden; "
            "microphone access must remain an explicit runtime action"
        )

    plugins = manifest.get("plugins")
    if not isinstance(plugins, dict) or "WechatSI" not in plugins:
        errors.append("WechatSI configuration: app.json must declare the WechatSI plugin")
        return
    if set(plugins) != {"WechatSI"}:
        errors.append(
            "WechatSI configuration: app.json plugins must contain only WechatSI; "
            f"found {sorted(plugins)}"
        )

    declaration = plugins.get("WechatSI")
    if not isinstance(declaration, dict):
        errors.append("WechatSI configuration: WechatSI declaration must be an object")
        return
    if declaration.get("version") != EXPECTED_WECHAT_SI["version"]:
        errors.append(
            "WechatSI configuration: version must be exactly "
            f"{EXPECTED_WECHAT_SI['version']}; found {declaration.get('version')!r}"
        )
    if declaration.get("provider") != EXPECTED_WECHAT_SI["provider"]:
        errors.append(
            "WechatSI configuration: provider must be exactly "
            f"{EXPECTED_WECHAT_SI['provider']}; found {declaration.get('provider')!r}"
        )
    if set(declaration) != {"version", "provider"}:
        errors.append(
            "WechatSI configuration: plugin declaration permits only version/provider; "
            f"found {sorted(declaration)}"
        )


def mask_javascript(source: str) -> str:
    """Mask strings/comments while preserving offsets and executable punctuation."""

    masked = list(source)
    index = 0
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if char == "/" and following == "/":
            masked[index] = masked[index + 1] = " "
            index += 2
            while index < len(source) and source[index] not in "\r\n":
                masked[index] = " "
                index += 1
            continue
        if char == "/" and following == "*":
            masked[index] = masked[index + 1] = " "
            index += 2
            while index < len(source):
                if (
                    source[index] == "*"
                    and index + 1 < len(source)
                    and source[index + 1] == "/"
                ):
                    masked[index] = masked[index + 1] = " "
                    index += 2
                    break
                if source[index] not in "\r\n":
                    masked[index] = " "
                index += 1
            continue
        if char in ("'", '"', "`"):
            quote = char
            masked[index] = " "
            index += 1
            escaped = False
            while index < len(source):
                current = source[index]
                if current not in "\r\n":
                    masked[index] = " "
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == quote:
                    index += 1
                    break
                index += 1
            continue
        index += 1
    return "".join(masked)


def find_matching_delimiter(
    source: str,
    opening_index: int,
    opening: str,
    closing: str,
) -> int:
    depth = 0
    for index in range(opening_index, len(source)):
        if source[index] == opening:
            depth += 1
        elif source[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    raise ValueError(f"unclosed JavaScript delimiter {opening} at offset {opening_index}")


def extract_page_methods(source: str) -> dict[str, tuple[str, str]]:
    masked = mask_javascript(source)
    pattern = re.compile(
        r"(?m)^\s{2}([A-Za-z_$][\w$]*):\s*function\s*\([^)]*\)\s*\{"
    )
    methods: dict[str, tuple[str, str]] = {}
    for match in pattern.finditer(masked):
        name = match.group(1)
        if name in methods:
            raise ValueError(f"duplicate page function: {name}")
        opening = masked.rfind("{", match.start(), match.end())
        closing = find_matching_delimiter(masked, opening, "{", "}")
        methods[name] = (
            source[opening + 1 : closing],
            masked[opening + 1 : closing],
        )
    return methods


def extract_this_calls(body: str, masked_body: str) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    for match in re.finditer(r"\bthis\.([A-Za-z_$][\w$]*)\s*\(", masked_body):
        opening = match.end() - 1
        closing = find_matching_delimiter(masked_body, opening, "(", ")")
        calls.append((match.group(1), body[opening + 1 : closing]))
    return calls


def validate_page_reference_safety(
    methods: dict[str, tuple[str, str]],
    errors: list[str],
) -> None:
    safe_callback_references: list[tuple[str, str]] = []
    safe_bare_this_offsets: dict[str, set[int]] = {}
    for owner, (_, masked_body) in methods.items():
        for match in re.finditer(r"(?<![\w$])this\.([A-Za-z_$][\w$]*)", masked_body):
            referenced = match.group(1)
            if referenced not in methods:
                continue
            suffix = masked_body[match.end() :]
            if re.match(r"\s*\(", suffix):
                continue
            pair = (owner, referenced)
            bound = re.match(r"\s*\.bind\s*\(\s*this\s*\)", suffix)
            if pair in SAFE_BOUND_PAGE_CALLBACKS and bound:
                safe_callback_references.append(pair)
                bare = re.search(r"(?<![\w$])this(?![\w$])", bound.group(0))
                if bare:
                    safe_bare_this_offsets.setdefault(owner, set()).add(
                        match.end() + bound.start() + bare.start()
                    )
                continue
            errors.append(
                "voice policy: Page method references must be direct calls; "
                f"{owner} aliases this.{referenced}"
            )

    if Counter(safe_callback_references) != Counter(
        {pair: 1 for pair in SAFE_BOUND_PAGE_CALLBACKS}
    ):
        errors.append(
            "voice policy: only the seven reviewed SimNav callbacks may bind Page methods; "
            f"found {safe_callback_references}"
        )

    for owner, (_, masked_body) in methods.items():
        allowed = safe_bare_this_offsets.get(owner, set())
        for match in re.finditer(r"(?<![\w$])this(?![\w$])", masked_body):
            if re.match(r"\.[A-Za-z_$][\w$]*", masked_body[match.end() :]):
                continue
            if match.start() in allowed:
                continue
            errors.append(
                "voice policy: Page object must not escape or use dynamic access; "
                f"found in {owner}"
            )


def classify_bare_calls(
    methods: dict[str, tuple[str, str]],
    token: str,
) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    for owner, (_, masked_body) in methods.items():
        for match in re.finditer(rf"(?<![\w$]){re.escape(token)}(?![\w$])", masked_body):
            before = masked_body[: match.start()]
            after = masked_body[match.end() :]
            if re.search(r"\btypeof\s+$", before):
                kind = "typeof"
            elif re.match(r"\s*\(", after):
                kind = "direct"
            else:
                kind = "alias"
            references.append((owner, kind))
    return references


def classify_plugin_members(
    methods: dict[str, tuple[str, str]],
    token: str,
) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    pattern = re.compile(rf"(?<![\w$]){re.escape(token)}(?![\w$])")
    for owner, (_, masked_body) in methods.items():
        for match in pattern.finditer(masked_body):
            before = masked_body[: match.start()]
            after = masked_body[match.end() :]
            plugin_member = bool(re.search(r"\bplugin\.\s*$", before))
            type_guard = bool(re.search(r"\btypeof\s+plugin\.\s*$", before))
            direct = plugin_member and bool(re.match(r"\s*\(", after))
            references.append(
                (owner, "typeof" if type_guard else "direct" if direct else "alias")
            )
    return references


def validate_voice_primitives(
    source: str,
    methods: dict[str, tuple[str, str]],
    errors: list[str],
) -> None:
    expected_references = {
        "requirePlugin": Counter(
            {("getWechatSIPlugin", "typeof"): 1, ("getWechatSIPlugin", "direct"): 1}
        ),
        "getRecordRecognitionManager": Counter(
            {("startRecorderForSession", "typeof"): 1, ("startRecorderForSession", "direct"): 1}
        ),
        "textToSpeech": Counter(
            {("synthesizeCurrentSpeechChunk", "typeof"): 1, ("synthesizeCurrentSpeechChunk", "direct"): 1}
        ),
    }
    actual_references = {
        "requirePlugin": classify_bare_calls(methods, "requirePlugin"),
        "getRecordRecognitionManager": classify_plugin_members(
            methods, "getRecordRecognitionManager"
        ),
        "textToSpeech": classify_plugin_members(methods, "textToSpeech"),
    }
    for primitive, expected in expected_references.items():
        actual = actual_references[primitive]
        if Counter(actual) != expected:
            errors.append(
                f"voice policy: {primitive} must be directly accessed only in its reviewed "
                f"function; found {actual}"
            )

    plugin_token_sites = Counter()
    for owner, (_, masked_body) in methods.items():
        plugin_token_sites[owner] += len(
            re.findall(r"(?<![\w$])plugin(?![\w$])", masked_body)
        )
    plugin_token_sites += Counter()
    expected_plugin_token_sites = Counter(
        {
            "getWechatSIPlugin": 4,
            "startRecorderForSession": 4,
            "synthesizeCurrentSpeechChunk": 4,
        }
    )
    if plugin_token_sites != expected_plugin_token_sites:
        errors.append(
            "voice policy: plugin capability object must not escape the three reviewed "
            f"functions; found {dict(plugin_token_sites)}"
        )

    cached_plugin_field_sites = Counter()
    for owner, (_, masked_body) in methods.items():
        cached_plugin_field_sites[owner] += len(
            re.findall(r"\bthis\.wechatSIPlugin\b", masked_body)
        )
    cached_plugin_field_sites += Counter()
    expected_cached_plugin_field_sites = Counter(
        {"onUnload": 1, "getWechatSIPlugin": 4}
    )
    if cached_plugin_field_sites != expected_cached_plugin_field_sites:
        errors.append(
            "voice policy: cached WechatSI capability object must not escape its reviewed "
            f"lifecycle/getter sites; found {dict(cached_plugin_field_sites)}"
        )

    plugin_body = methods.get("getWechatSIPlugin", ("", ""))[0]
    if len(re.findall(r"\brequirePlugin\s*\(\s*['\"]WechatSI['\"]\s*\)", plugin_body)) != 1:
        errors.append(
            "voice policy: getWechatSIPlugin must directly request exactly WechatSI once"
        )

    masked = mask_javascript(source)
    dangerous_member_alias = re.compile(
        r"\b(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*"
        r"(?:plugin|manager|this\.(?:voiceManager|wechatSIPlugin|speechAudioContext))\."
        r"(?:getRecordRecognitionManager|textToSpeech|start|play)\b"
    )
    if dangerous_member_alias.search(masked):
        errors.append(
            "voice policy: requirePlugin/getRecordRecognitionManager/textToSpeech/start/play "
            "must not escape through aliases"
        )
    dangerous_object_alias = re.compile(
        r"\b(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*(?:plugin|manager)\s*;"
    )
    if dangerous_object_alias.search(masked):
        errors.append("voice policy: plugin/manager objects must not escape through aliases")


def validate_recognition_callbacks(
    methods: dict[str, tuple[str, str]],
    errors: list[str],
) -> None:
    bind_source, bind_masked = methods.get("bindVoiceManagerSession", ("", ""))
    for callback in CALLBACK_NAMES:
        assignment = re.compile(
            rf"\bmanager\.{callback}\s*=\s*handlers\.{callback}\s*;"
        )
        if len(assignment.findall(bind_masked)) != 1:
            errors.append(
                "voice policy: callback property assignments must bind "
                f"manager.{callback} = handlers.{callback} exactly once in "
                "bindVoiceManagerSession"
            )
        property_owners = [
            owner
            for owner, (_, masked_body) in methods.items()
            for _ in re.finditer(rf"\bmanager\.{callback}\b", masked_body)
        ]
        if property_owners != ["bindVoiceManagerSession"]:
            errors.append(
                "voice policy: each callback property may be written exactly once in "
                f"bindVoiceManagerSession; manager.{callback} found in {property_owners}"
            )
        call_pattern = re.compile(rf"\bmanager\.{callback}\s*\(")
        callers = [
            owner
            for owner, (_, masked_body) in methods.items()
            if call_pattern.search(masked_body)
        ]
        if callers:
            errors.append(
                "voice policy: WechatSI callbacks are callback properties, not methods; "
                f"manager.{callback}(...) found in {callers}"
            )

    start_source, start_masked = methods.get("startRecorderForSession", ("", ""))
    bind_positions = [
        match.start()
        for match in re.finditer(
            r"\bthis\.bindVoiceManagerSession\s*\(\s*manager\s*,\s*session\s*\)",
            start_masked,
        )
    ]
    start_positions = [
        match.start() for match in re.finditer(r"\bmanager\.start\s*\(", start_masked)
    ]
    if (
        len(bind_positions) != 1
        or len(start_positions) != 1
        or bind_positions[0] > start_positions[0]
    ):
        errors.append(
            "voice policy: bindVoiceManagerSession must bind callback properties before manager.start"
        )

    manager_start_references: list[tuple[str, str]] = []
    for owner, (_, masked_body) in methods.items():
        for match in re.finditer(r"\bmanager\.start\b", masked_body):
            before = masked_body[: match.start()]
            after = masked_body[match.end() :]
            kind = (
                "typeof"
                if re.search(r"\btypeof\s+$", before)
                else "direct"
                if re.match(r"\s*\(", after)
                else "alias"
            )
            manager_start_references.append((owner, kind))
    if Counter(manager_start_references) != Counter(
        {
            ("startRecorderForSession", "typeof"): 1,
            ("startRecorderForSession", "direct"): 1,
        }
    ):
        errors.append(
            "voice policy: manager.start must be guarded and directly called only in "
            f"startRecorderForSession; found {manager_start_references}"
        )


def validate_speech_architecture(
    methods: dict[str, tuple[str, str]],
    calls_by_method: dict[str, list[tuple[str, str]]],
    errors: list[str],
) -> None:
    context_references: list[tuple[str, str]] = []
    play_references: list[tuple[str, str]] = []
    for owner, (_, masked_body) in methods.items():
        for match in re.finditer(r"\bcreateInnerAudioContext\b", masked_body):
            before = masked_body[: match.start()]
            after = masked_body[match.end() :]
            direct = bool(re.search(r"\bwx\.\s*$", before)) and bool(
                re.match(r"\s*\(", after)
            )
            context_references.append((owner, "direct" if direct else "alias"))
        for match in re.finditer(r"(?<![\w$])play(?![\w$])", masked_body):
            before = masked_body[: match.start()]
            after = masked_body[match.end() :]
            direct = bool(re.search(r"\.\s*$", before)) and bool(
                re.match(r"\s*\(", after)
            )
            play_references.append((owner, "direct" if direct else "alias"))
    if context_references != [("onLoad", "direct")]:
        errors.append(
            "voice policy: wx.createInnerAudioContext must have exactly one owner, onLoad; "
            f"found {context_references}"
        )
    if play_references != [("playSpeechChunk", "direct")]:
        errors.append(
            "voice policy: audio play must have exactly one owner, playSpeechChunk; "
            f"found {play_references}"
        )

    callers_by_target: dict[str, list[tuple[str, str]]] = {}
    for caller, calls in calls_by_method.items():
        for target, arguments in calls:
            callers_by_target.setdefault(target, []).append((caller, arguments))

    expected_speak_text_callers = {
        "playWelcomePrompt",
        "speakAssistantReply",
        "speakNavigationPrompt",
    }
    actual_speak_text_callers = {
        caller for caller, _ in callers_by_target.get("speakText", [])
    }
    if actual_speak_text_callers != expected_speak_text_callers:
        errors.append(
            "voice policy: playWelcomePrompt, speakAssistantReply, and "
            "speakNavigationPrompt must be the direct speakText callers; found "
            f"{sorted(actual_speak_text_callers)}"
        )
    for owner in sorted(expected_speak_text_callers):
        count = sum(
            target == "speakText" for target, _ in calls_by_method.get(owner, [])
        )
        if count != 1:
            errors.append(
                f"voice policy: {owner} must directly call speakText exactly once; found {count}"
            )

    speak_text_synthesis = sum(
        target == "synthesizeCurrentSpeechChunk"
        for target, _ in calls_by_method.get("speakText", [])
    )
    if speak_text_synthesis != 1:
        errors.append(
            "voice policy: speakText must enter the unified synthesis queue through "
            "synthesizeCurrentSpeechChunk exactly once"
        )


def reachable(
    graph: dict[str, set[str]],
    root: str,
    target: str,
) -> tuple[bool, list[str]]:
    pending = [(root, [root])]
    visited: set[str] = set()
    while pending:
        current, chain = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        if current == target:
            return True, chain
        for child in sorted(graph.get(current, set()), reverse=True):
            pending.append((child, [*chain, child]))
    return False, []


def validate_recorder_call_graph(
    methods: dict[str, tuple[str, str]],
    calls_by_method: dict[str, list[tuple[str, str]]],
    errors: list[str],
) -> None:
    wxml_path = MINIPROGRAM_ROOT / NAVIGATION_WXML_RELATIVE
    try:
        wxml = wxml_path.read_text(encoding="utf-8-sig")
    except Exception as error:
        errors.append(f"voice policy: cannot inspect navigation WXML: {error}")
        return
    visible_wxml = re.sub(r"<!--[\s\S]*?-->", "", wxml)
    event_bindings: list[tuple[str, str, str]] = []
    event_pattern = re.compile(
        r"\b((?:capture-)?(?:bind|catch)(?::|-)?[A-Za-z][A-Za-z0-9_-]*)"
        r"\s*=\s*['\"]([A-Za-z_$][\w$]*)['\"]"
    )
    for tag_match in re.finditer(
        r"<([A-Za-z][\w-]*)\b([^<>]*)>", visible_wxml, re.S
    ):
        tag = tag_match.group(1)
        attributes = tag_match.group(2)
        for event_match in event_pattern.finditer(attributes):
            event_bindings.append((tag, event_match.group(1), event_match.group(2)))
    event_handlers = [handler for _, _, handler in event_bindings]

    graph = {
        owner: {target for target, _ in calls if target in methods}
        for owner, calls in calls_by_method.items()
    }
    recording_bindings: list[tuple[str, str, str]] = []
    for binding in event_bindings:
        handler = binding[2]
        reaches, _ = reachable(graph, handler, "startRecorderForSession")
        if reaches:
            recording_bindings.append(binding)
    expected_recording_bindings = Counter(
        {
            ("button", "bindtap", "toggleVoiceInput"): 1,
            ("button", "bindtap", "toggleAgentVoiceInput"): 1,
        }
    )
    if Counter(recording_bindings) != expected_recording_bindings:
        errors.append(
            "voice policy: recorder call graph must begin at exactly two explicit WXML "
            "buttons (toggleVoiceInput and toggleAgentVoiceInput); found "
            f"{recording_bindings}"
        )

    for handler, mode in EXPLICIT_RECORD_BUTTONS.items():
        if event_handlers.count(handler) != 1:
            errors.append(
                f"voice policy: explicit recorder button {handler} must appear once in WXML"
            )
        calls = [
            arguments
            for target, arguments in calls_by_method.get(handler, [])
            if target == "startVoiceRecognition"
        ]
        if len(calls) != 1 or not re.fullmatch(
            rf"\s*['\"]{mode}['\"]\s*", calls[0]
        ):
            errors.append(
                f"voice policy: {handler} must directly call startVoiceRecognition('{mode}')"
            )

    callers = {
        target: {
            owner
            for owner, calls in calls_by_method.items()
            if any(called == target for called, _ in calls)
        }
        for target in (
            "startVoiceRecognition",
            "requestRecordPermission",
            "startRecorderForSession",
        )
    }
    if callers["startVoiceRecognition"] != set(EXPLICIT_RECORD_BUTTONS):
        errors.append(
            "voice policy: startVoiceRecognition may be called only by the two explicit "
            f"WXML buttons; found {sorted(callers['startVoiceRecognition'])}"
        )
    if callers["requestRecordPermission"] != {"startVoiceRecognition"}:
        errors.append(
            "voice policy: requestRecordPermission must be called only by "
            f"startVoiceRecognition; found {sorted(callers['requestRecordPermission'])}"
        )
    request_calls = sum(
        target == "startRecorderForSession"
        for target, _ in calls_by_method.get("requestRecordPermission", [])
    )
    if callers["startRecorderForSession"] != {"requestRecordPermission"} or request_calls != 2:
        errors.append(
            "voice policy: startRecorderForSession must be reached only from the two "
            "permission outcomes in requestRecordPermission"
        )

    for root in ("onLoad", "onShow", "openAgentAssistant", "playWelcomePrompt"):
        reaches, chain = reachable(graph, root, "startRecorderForSession")
        if reaches:
            errors.append(
                f"voice policy: {' -> '.join(chain)} reaches recorder without an explicit button"
            )


def require_pattern(
    owner: str,
    methods: dict[str, tuple[str, str]],
    pattern: str,
    message: str,
    errors: list[str],
    *,
    use_raw: bool = False,
) -> None:
    body, masked = methods.get(owner, ("", ""))
    inspected = body if use_raw else masked
    if re.search(pattern, inspected, re.S) is None:
        errors.append(f"voice policy: {message}")


def validate_single_flight_drain(
    source: str,
    methods: dict[str, tuple[str, str]],
    errors: list[str],
) -> None:
    for token, message in (
        ("currentVoiceSession: null", "currentVoiceSession state is required"),
        ("recordState: 'idle'", "recordState must initialize to idle"),
        ("voiceDrainActive: false", "voiceDrainActive must initialize false"),
    ):
        if token not in source:
            errors.append(f"voice policy: {message}")

    start_requirements = (
        (r"const\s+activeSession\s*=\s*this\.currentVoiceSession\s*;", "currentVoiceSession must gate new recognition", False),
        (r"this\.data\.voiceDrainActive", "voiceDrainActive must block a new recognition session", False),
        (r"this\.data\.recordState\s*===\s*['\"]starting['\"]", "recordState starting must block a new recognition session", True),
        (r"this\.data\.recordState\s*===\s*['\"]stopping['\"]", "recordState stopping must block a new recognition session", True),
        (r"awaitingTerminal\s*:\s*false", "awaitingTerminal must initialize for every voice session", False),
        (r"stopRequested\s*:\s*false", "stopRequested must initialize for every voice session", False),
    )
    for pattern, message, use_raw in start_requirements:
        require_pattern(
            "startVoiceRecognition", methods, pattern, message, errors, use_raw=use_raw
        )

    start_masked = methods.get("startVoiceRecognition", ("", ""))[1]
    session_creation = start_masked.find("const session")
    guard_positions = [
        start_masked.find(token)
        for token in (
            "this.voiceManagerTainted",
            "this.data.voiceRecognitionTainted",
            "this.data.voiceDrainActive",
            "this.data.recordState",
        )
    ]
    if (
        session_creation < 0
        or any(position < 0 or position > session_creation for position in guard_positions)
    ):
        errors.append(
            "voice policy: taint, voiceDrainActive, and recordState guards must run "
            "before creating a voice session"
        )

    drain_requirements = (
        (r"if\s*\(\s*session\.awaitingTerminal\s*\)\s*return\s*;", "awaitingTerminal must make drain single-flight", False),
        (r"session\.awaitingTerminal\s*=\s*true\s*;", "awaitingTerminal must be set before native stop", False),
        (r"recordState\s*:\s*['\"]stopping['\"]", "recordState must enter stopping during drain", True),
        (r"voiceDrainActive\s*:\s*true", "voiceDrainActive must remain true during drain", False),
        (r"if\s*\(\s*session\.stopRequested\s*\)\s*return\s*;", "stopRequested must make native stop single-flight", False),
        (r"session\.stopRequested\s*=\s*true\s*;", "stopRequested must be set before native stop", False),
    )
    for pattern, message, use_raw in drain_requirements:
        require_pattern(
            "beginVoiceSessionDrain", methods, pattern, message, errors, use_raw=use_raw
        )

    drain_masked = methods.get("beginVoiceSessionDrain", ("", ""))[1]
    ordered_patterns = (
        r"if\s*\(\s*session\.awaitingTerminal\s*\)\s*return",
        r"session\.awaitingTerminal\s*=\s*true",
        r"this\.armVoiceSessionTerminalTimer\s*\(",
        r"if\s*\(\s*session\.stopRequested\s*\)\s*return",
        r"session\.stopRequested\s*=\s*true",
        r"manager\.stop\s*\(",
    )
    ordered_positions = []
    for pattern in ordered_patterns:
        match = re.search(pattern, drain_masked)
        ordered_positions.append(match.start() if match else -1)
    if any(position < 0 for position in ordered_positions):
        errors.append(
            "voice policy: awaitingTerminal and stopRequested require a guard before assignment"
        )
    elif not (
        ordered_positions[0] < ordered_positions[1]
        and ordered_positions[3] < ordered_positions[4]
    ):
        errors.append(
            "voice policy: awaitingTerminal and stopRequested require a guard before assignment"
        )
    elif ordered_positions != sorted(ordered_positions):
        errors.append(
            "voice policy: awaitingTerminal, the 5s timer, and stopRequested must be "
            "armed before manager.stop"
        )

    arm_body, arm_masked = methods.get("armVoiceSessionTerminalTimer", ("", ""))
    if len(re.findall(r"\bthis\.taintVoiceRecognition\s*\(\s*session\s*\)", arm_masked)) != 1:
        errors.append(
            "voice policy: the missing-terminal timeout must taint the recognition manager"
        )
    if re.search(r"\}\s*,\s*5000\s*\)\s*;", arm_masked) is None:
        errors.append("voice policy: recognition terminal timeout must be exactly 5000 ms")
    require_pattern(
        "taintVoiceRecognition",
        methods,
        r"this\.voiceManagerTainted\s*=\s*true\s*;",
        "taintVoiceRecognition must permanently taint the page manager",
        errors,
    )

    detach_masked = methods.get("detachVoiceManagerCallbacks", ("", ""))[1]
    exact_detach = re.compile(
        r"if\s*\(\s*manager\s*\[\s*name\s*\]\s*===\s*"
        r"handlers\s*\[\s*name\s*\]\s*\)\s*"
        r"manager\s*\[\s*name\s*\]\s*=\s*null\s*;"
    )
    if len(exact_detach.findall(detach_masked)) != 1:
        errors.append(
            "voice policy: callback detach must use exact manager/handler identity before clearing"
        )
    computed_manager_owners = [
        owner
        for owner, (_, masked_body) in methods.items()
        for _ in re.finditer(r"\bmanager\s*\[", masked_body)
    ]
    if computed_manager_owners != [
        "detachVoiceManagerCallbacks",
        "detachVoiceManagerCallbacks",
    ]:
        errors.append(
            "voice policy: computed manager access is allowed only for exact identity detach; "
            f"found {computed_manager_owners}"
        )


def validate_voice_policy(errors: list[str]) -> None:
    navigation_path = MINIPROGRAM_ROOT / NAVIGATION_RELATIVE
    try:
        source = navigation_path.read_text(encoding="utf-8-sig")
        methods = extract_page_methods(source)
    except Exception as error:
        errors.append(f"voice policy: cannot inspect navigation page functions: {error}")
        return

    required = {
        "onLoad",
        "onShow",
        "openAgentAssistant",
        "playWelcomePrompt",
        "getWechatSIPlugin",
        "bindVoiceManagerSession",
        "startRecorderForSession",
        "requestRecordPermission",
        "startVoiceRecognition",
        "speakText",
        "synthesizeCurrentSpeechChunk",
        "playSpeechChunk",
        "speakAssistantReply",
        "speakNavigationPrompt",
        "armVoiceSessionTerminalTimer",
        "taintVoiceRecognition",
        "beginVoiceSessionDrain",
        "detachVoiceManagerCallbacks",
    }
    missing = sorted(required - methods.keys())
    if missing:
        errors.append(f"voice policy: missing required page function(s): {', '.join(missing)}")
        return

    calls_by_method = {
        owner: extract_this_calls(body, masked_body)
        for owner, (body, masked_body) in methods.items()
    }
    plugin_getter_callers = Counter(
        owner
        for owner, calls in calls_by_method.items()
        for target, _ in calls
        if target == "getWechatSIPlugin"
    )
    expected_plugin_getter_callers = Counter(
        {"startRecorderForSession": 1, "synthesizeCurrentSpeechChunk": 1}
    )
    if plugin_getter_callers != expected_plugin_getter_callers:
        errors.append(
            "voice policy: WechatSI capability object getter may be called only by the "
            "reviewed recorder and synthesis functions; found "
            f"{dict(plugin_getter_callers)}"
        )
    validate_page_reference_safety(methods, errors)
    validate_voice_primitives(source, methods, errors)
    validate_recognition_callbacks(methods, errors)
    validate_speech_architecture(methods, calls_by_method, errors)
    validate_recorder_call_graph(methods, calls_by_method, errors)
    validate_single_flight_drain(source, methods, errors)


def validate_assets(files: list[Path], errors: list[str]) -> int:
    map_dir = MINIPROGRAM_ROOT / "assets" / "floor-maps"
    audio_dir = MINIPROGRAM_ROOT / "assets" / "audio"
    if audio_dir.exists():
        errors.append("local audio: miniprogram/assets/audio must not exist")

    map_files = (
        sorted(path for path in map_dir.glob("*") if path.is_file())
        if map_dir.is_dir()
        else []
    )
    map_names = {path.name for path in map_files}
    if map_names != EXPECTED_MAPS:
        errors.append(
            f"floor-map set mismatch: expected {sorted(EXPECTED_MAPS)}, got {sorted(map_names)}"
        )

    allowed_assets = {map_dir / name for name in EXPECTED_MAPS}
    actual_assets = {
        path
        for path in files
        if "assets" in path.relative_to(MINIPROGRAM_ROOT).parts
    }
    extras = sorted(actual_assets - allowed_assets)
    if extras:
        errors.append(
            "unexpected production assets (local audio and audit/legacy artifacts are forbidden): "
            + ", ".join(str(path.relative_to(MINIPROGRAM_ROOT)) for path in extras)
        )

    for path in map_files:
        try:
            if path.stat().st_size > MAP_LIMIT_BYTES:
                errors.append(
                    f"map exceeds 90 KiB: {path.name} ({path.stat().st_size} B)"
                )
            with Image.open(path) as image:
                image.load()
                if image.format != "JPEG":
                    errors.append(f"map is not JPEG: {path.name} ({image.format})")
                if image.width < 800:
                    errors.append(f"map width is below 800 px: {path.name} ({image.width})")
                if image.width > 960:
                    errors.append(f"map width exceeds 960 px: {path.name} ({image.width})")
                if image.mode != "RGB":
                    errors.append(f"map is not RGB: {path.name} ({image.mode})")
        except Exception as error:
            errors.append(f"cannot fully decode map {path.name}: {error}")
    return sum(path.stat().st_size for path in map_files)


def validate_package_sources(files: Iterable[Path], errors: list[str]) -> None:
    asset_reference_pattern = re.compile(r"/assets/[A-Za-z0-9_./-]+")
    audio_reference_pattern = re.compile(
        r"\.(?:aac|amr|caf|flac|m4a|mp2|mp3|mpeg|ogg|opus|wav|webm|wma)"
        r"(?:\b|[?'\"/])",
        re.I,
    )
    expected_primitive_counts = {
        "requirePlugin": 2,
        "getRecordRecognitionManager": 2,
        "textToSpeech": 2,
        "createInnerAudioContext": 1,
        "play": 1,
    }
    primitive_sites = {primitive: [] for primitive in expected_primitive_counts}
    for path in files:
        relative = path.relative_to(MINIPROGRAM_ROOT)
        relative_posix = relative.as_posix()
        lower_name = path.name.casefold()
        suffix = path.suffix.casefold()
        if suffix in AUDIO_EXTENSIONS:
            errors.append(f"local audio file is forbidden in miniprogram: {relative_posix}")
        if suffix in FORBIDDEN_FILE_EXTENSIONS:
            errors.append(f"forbidden file type in package: {relative_posix}")
        if "audit" in lower_name or re.search(r"(?:^|[-_])route(?:[-_]|\d)", lower_name):
            errors.append(f"audit or legacy route artifact in package: {relative_posix}")
        if suffix not in TEXT_EXTENSIONS:
            continue
        try:
            source = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as error:
            errors.append(f"production text is not UTF-8: {relative_posix}: {error}")
            continue

        for identifier in OLD_LOCAL_AUDIO_IDENTIFIERS:
            if identifier in source:
                errors.append(
                    "local audio identifier is forbidden in production source: "
                    f"{identifier} in {relative_posix}"
                )
        if audio_reference_pattern.search(source):
            errors.append(f"local audio reference is forbidden in {relative_posix}")

        if suffix == ".js":
            masked_source = mask_javascript(source)
            for label, pattern in FORBIDDEN_RAW_JS_PATTERNS.items():
                if pattern.search(source):
                    errors.append(f"voice policy: forbidden {label} in {relative_posix}")
            for label, pattern in FORBIDDEN_DYNAMIC_JS_PATTERNS.items():
                if pattern.search(masked_source):
                    errors.append(
                        f"voice policy: forbidden dynamic {label} in {relative_posix}"
                    )
            for match in DANGEROUS_COMPUTED_MEMBER.finditer(source):
                errors.append(
                    "voice policy: dynamic/computed voice member access is forbidden in "
                    f"{relative_posix}: {match.group(0)}"
                )
            for match in DANGEROUS_COMPUTED_CONCAT.finditer(source):
                literal_parts = [
                    part.group(2)
                    for part in re.finditer(r"(['\"])(.*?)\1", match.group(0))
                ]
                if "".join(literal_parts) in DANGEROUS_VOICE_MEMBER_NAMES:
                    errors.append(
                        "voice policy: dynamic/computed string concatenation is forbidden "
                        f"in {relative_posix}: {match.group(0)}"
                    )
            for match in DANGEROUS_DYNAMIC_OBJECT_ACCESS.finditer(masked_source):
                errors.append(
                    "voice policy: dynamic/computed access on wx/plugin/audio voice objects "
                    f"is forbidden in {relative_posix}: {match.group(0).strip()}"
                )
            for primitive, sites in primitive_sites.items():
                sites.extend(
                    relative_posix
                    for _ in re.finditer(
                        rf"(?<![\w$]){re.escape(primitive)}(?![\w$])",
                        masked_source,
                    )
                )

        for label, pattern in FORBIDDEN_SOURCE_PATTERNS.items():
            if pattern.search(source):
                errors.append(f"forbidden {label} in {relative_posix}")
        for reference in asset_reference_pattern.findall(source):
            clean = reference.split("?", 1)[0]
            target = (MINIPROGRAM_ROOT / clean.lstrip("/")).resolve()
            try:
                target.relative_to(MINIPROGRAM_ROOT.resolve())
            except ValueError:
                errors.append(
                    f"asset reference escapes package in {relative_posix}: {reference}"
                )
                continue
            if not target.is_file():
                errors.append(
                    f"missing local asset referenced by {relative_posix}: {reference}"
                )

    for primitive, sites in primitive_sites.items():
        expected_count = expected_primitive_counts[primitive]
        if Counter(sites) != Counter({NAVIGATION_RELATIVE: expected_count}):
            errors.append(
                f"voice policy: {primitive} must occur exactly {expected_count} time(s) in "
                f"{NAVIGATION_RELATIVE} "
                f"and nowhere else; found {sites}"
            )


def package_sizes(files: list[Path]) -> dict[str, object]:
    sizes = {path: path.stat().st_size for path in files}
    map_dir = MINIPROGRAM_ROOT / "assets" / "floor-maps"
    audio_files = [path for path in files if path.suffix.casefold() in AUDIO_EXTENSIONS]
    maps = sum(size for path, size in sizes.items() if map_dir in path.parents)
    audio = sum(sizes[path] for path in audio_files)
    total = sum(sizes.values())
    largest = max(sizes, key=sizes.get) if sizes else None
    return {
        "total": total,
        "maps": maps,
        "audio": audio,
        "audioCount": len(audio_files),
        "code": total - maps - audio,
        "largest": largest,
        "largestSize": sizes.get(largest, 0),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--candidate",
        action="store_true",
        help="run automated candidate checks without authorizing patient release",
    )
    mode.add_argument(
        "--release",
        action="store_true",
        help="run the strict fail-closed patient-release gate (default)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    candidate_mode = bool(args.candidate)
    errors: list[str] = []
    if not MINIPROGRAM_ROOT.is_dir():
        label = "Candidate" if candidate_mode else "Patient release"
        print(f"{label} verification failed: missing {MINIPROGRAM_ROOT}", file=sys.stderr)
        return 1

    files = all_files(MINIPROGRAM_ROOT)
    sizes = package_sizes(files)
    if sizes["total"] >= PACKAGE_LIMIT_BYTES:
        errors.append(
            f"package must be strictly below {PACKAGE_LIMIT_BYTES:,} B; "
            f"got {sizes['total']:,} B"
        )
    validate_json_files(errors)
    validate_wechat_si_manifest(errors)
    validate_package_sources(files, errors)
    validate_voice_policy(errors)
    decoded_map_bytes = validate_assets(files, errors)
    if decoded_map_bytes != sizes["maps"]:
        errors.append("map category size does not match decoded production map set")
    if sizes["audio"] != 0 or sizes["audioCount"] != 0:
        errors.append(
            "local audio package invariant failed: expected 0 B (0 files), got "
            f"{sizes['audio']:,} B ({sizes['audioCount']} files)"
        )

    route_turn_gate_passed = run_gate(
        [
            sys.executable,
            "scripts/check-route-turn-quality.py",
            "--expected-review-status",
            "pending" if candidate_mode else "approved",
        ],
        "route-turn quality gate",
        errors,
    )

    node = shutil.which("node")
    if not node:
        errors.append("Node.js executable not found; syntax and route gates did not run")
    else:
        run_gate(
            [node, "scripts/build-web-bundle.js", "--check", "web-demo/navigation.bundle.js"],
            "browser bundle freshness gate",
            errors,
        )
        run_gate([node, "scripts/check-syntax.js"], "syntax gate", errors)
        run_gate([node, "scripts/check-routes.js"], "route gate", errors)

    if not candidate_mode:
        validate_patient_release_authorization(errors)

    print(
        "Offline mini-program candidate verification"
        if candidate_mode
        else "Offline mini-program patient release verification"
    )
    print(
        f"- package total: {format_bytes(sizes['total'])} "
        f"(limit < {PACKAGE_LIMIT_BYTES:,} B)"
    )
    print(f"- code: {format_bytes(sizes['code'])}")
    print(f"- maps: {format_bytes(sizes['maps'])} ({len(EXPECTED_MAPS)} files)")
    print(f"- audio: {sizes['audio']:,} B ({sizes['audioCount']} files)")
    if sizes["largest"] is not None:
        relative = sizes["largest"].relative_to(MINIPROGRAM_ROOT).as_posix()
        print(f"- largest file: {relative} ({sizes['largestSize']:,} B)")
    if route_turn_gate_passed:
        print("route-turn quality gate: passed")
    if errors:
        label = "Candidate" if candidate_mode else "Patient release"
        print(f"{label} verification failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    if candidate_mode:
        print(
            "CANDIDATE PASS - NOT RELEASE AUTHORIZATION: package, zero local audio, "
            "WechatSI architecture, privacy boundaries, syntax, and automated route "
            "gates are valid."
        )
    else:
        print(
            "PASS: patient-release package, review registries, device acceptance, "
            "syntax, and route gates are valid."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
