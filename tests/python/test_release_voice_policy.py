from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NAVIGATION_RELATIVE = Path("miniprogram/pages/navigation/navigation.js")


def copy_release_fixture(target: Path) -> Path:
    """Copy every input used by verify-release so failures cannot be missing-file passes."""

    for directory in ("config", "miniprogram", "scripts", "web-demo"):
        shutil.copytree(
            PROJECT_ROOT / directory,
            target / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copy2(PROJECT_ROOT / "project.config.json", target / "project.config.json")
    # Mutation cases isolate the static release policy. The production verifier
    # is exercised separately with the real external gates.
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


def replace_once(source: str, old: str, new: str) -> str:
    assert source.count(old) == 1, (old, source.count(old))
    return source.replace(old, new, 1)


def inject_after(source: str, marker: str, statement: str) -> str:
    return replace_once(source, marker, marker + "\n    " + statement)


def mutate_navigation(project: Path, mutator: Callable[[str], str]) -> None:
    path = project / NAVIGATION_RELATIVE
    path.write_text(mutator(path.read_text(encoding="utf-8")), encoding="utf-8")


def add_method(source: str, name: str, body: str) -> str:
    marker = "  onLoad: function() {"
    return replace_once(
        source,
        marker,
        f"  {name}: function() {{\n    {body}\n  }},\n\n{marker}",
    )


def extra_require_plugin(source: str) -> str:
    return add_method(source, "escapePlugin", "return requirePlugin('WechatSI');")


def aliased_require_plugin(source: str) -> str:
    return add_method(
        source,
        "escapePluginAlias",
        "const loadPlugin = requirePlugin; return loadPlugin('WechatSI');",
    )


def computed_plugin_loader(source: str) -> str:
    return add_method(source, "escapePlugin", "return globalThis['require' + 'Plugin']('WechatSI');")


def aliased_manager_getter(source: str) -> str:
    marker = "      const manager = this.voiceManager || plugin.getRecordRecognitionManager();"
    injected = (
        "      const getter = plugin.getRecordRecognitionManager;\n"
        "      getter.call(plugin);\n"
        f"{marker}"
    )
    return replace_once(source, marker, injected)


def computed_manager_getter(source: str) -> str:
    marker = "      const manager = this.voiceManager || plugin.getRecordRecognitionManager();"
    return replace_once(
        source,
        marker,
        "      plugin['getRecordRecognitionManager']();\n" + marker,
    )


def aliased_tts(source: str) -> str:
    marker = "      plugin.textToSpeech({"
    return replace_once(
        source,
        marker,
        "      const synthesize = plugin.textToSpeech;\n"
        "      synthesize.call(plugin, { content: chunk });\n"
        + marker,
    )


def concatenated_tts_member(source: str) -> str:
    marker = "      plugin.textToSpeech({"
    return replace_once(
        source,
        marker,
        "      plugin['text' + 'ToSpeech']({ content: chunk });\n" + marker,
    )


def extra_audio_context(source: str) -> str:
    return inject_after(source, "  onShow: function() {", "wx.createInnerAudioContext();")


def aliased_audio_context_factory(source: str) -> str:
    return inject_after(
        source,
        "  onShow: function() {",
        "const makeAudio = wx.createInnerAudioContext; makeAudio();",
    )


def play_outside_owner(source: str) -> str:
    return add_method(source, "escapePlayback", "this.speechAudioContext.play();")


def aliased_play(source: str) -> str:
    marker = "      context.play();"
    return replace_once(
        source,
        marker,
        "      const playAgain = context.play;\n      playAgain.call(context);\n" + marker,
    )


def tts_outside_owner(source: str) -> str:
    return add_method(
        source,
        "escapeSynthesis",
        "const plugin = this.getWechatSIPlugin(); plugin.textToSpeech({ content: 'x' });",
    )


def welcome_bypasses_funnel(source: str) -> str:
    return replace_once(
        source,
        "    return this.speakText(WELCOME_PROMPT, {",
        "    return this.synthesizeCurrentSpeechChunk(1, 0) || ({",
    )


def assistant_bypasses_funnel(source: str) -> str:
    return replace_once(
        source,
        "    return this.speakText(replyText, { source: 'assistant' });",
        "    return this.synthesizeCurrentSpeechChunk(1, 0);",
    )


def navigation_bypasses_funnel(source: str) -> str:
    return replace_once(
        source,
        "    return this.speakText(text, {",
        "    return this.synthesizeCurrentSpeechChunk(1, 0) || ({",
    )


def speak_text_bypasses_queue(source: str) -> str:
    return replace_once(
        source,
        "    this.synthesizeCurrentSpeechChunk(token, 0);",
        "    this.playSpeechChunk('escape', token, 0);",
    )


def recorder_from_on_load(source: str) -> str:
    return inject_after(source, "  onLoad: function() {", "this.startVoiceRecognition('destination');")


def recorder_from_on_show(source: str) -> str:
    return inject_after(source, "  onShow: function() {", "this.startVoiceRecognition('destination');")


def recorder_from_panel(source: str) -> str:
    return inject_after(source, "  openAgentAssistant: function() {", "this.startVoiceRecognition('agent');")


def recorder_from_welcome(source: str) -> str:
    return inject_after(source, "  playWelcomePrompt: function() {", "this.startVoiceRecognition('destination');")


def overwrite_callback_before_start(source: str) -> str:
    return replace_once(
        source,
        "      this.bindVoiceManagerSession(manager, session);",
        "      this.bindVoiceManagerSession(manager, session);\n"
        "      manager.onStart = null;",
    )


def remove_drain_guard(source: str) -> str:
    return replace_once(source, "      || this.data.voiceDrainActive\n", "")


def move_drain_guard_after_session_creation(source: str) -> str:
    source = replace_once(source, "      || this.data.voiceDrainActive\n", "")
    return replace_once(
        source,
        "    this.requestRecordPermission(session);",
        "    if (this.data.voiceDrainActive) return;\n"
        "    this.requestRecordPermission(session);",
    )


def remove_awaiting_terminal(source: str) -> str:
    return replace_once(source, "      awaitingTerminal: false,\n", "")


def remove_stop_requested(source: str) -> str:
    return replace_once(source, "      stopRequested: false,\n", "")


def move_awaiting_guard_after_assignment(source: str) -> str:
    source = replace_once(source, "    if (session.awaitingTerminal) return;\n", "")
    return replace_once(
        source,
        "    session.awaitingTerminal = true;",
        "    session.awaitingTerminal = true;\n"
        "    if (session.awaitingTerminal) return;",
    )


def move_stop_guard_after_assignment(source: str) -> str:
    return replace_once(
        source,
        "    this.armVoiceSessionTerminalTimer(session);\n"
        "    if (session.stopRequested) return;\n"
        "    session.stopRequested = true;\n"
        "    const manager = session.manager || this.voiceManager;",
        "    this.armVoiceSessionTerminalTimer(session);\n"
        "    session.stopRequested = true;\n"
        "    if (session.stopRequested) return;\n"
        "    const manager = session.manager || this.voiceManager;",
    )


def move_awaiting_terminal_after_native_stop(source: str) -> str:
    source = replace_once(source, "    session.awaitingTerminal = true;\n", "")
    return replace_once(
        source,
        "      manager.stop();\n    } catch (error) {\n      this.taintVoiceRecognition(session);",
        "      manager.stop();\n"
        "      session.awaitingTerminal = true;\n"
        "    } catch (error) {\n"
        "      this.taintVoiceRecognition(session);",
    )


def wrong_terminal_timeout(source: str) -> str:
    return replace_once(source, "    }, 5000);", "    }, 6000);")


def timeout_without_taint(source: str) -> str:
    return replace_once(
        source,
        "      this.taintVoiceRecognition(session);\n    }, 5000);",
        "      this.releaseVoiceSession(session, {});\n    }, 5000);",
    )


def detach_without_identity(source: str) -> str:
    return replace_once(
        source,
        "      if (manager[name] === handlers[name]) manager[name] = null;",
        "      manager[name] = null;",
    )


def dynamic_page_escape(source: str) -> str:
    return inject_after(
        source,
        "  onShow: function() {",
        "const page = this; page.startVoiceRecognition('destination');",
    )


@pytest.mark.parametrize(
    ("case_name", "mutator", "expected_message"),
    (
        ("extra-require-plugin", extra_require_plugin, "requirePlugin"),
        ("aliased-require-plugin", aliased_require_plugin, "requirePlugin"),
        ("computed-plugin-loader", computed_plugin_loader, "dynamic"),
        ("aliased-manager-getter", aliased_manager_getter, "getRecordRecognitionManager"),
        ("computed-manager-getter", computed_manager_getter, "getRecordRecognitionManager"),
        ("aliased-tts", aliased_tts, "textToSpeech"),
        ("concatenated-tts-member", concatenated_tts_member, "dynamic"),
        ("extra-audio-context", extra_audio_context, "createInnerAudioContext"),
        ("aliased-audio-context-factory", aliased_audio_context_factory, "createInnerAudioContext"),
        ("play-outside-owner", play_outside_owner, "playSpeechChunk"),
        ("aliased-play", aliased_play, "playSpeechChunk"),
        ("tts-outside-owner", tts_outside_owner, "synthesizeCurrentSpeechChunk"),
        ("welcome-bypasses-funnel", welcome_bypasses_funnel, "playWelcomePrompt"),
        ("assistant-bypasses-funnel", assistant_bypasses_funnel, "speakAssistantReply"),
        ("navigation-bypasses-funnel", navigation_bypasses_funnel, "speakNavigationPrompt"),
        ("speak-text-bypasses-queue", speak_text_bypasses_queue, "speakText"),
        ("recorder-from-on-load", recorder_from_on_load, "onLoad"),
        ("recorder-from-on-show", recorder_from_on_show, "onShow"),
        ("recorder-from-panel", recorder_from_panel, "openAgentAssistant"),
        ("recorder-from-welcome", recorder_from_welcome, "playWelcomePrompt"),
        ("overwrite-callback-before-start", overwrite_callback_before_start, "callback property"),
        ("remove-drain-guard", remove_drain_guard, "voiceDrainActive"),
        (
            "move-drain-guard-after-session",
            move_drain_guard_after_session_creation,
            "before creating a voice session",
        ),
        ("remove-awaiting-terminal", remove_awaiting_terminal, "awaitingTerminal"),
        ("remove-stop-requested", remove_stop_requested, "stopRequested"),
        (
            "move-awaiting-guard-after-assignment",
            move_awaiting_guard_after_assignment,
            "guard before assignment",
        ),
        (
            "move-stop-guard-after-assignment",
            move_stop_guard_after_assignment,
            "guard before assignment",
        ),
        (
            "move-awaiting-terminal-after-stop",
            move_awaiting_terminal_after_native_stop,
            "before manager.stop",
        ),
        ("wrong-terminal-timeout", wrong_terminal_timeout, "5000"),
        ("timeout-without-taint", timeout_without_taint, "taint"),
        ("detach-without-identity", detach_without_identity, "identity"),
        ("dynamic-page-escape", dynamic_page_escape, "Page object"),
    ),
)
def test_release_gate_rejects_voice_architecture_mutations(
    case_name: str,
    mutator: Callable[[str], str],
    expected_message: str,
    tmp_path: Path,
) -> None:
    project = copy_release_fixture(tmp_path / case_name)
    mutate_navigation(project, mutator)
    result = run_verifier(project)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 1, output
    assert expected_message.casefold() in output.casefold(), output


def test_release_gate_rejects_third_voice_button(tmp_path: Path) -> None:
    project = copy_release_fixture(tmp_path / "third-voice-button")
    navigation = project / NAVIGATION_RELATIVE
    navigation.write_text(
        add_method(
            navigation.read_text(encoding="utf-8"),
            "escapeVoiceButton",
            "this.startVoiceRecognition('destination');",
        ),
        encoding="utf-8",
    )
    wxml = project / "miniprogram/pages/navigation/navigation.wxml"
    wxml.write_text(
        wxml.read_text(encoding="utf-8")
        + '\n<button bindtap="escapeVoiceButton">escape</button>\n',
        encoding="utf-8",
    )
    result = run_verifier(project)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 1, output
    assert "two explicit" in output.casefold(), output


def test_release_gate_rejects_non_tap_voice_event(tmp_path: Path) -> None:
    project = copy_release_fixture(tmp_path / "longpress-voice-event")
    wxml = project / "miniprogram/pages/navigation/navigation.wxml"
    wxml.write_text(
        wxml.read_text(encoding="utf-8")
        + '\n<button bindlongpress="toggleVoiceInput">escape</button>\n',
        encoding="utf-8",
    )
    result = run_verifier(project)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 1, output
    assert "two explicit" in output.casefold(), output


def test_release_gate_rejects_plugin_escape_to_dynamic_helper(tmp_path: Path) -> None:
    project = copy_release_fixture(tmp_path / "dynamic-helper")
    helper = project / "miniprogram/utils/voiceEscape.js"
    helper.write_text(
        "'use strict';\n"
        "module.exports = function(p, content) {\n"
        "  return p['text' + 'ToSpeech']({ content });\n"
        "};\n",
        encoding="utf-8",
    )
    navigation = project / NAVIGATION_RELATIVE
    source = navigation.read_text(encoding="utf-8")
    marker = "    const chunk = this.speechQueue[chunkIndex];"
    assert source.count(marker) == 1
    source = source.replace(
        marker,
        marker
        + "\n    require('../../utils/voiceEscape')(plugin, chunk);",
        1,
    )
    navigation.write_text(source, encoding="utf-8")
    result = run_verifier(project)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 1, output
    assert "dynamic" in output.casefold() or "escape" in output.casefold(), output


def test_release_gate_rejects_cached_plugin_field_escape(tmp_path: Path) -> None:
    project = copy_release_fixture(tmp_path / "cached-plugin-field-escape")
    helper = project / "miniprogram/utils/voiceEscape.js"
    helper.write_text(
        "'use strict';\n"
        "module.exports = function(p, content) {\n"
        "  const k = ['text', 'ToSpeech'].join('');\n"
        "  return p[k]({ content });\n"
        "};\n",
        encoding="utf-8",
    )
    navigation = project / NAVIGATION_RELATIVE
    source = navigation.read_text(encoding="utf-8")
    marker = "  onShow: function() {"
    assert source.count(marker) == 1
    source = source.replace(
        marker,
        marker
        + "\n    require('../../utils/voiceEscape')(this.wechatSIPlugin, 'escape');",
        1,
    )
    navigation.write_text(source, encoding="utf-8")
    result = run_verifier(project)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 1, output
    assert "capability object" in output.casefold(), output


def test_release_gate_ignores_commented_voice_button(tmp_path: Path) -> None:
    project = copy_release_fixture(tmp_path / "commented-voice-button")
    wxml = project / "miniprogram/pages/navigation/navigation.wxml"
    source = wxml.read_text(encoding="utf-8")
    real_binding = 'bindtap="toggleVoiceInput"'
    assert source.count(real_binding) == 1
    source = source.replace(real_binding, 'bindtap="onSearch2"', 1)
    source += '\n<!-- <button bindtap="toggleVoiceInput">not real</button> -->\n'
    wxml.write_text(source, encoding="utf-8")
    result = run_verifier(project)
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 1, output
    assert "two explicit" in output.casefold(), output
