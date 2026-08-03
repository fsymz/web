const assert = require('node:assert/strict');
const test = require('node:test');

const { createNavigationPageHarness } = require('./helpers/navigation-page-harness.js');

test('onHide stops recording, the single audio context, timers, and simulation', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page, recorder, timers } = harness;

  page.toggleVoiceInput();
  harness.authorizeCalls[0].success();
  recorder.emitStart();
  page.speakText('正在播报');
  harness.textToSpeechCalls[0].success({ retcode: 0, filename: 'https://tts/active' });
  page.stepAnimationTimer = setInterval(() => {}, 50);
  const welcomeTimer = page.welcomePromptTimer;
  const stepTimer = page.stepAnimationTimer;
  let engineStopCalls = 0;
  const originalStop = page.simNavEngine.stop.bind(page.simNavEngine);
  page.simNavEngine.stop = () => {
    engineStopCalls += 1;
    return originalStop();
  };

  const audioStops = harness.speechAudio.stopCalls;
  page.onHide();

  assert.equal(recorder.stopCalls, 1);
  assert.equal(page.pageIsActive, false);
  assert.equal(page.data.recordState, 'stopping');
  assert.equal(page.data.voiceDrainActive, true);
  assert.equal(typeof recorder.onStop, 'function', 'hide must retain the exact terminal handler while draining');
  recorder.emitRecognize({ result: '隐藏后迟到的局部结果' });
  assert.equal(page.data.voicePartialText, '');
  assert.ok(harness.speechAudio.stopCalls > audioStops);
  assert.equal(harness.speechAudio.activeEndedHandlerCount, 0);
  assert.equal(harness.speechAudio.activeErrorHandlerCount, 0);
  assert.equal(page.welcomePromptTimer, null);
  assert.equal(page.stepAnimationTimer, null);
  assert.ok(timers.clearedTimeouts.includes(welcomeTimer));
  assert.ok(timers.clearedIntervals.includes(stepTimer));
  assert.equal(engineStopCalls, 1);

  recorder.emitStop({ result: '隐藏后迟到的最终结果' });
  assert.equal(page.data.recordState, 'idle');
  assert.equal(page.data.voiceDrainActive, false);
  assert.equal(page.data.inputVal2, '');
  for (const name of ['onStart', 'onStop', 'onError', 'onRecognize']) {
    assert.equal(recorder[name], null);
  }
});

test('onUnload destroys page-owned resources at most once', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const engine = harness.page.simNavEngine;
  let engineDestroyCalls = 0;
  const originalDestroy = engine.destroy.bind(engine);
  engine.destroy = () => {
    engineDestroyCalls += 1;
    return originalDestroy();
  };

  harness.page.onUnload();
  harness.page.onUnload();

  assert.equal(harness.audioContextCreateCalls, 1);
  assert.equal(harness.speechAudio.destroyCalls, 1);
  assert.equal(engineDestroyCalls, 1);
});

test('hide drains through the owned handlers and onShow requires a fresh explicit microphone tap', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page, recorder } = harness;

  page.toggleVoiceInput();
  harness.authorizeCalls[0].success();
  recorder.emitStart();
  const formerCallbacks = recorder.getSessionCallbacks(0);
  page.onHide();

  const hiddenSnapshot = {
    destination: page.data.inputVal2,
    messages: page.data.agentMessages.slice(),
    voicePartialText: page.data.voicePartialText,
    voiceTip: page.data.voiceTip
  };
  assert.equal(page.data.recordState, 'stopping');
  for (const name of ['onStart', 'onStop', 'onError', 'onRecognize']) {
    assert.equal(recorder[name], formerCallbacks[name]);
  }

  recorder.emitRecognize({ result: '迟到的局部结果' });
  assert.equal(page.data.inputVal2, hiddenSnapshot.destination);
  assert.deepEqual(page.data.agentMessages, hiddenSnapshot.messages);
  assert.equal(page.data.voicePartialText, hiddenSnapshot.voicePartialText);
  assert.equal(page.data.voiceTip, hiddenSnapshot.voiceTip);

  page.onShow();
  page.toggleVoiceInput();
  assert.equal(harness.authorizeCalls.length, 1, 'onShow and a blocked tap cannot supersede an active drain');
  assert.equal(recorder.startCalls.length, 1);

  recorder.emitStop({ result: '急诊科' });
  assert.equal(page.data.recordState, 'idle');
  assert.equal(page.data.inputVal2, hiddenSnapshot.destination);
  assert.equal(harness.authorizeCalls.length, 1, 'drain completion must not auto-start');

  page.toggleVoiceInput();
  harness.authorizeCalls[1].success();
  const freshCallbacks = recorder.getSessionCallbacks(1);
  for (const name of ['onStart', 'onStop', 'onError', 'onRecognize']) {
    assert.equal(typeof recorder[name], 'function');
    assert.equal(freshCallbacks[name], recorder[name]);
    assert.notEqual(freshCallbacks[name], formerCallbacks[name]);
  }
  assert.equal(recorder.startCalls.length, 2);
  assert.equal(harness.getRecorderManagerCalls, 1);
});

test('unload detaches recorder callbacks and stale snapshots cannot mutate page data', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page, recorder } = harness;

  page.toggleAgentVoiceInput();
  harness.authorizeCalls[0].success();
  recorder.emitStart();
  const formerCallbacks = recorder.getSessionCallbacks(0);
  page.onUnload();
  const unloadedSnapshot = {
    destination: page.data.inputVal2,
    messages: page.data.agentMessages.slice(),
    voicePartialText: page.data.voicePartialText,
    voiceTip: page.data.voiceTip
  };

  assert.equal(recorder.stopCalls, 1, 'unload must stop the active manager exactly once');
  formerCallbacks.onRecognize({ result: '迟到的导诊结果' });
  formerCallbacks.onStop({ result: '我要去急诊科' });
  formerCallbacks.onError({ errMsg: 'late unload error' });

  assert.equal(recorder.onStart, null);
  assert.equal(recorder.onStop, null);
  assert.equal(recorder.onError, null);
  assert.equal(recorder.onRecognize, null);
  assert.equal(page.data.inputVal2, unloadedSnapshot.destination);
  assert.deepEqual(page.data.agentMessages, unloadedSnapshot.messages);
  assert.equal(page.data.voicePartialText, unloadedSnapshot.voicePartialText);
  assert.equal(page.data.voiceTip, unloadedSnapshot.voiceTip);
});

test('pause cancels TTS and resume force-replays the current semantic instruction without moving', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page } = harness;
  const engine = {
    currentLeg: { instruction: '继续直行约11米' },
    pause() {
      return true;
    },
    resume() {
      return true;
    },
    stop() {},
    destroy() {}
  };
  page.simNavEngine = engine;
  page.lastSpokenStepKey = 'leg-0-point-2';
  page.setData({
    navInstruction: '继续直行约11米',
    currentImageIndex: 0,
    navProgress: 37,
    navDistanceText: '23m',
    markerX: 42.5,
    markerY: 61.25,
    markerAngle: 90,
    stepMode: false
  });
  page.speakText('继续直行约11米');
  harness.textToSpeechCalls[0].success({ retcode: 0, filename: 'https://tts/current-step' });
  const playCountBeforePause = harness.speechAudio.playCalls.length;
  const spatialSnapshot = {
    currentImageIndex: page.data.currentImageIndex,
    navProgress: page.data.navProgress,
    navDistanceText: page.data.navDistanceText,
    markerX: page.data.markerX,
    markerY: page.data.markerY,
    markerAngle: page.data.markerAngle
  };

  page.pauseNavigation();
  assert.equal(page.speechQueue.length, 0);
  assert.equal(harness.speechAudio.activeEndedHandlerCount, 0);
  assert.ok(harness.speechAudio.stopCalls >= 1);

  const promptCalls = [];
  page.speakNavigationPrompt = (...args) => promptCalls.push(args);
  page.resumeNavigation();

  assert.deepEqual(promptCalls, [[
    '继续直行约11米',
    'leg-0-point-2',
    { force: true }
  ]]);
  assert.equal(harness.speechAudio.playCalls.length, playCountBeforePause);
  for (const [name, value] of Object.entries(spatialSnapshot)) {
    assert.equal(page.data[name], value);
  }
});

test('co-located preview and start show the RoutePlan message without starting navigation', t => {
  const harness = createNavigationPageHarness({ autoLoad: false, pluginUnavailable: true });
  t.after(() => harness.restore());
  const { page } = harness;
  page.data.inputVal1 = '病理科';
  page.data.inputVal2 = '重症医学科';
  let stepStartCalls = 0;
  page.startStepLeg = () => {
    stepStartCalls += 1;
  };

  page.updateRoutePreview();
  assert.equal(page.data.previewMessage, '当前位置与目的地位于同一区域，请根据现场标识确认');
  assert.equal(Object.hasOwn(page.data, 'previewLegs'), false);
  assert.equal(page.data.currentPreviewLeg, null);
  assert.deepEqual(page.previewPlanLegs, []);

  page.startNavigation();
  assert.equal(page.data.previewMessage, '当前位置与目的地位于同一区域，请根据现场标识确认');
  assert.equal(harness.toastCalls.at(-1).title, '当前位置与目的地位于同一区域，请根据现场标识确认');
  assert.equal(page.data.showNavigationPopup, false);
  assert.equal(page.currentPlan, null);
  assert.equal(stepStartCalls, 0);
  assert.equal(harness.timers.intervals.size, 0);
});
