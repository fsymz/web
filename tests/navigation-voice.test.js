const assert = require('node:assert/strict');
const test = require('node:test');

const { createNavigationPageHarness } = require('./helpers/navigation-page-harness.js');

test('opening the AI panel never requests permission or starts recording', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());

  assert.equal(harness.requirePluginCalls.length, 0, 'page load must keep WechatSI lazy');
  harness.page.openAgentAssistant();

  assert.equal(harness.page.data.showAgentPanel, true);
  assert.equal(harness.authorizeCalls.length, 0);
  assert.equal(harness.recorder.startCalls.length, 0);
});

test('consecutive typed and recognized assistant replies each use WechatSI TTS', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());

  const completeLatestSpeech = () => {
    const request = harness.textToSpeechCalls.at(-1);
    request.success({
      retcode: 0,
      filename: `https://tts/${harness.textToSpeechCalls.length}`
    });
    harness.speechAudio.emitEnded();
  };

  for (const message of ['我要去急诊科', '急诊科在哪里']) {
    harness.page.setData({ agentInput: message });
    harness.page.sendAgentMessage();
    assert.equal(harness.textToSpeechCalls.length, harness.speechAudio.playCalls.length + 1);
    completeLatestSpeech();
  }

  for (const message of ['我要去儿科门诊', '儿科门诊在哪里']) {
    harness.page.applyAgentVoiceText(message);
    assert.equal(harness.textToSpeechCalls.length, harness.speechAudio.playCalls.length + 1);
    completeLatestSpeech();
  }

  assert.equal(harness.textToSpeechCalls.length, 4);
  assert.equal(harness.speechAudio.playCalls.length, 4);
  assert.equal(harness.getRecorderManagerCalls, 0);
  assert.equal(harness.recorder.startCalls.length, 0);
});

test('a microphone click drives idle through starting, recording, stopping, and idle', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page, recorder } = harness;

  assert.equal(page.data.recordState, 'idle');
  page.toggleVoiceInput();
  assert.equal(page.data.recordState, 'starting');
  assert.equal(harness.authorizeCalls.length, 1);

  harness.authorizeCalls[0].success();
  assert.equal(recorder.startCalls.length, 1);
  recorder.emitStart();
  assert.equal(page.data.recordState, 'recording');

  page.toggleVoiceInput();
  assert.equal(page.data.recordState, 'stopping');
  assert.equal(recorder.stopCalls, 1);
  recorder.emitStop({ result: '急诊科' });

  assert.equal(page.data.recordState, 'idle');
  assert.equal(page.data.inputVal2, '急诊科');
});

test('binds recognition callbacks as properties before recording starts', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());

  harness.page.toggleVoiceInput();
  harness.authorizeCalls[0].success();

  const manager = harness.recorder;
  assert.equal(manager.startCalls.length, 1);
  const callbacksAtStart = manager.getSessionCallbacks(0);
  for (const name of ['onStart', 'onStop', 'onError', 'onRecognize']) {
    assert.equal(typeof callbacksAtStart[name], 'function');
    assert.equal(callbacksAtStart[name], manager[name]);
  }
});

test('interim recognition updates only partial text and final stop applies the result', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page, recorder } = harness;
  const appliedDestinations = [];
  const appliedAgentText = [];
  page.applyVoiceText = text => appliedDestinations.push(text);
  page.applyAgentVoiceText = text => appliedAgentText.push(text);

  page.toggleVoiceInput();
  harness.authorizeCalls[0].success();
  recorder.emitStart();
  const beforeInterim = JSON.parse(JSON.stringify(page.data));
  recorder.emitRecognize({ result: '急诊' });

  assert.equal(page.data.voicePartialText, '急诊');
  const afterInterim = JSON.parse(JSON.stringify(page.data));
  afterInterim.voicePartialText = beforeInterim.voicePartialText;
  assert.deepEqual(afterInterim, beforeInterim);
  assert.deepEqual(appliedDestinations, []);
  assert.deepEqual(appliedAgentText, []);

  recorder.emitStop({ result: '急诊科' });
  assert.equal(page.data.voicePartialText, '');
  assert.deepEqual(appliedDestinations, ['急诊科']);
  assert.deepEqual(appliedAgentText, []);
});

test('permission denial offers settings and recovery never auto-starts recording', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page, recorder } = harness;

  page.toggleVoiceInput();
  harness.authorizeCalls[0].fail({ errMsg: 'authorize:fail auth deny' });

  assert.equal(page.data.recordState, 'idle');
  assert.equal(page.data.recordPermissionDenied, true);
  assert.match(page.data.voiceTip, /开启麦克风权限/);

  page.openRecordPermissionSettings();
  assert.equal(harness.openSettingCalls.length, 1);
  harness.openSettingCalls[0].success({ authSetting: { 'scope.record': true } });

  assert.equal(page.data.recordPermissionDenied, false);
  assert.match(page.data.voiceTip, /再次点击/);
  assert.equal(recorder.startCalls.length, 0);
});

test('late authorization after cancellation cannot start the recorder', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page, recorder } = harness;

  page.openAgentAssistant();
  page.toggleAgentVoiceInput();
  assert.equal(page.data.recordState, 'starting');
  page.closeAgentAssistant();
  harness.authorizeCalls[0].success();

  assert.equal(page.data.recordState, 'idle');
  assert.equal(recorder.startCalls.length, 0);
  assert.equal(page.data.showAgentPanel, false);
});

test('discarded recording drains the current manager before another explicit recording can start', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page, recorder } = harness;

  page.openAgentAssistant();
  page.toggleAgentVoiceInput();
  harness.authorizeCalls[0].success();
  recorder.emitStart();
  page.closeAgentAssistant();
  const snapshot = {
    messages: page.data.agentMessages.slice(),
    agentInput: page.data.agentInput,
    destination: page.data.inputVal2,
    voicePartialText: page.data.voicePartialText
  };

  assert.equal(page.data.recordState, 'stopping');
  assert.equal(page.data.voiceDrainActive, true);
  assert.ok(page.currentVoiceSession, 'drain must retain ownership until a terminal callback');
  assert.equal(page.currentVoiceSession.finalPolicy, 'discard');
  assert.equal(recorder.stopCalls, 1);

  page.toggleVoiceInput();
  assert.equal(harness.authorizeCalls.length, 1);
  assert.equal(recorder.startCalls.length, 1);

  // WechatSI dispatches a late event through the manager's current property.
  recorder.emitRecognize({ result: '迟到的局部结果' });
  assert.equal(page.data.voicePartialText, snapshot.voicePartialText);
  recorder.emitStop({ result: '我要去急诊科' });

  assert.deepEqual(page.data.agentMessages, snapshot.messages);
  assert.equal(page.data.agentInput, snapshot.agentInput);
  assert.equal(page.data.inputVal2, snapshot.destination);
  assert.equal(page.data.recordState, 'idle');
  assert.equal(page.data.voiceDrainActive, false);
  assert.equal(page.currentVoiceSession, null);
  assert.equal(harness.toastCalls.length, 0);
  for (const name of ['onStart', 'onStop', 'onError', 'onRecognize']) {
    assert.equal(recorder[name], null);
  }
  assert.equal(harness.authorizeCalls.length, 1, 'terminal drain must not auto-start');
  assert.equal(recorder.startCalls.length, 1, 'terminal drain must not auto-start');

  page.toggleVoiceInput();
  assert.equal(harness.authorizeCalls.length, 2, 'the patient must explicitly click again');
  harness.authorizeCalls[1].success();
  assert.equal(recorder.startCalls.length, 2);
});

test('a discarded recording error releases silently and never applies old text', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page, recorder } = harness;

  page.openAgentAssistant();
  page.toggleAgentVoiceInput();
  harness.authorizeCalls[0].success();
  recorder.emitStart();
  const snapshot = {
    messages: page.data.agentMessages.slice(),
    agentInput: page.data.agentInput,
    destination: page.data.inputVal2
  };

  page.setNavigationMode({ currentTarget: { dataset: { mode: 'loop' } } });
  assert.equal(page.data.recordState, 'stopping');
  recorder.emitError({ errMsg: 'late error' });

  assert.equal(recorder.stopCalls, 1);
  assert.equal(page.data.recordState, 'idle');
  assert.equal(page.data.voiceDrainActive, false);
  assert.deepEqual(page.data.agentMessages, snapshot.messages);
  assert.equal(page.data.agentInput, snapshot.agentInput);
  assert.equal(page.data.inputVal2, snapshot.destination);
  assert.equal(harness.toastCalls.length, 0);
  assert.equal(harness.authorizeCalls.length, 1);
  assert.equal(recorder.startCalls.length, 1);
});

test('a five-second drain timeout taints recognition for the page but leaves text navigation usable', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page, recorder, timers } = harness;

  page.toggleVoiceInput();
  harness.authorizeCalls[0].success();
  recorder.emitStart();
  page.stopVoiceRecognition({ discardResult: true, reason: 'switch-mode' });

  const timeoutEntry = [...timers.timeouts.entries()].find(([, timer]) => timer.delay === 5000);
  assert.ok(timeoutEntry, 'draining a started manager must arm the terminal watchdog');
  assert.equal(timers.runTimeout(timeoutEntry[0]), true);

  assert.equal(page.data.voiceRecognitionTainted, true);
  assert.equal(page.data.voiceDrainActive, false);
  assert.equal(page.data.recordState, 'idle');
  assert.equal(page.currentVoiceSession, null);
  for (const name of ['onStart', 'onStop', 'onError', 'onRecognize']) {
    assert.equal(recorder[name], null);
  }

  page.toggleVoiceInput();
  page.toggleAgentVoiceInput();
  assert.equal(harness.authorizeCalls.length, 1);
  assert.equal(recorder.startCalls.length, 1);

  page.onInput2({ detail: { value: '急诊科' } });
  assert.equal(page.data.inputVal2, '急诊科');
});

test('a synchronous recorder stop failure taints recognition after only one stop attempt', t => {
  const harness = createNavigationPageHarness({ recorderStopThrows: new Error('stop failed') });
  t.after(() => harness.restore());
  const { page, recorder } = harness;

  page.toggleVoiceInput();
  harness.authorizeCalls[0].success();
  recorder.emitStart();
  page.stopVoiceRecognition({ discardResult: true, reason: 'switch-mode' });

  assert.equal(recorder.stopCalls, 1);
  assert.equal(page.data.voiceRecognitionTainted, true);
  assert.equal(page.data.voiceDrainActive, false);
  page.toggleVoiceInput();
  assert.equal(harness.authorizeCalls.length, 1);
  assert.equal(recorder.startCalls.length, 1);
});

test('the other voice mode cannot interrupt an active recording while the current mode can stop it', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page, recorder } = harness;

  page.toggleVoiceInput();
  harness.authorizeCalls[0].success();
  recorder.emitStart();

  page.toggleAgentVoiceInput();
  assert.equal(page.data.recordState, 'recording');
  assert.equal(page.data.voiceMode, 'destination');
  assert.equal(recorder.stopCalls, 0);
  assert.equal(harness.authorizeCalls.length, 1);

  page.toggleVoiceInput();
  assert.equal(page.data.recordState, 'stopping');
  assert.equal(recorder.stopCalls, 1);
});

test('terminal cleanup clears only callbacks still owned by that recognition session', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page, recorder } = harness;

  page.toggleVoiceInput();
  harness.authorizeCalls[0].success();
  recorder.emitStart();
  const callbacks = recorder.getSessionCallbacks(0);
  page.toggleVoiceInput();
  const replacement = () => {};
  recorder.onRecognize = replacement;

  callbacks.onStop({ result: '急诊科' });

  assert.equal(recorder.onRecognize, replacement);
  assert.equal(recorder.onStart, null);
  assert.equal(recorder.onStop, null);
  assert.equal(recorder.onError, null);
});

test('automatic welcome speaks exactly once without touching the recorder and fails text-only', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());

  const welcomeTimer = harness.page.welcomePromptTimer;
  assert.ok(welcomeTimer);
  assert.equal(harness.timers.runTimeout(welcomeTimer), true);
  assert.equal(harness.textToSpeechCalls.length, 1);
  assert.match(harness.textToSpeechCalls[0].content, /医院导航系统/);
  assert.equal(harness.page.lastSpokenStepKey, 'welcome');
  assert.equal(harness.page.speechQueueSource, 'welcome');
  assert.equal(harness.getRecorderManagerCalls, 0);
  assert.equal(harness.recorder.startCalls.length, 0);

  harness.page.playWelcomePrompt();
  assert.equal(harness.textToSpeechCalls.length, 1);

  harness.textToSpeechCalls[0].fail({ errMsg: 'network unavailable' });
  assert.match(harness.page.data.speechStatusText, /查看屏幕文字/);
  assert.equal(harness.speechAudio.playCalls.length, 0);
  harness.page.onInput2({ detail: { value: '急诊科' } });
  assert.equal(harness.page.data.inputVal2, '急诊科');
});

test('assistant TTS failure keeps visible text and never falls back to packaged audio', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());

  harness.page.setData({ navInstruction: '请继续直行' });
  harness.page.speakAssistantReply('请继续直行');
  assert.equal(harness.textToSpeechCalls.length, 1);
  const request = harness.textToSpeechCalls[0];
  assert.equal(request.content, '请继续直行');
  assert.equal(Object.hasOwn(request, 'tts'), false);

  request.fail({ errMsg: 'network unavailable' });
  assert.equal(harness.speechAudio.playCalls.length, 0);
  assert.equal(harness.page.data.navInstruction, '请继续直行');
  assert.match(harness.page.data.speechStatusText, /查看屏幕文字/);
});
