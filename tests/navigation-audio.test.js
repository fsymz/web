const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const { createNavigationPageHarness } = require('./helpers/navigation-page-harness.js');

test('creates exactly one page-scoped audio context', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());

  assert.equal(harness.audioContextCreateCalls, 1);
  assert.equal(harness.page.speechAudioContext, harness.speechAudio);
});

test('splits empty, punctuated, and unpunctuated TTS text at 50 characters', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  assert.deepEqual(harness.page.splitSpeechText('', 50), []);

  const originalPunctuated = '请直行。'.repeat(18);
  const punctuated = harness.page.splitSpeechText(originalPunctuated, 50);
  assert.ok(punctuated.length > 1);
  assert.ok(punctuated.every(chunk => chunk.length > 0 && chunk.length <= 50));
  assert.equal(punctuated.join(''), originalPunctuated);

  const hardSplit = harness.page.splitSpeechText('甲'.repeat(101), 50);
  assert.deepEqual(hardSplit.map(chunk => chunk.length), [50, 50, 1]);
  assert.equal(hardSplit.join(''), '甲'.repeat(101));
});

test('speakText rejects empty and duplicate keyed prompts while force and unkeyed prompts remain playable', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());

  assert.deepEqual(harness.page.speakText('   '), { ok: false, reason: 'empty' });
  const first = harness.page.speakText('第一条提示', { key: 'step-1', source: 'navigation' });
  assert.equal(first.ok, true);
  assert.equal(harness.page.speechQueueSource, 'navigation');
  assert.equal(harness.textToSpeechCalls.length, 1);

  assert.deepEqual(
    harness.page.speakText('重复提示', { key: 'step-1' }),
    { ok: false, reason: 'duplicate' }
  );
  assert.equal(harness.textToSpeechCalls.length, 1);

  assert.equal(harness.page.speakText('强制重播', { key: 'step-1', force: true }).ok, true);
  assert.equal(harness.textToSpeechCalls.length, 2);
  assert.equal(harness.page.speakText('无键回复').ok, true);
  assert.equal(harness.textToSpeechCalls.length, 3);
});

test('plays TTS chunks sequentially through one audio context', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  harness.page.speakText('甲'.repeat(50) + '乙');
  assert.equal(harness.textToSpeechCalls.length, 1);

  harness.textToSpeechCalls[0].success({ retcode: 0, filename: 'https://tts/one' });
  assert.deepEqual(harness.speechAudio.playCalls, ['https://tts/one']);
  assert.equal(harness.textToSpeechCalls.length, 1);

  harness.speechAudio.emitEnded();
  assert.equal(harness.textToSpeechCalls.length, 2);
  harness.textToSpeechCalls[1].success({ retcode: 0, filename: 'https://tts/two' });
  assert.deepEqual(harness.speechAudio.playCalls, ['https://tts/one', 'https://tts/two']);
  harness.speechAudio.emitEnded();
  assert.equal(harness.page.speechQueue.length, 0);
});

test('rejects retcode nonzero and keeps navigation text usable', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  harness.page.setData({ navInstruction: '请继续直行' });
  harness.page.speakText('请继续直行');
  harness.textToSpeechCalls[0].success({ retcode: -1, filename: 'https://tts/bad' });

  assert.equal(harness.speechAudio.playCalls.length, 0);
  assert.match(harness.page.data.speechStatusText, /查看屏幕文字/);
  assert.equal(harness.page.data.navInstruction, '请继续直行');
});

test('new speech rejects a stale synthesis success', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  harness.page.speakText('旧合成');
  const staleSynthesis = harness.textToSpeechCalls[0];
  harness.page.speakText('新合成');

  staleSynthesis.success({ retcode: 0, filename: 'https://tts/stale' });
  assert.equal(harness.speechAudio.playCalls.length, 0);
  harness.textToSpeechCalls[1].success({ retcode: 0, filename: 'https://tts/current' });
  assert.deepEqual(harness.speechAudio.playCalls, ['https://tts/current']);
});

test('new speech rejects an old playback ended event', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  harness.page.speakText('旧提示');
  harness.textToSpeechCalls[0].success({ retcode: 0, filename: 'https://tts/old' });
  assert.deepEqual(harness.speechAudio.playCalls, ['https://tts/old']);

  harness.page.speakText('新提示');
  const requestCountBeforeStaleEnded = harness.textToSpeechCalls.length;
  harness.speechAudio.emitEndedFromRegistration(0);
  assert.equal(harness.textToSpeechCalls.length, requestCountBeforeStaleEnded);

  harness.textToSpeechCalls[1].success({ retcode: 0, filename: 'https://tts/new' });
  assert.deepEqual(harness.speechAudio.playCalls, ['https://tts/old', 'https://tts/new']);
});

test('synthesis and playback watchdogs fail text-only and clear their timers', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  harness.page.speakText('合成超时');
  assert.equal(harness.timers.timeouts.get(harness.page.speechSynthesisTimer).delay, 15000);
  assert.equal(harness.timers.runTimeout(harness.page.speechSynthesisTimer), true);
  assert.match(harness.page.data.speechStatusText, /暂不可用|超时/);
  assert.equal(harness.page.speechSynthesisTimer, null);
  assert.equal(harness.page.speechQueue.length, 0);

  harness.page.speakText('播放超时');
  harness.textToSpeechCalls.at(-1).success({ retcode: 0, filename: 'https://tts/timeout' });
  assert.equal(harness.timers.timeouts.get(harness.page.speechPlaybackTimer).delay, 60000);
  assert.equal(harness.timers.runTimeout(harness.page.speechPlaybackTimer), true);
  assert.ok(harness.speechAudio.stopCalls >= 1);
  assert.equal(harness.page.speechPlaybackTimer, null);
  assert.equal(harness.page.speechQueue.length, 0);
});

test('page hide stops audio and invalidates pending synthesis', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  harness.page.speakText('迟到提示');
  const pending = harness.textToSpeechCalls[0];
  harness.page.onHide();

  pending.success({ retcode: 0, filename: 'https://tts/late' });
  assert.equal(harness.speechAudio.playCalls.length, 0);
  assert.ok(harness.speechAudio.stopCalls >= 1);
});

test('strictly rejects absent, mistyped, or blank synthesis results', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  for (const result of [
    undefined,
    null,
    {},
    { retcode: null, filename: 'https://tts/null' },
    { retcode: '', filename: 'https://tts/empty-code' },
    { retcode: '0', filename: 'https://tts/string-code' },
    { retcode: 0, filename: '' },
    { retcode: 0, filename: '   ' }
  ]) {
    harness.page.speakText('结果校验', { force: true });
    harness.textToSpeechCalls.at(-1).success(result);
    assert.equal(harness.speechAudio.playCalls.length, 0);
    assert.match(harness.page.data.speechStatusText, /查看屏幕文字/);
  }
});

test('asynchronous synthesis failure clears the queue without playing audio', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  harness.page.speakText('网络失败');
  harness.textToSpeechCalls[0].fail({ errMsg: 'network unavailable' });

  assert.equal(harness.page.speechSynthesisTimer, null);
  assert.equal(harness.page.speechQueue.length, 0);
  assert.equal(harness.speechAudio.playCalls.length, 0);
  assert.match(harness.page.data.speechStatusText, /网络语音暂不可用|查看屏幕文字/);
});

test('current playback error clears timers, handlers, and the queue', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  harness.page.speakText('播放失败');
  harness.textToSpeechCalls[0].success({ retcode: 0, filename: 'https://tts/error' });
  harness.speechAudio.emitError({ errMsg: 'playback failed' });

  assert.equal(harness.page.speechPlaybackTimer, null);
  assert.equal(harness.page.speechQueue.length, 0);
  assert.equal(harness.speechAudio.activeEndedHandlerCount, 0);
  assert.equal(harness.speechAudio.activeErrorHandlerCount, 0);
  assert.match(harness.page.data.speechStatusText, /播放失败|查看屏幕文字/);
});

test('missing plugin and synchronous synthesis exceptions fail text-only', t => {
  const missing = createNavigationPageHarness({ pluginUnavailable: true });
  t.after(() => missing.restore());
  assert.doesNotThrow(() => missing.page.speakText('插件缺失'));
  assert.match(missing.page.data.speechStatusText, /查看屏幕文字/);
  assert.equal(missing.page.speechQueue.length, 0);

  const throwing = createNavigationPageHarness({
    textToSpeechThrows: new Error('sync synthesis failure')
  });
  t.after(() => throwing.restore());
  assert.doesNotThrow(() => throwing.page.speakText('同步异常'));
  assert.equal(throwing.textToSpeechCalls.length, 0);
  assert.match(throwing.page.data.speechStatusText, /查看屏幕文字/);
  assert.equal(throwing.page.speechSynthesisTimer, null);
  assert.equal(throwing.page.speechQueue.length, 0);
});

for (const mode of ['audioSrcThrows', 'audioPlayThrows']) {
  test(`player ${mode} exception fails text-only and removes handlers`, t => {
    const harness = createNavigationPageHarness({
      [mode]: new Error(`sync ${mode} failure`)
    });
    t.after(() => harness.restore());
    harness.page.speakText('同步播放异常');
    assert.doesNotThrow(() => {
      harness.textToSpeechCalls[0].success({
        retcode: 0,
        filename: 'https://tts/throws'
      });
    });

    assert.match(harness.page.data.speechStatusText, /查看屏幕文字/);
    assert.equal(harness.page.speechSynthesisTimer, null);
    assert.equal(harness.page.speechPlaybackTimer, null);
    assert.equal(harness.page.speechQueue.length, 0);
    assert.equal(harness.page.activeSpeechPlayback, null);
    assert.equal(harness.speechAudio.activeEndedHandlerCount, 0);
    assert.equal(harness.speechAudio.activeErrorHandlerCount, 0);
  });
}

test('stale playback errors cannot overwrite the current speech state', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  harness.page.speakText('旧播放');
  harness.textToSpeechCalls[0].success({ retcode: 0, filename: 'https://tts/old' });
  harness.page.speakText('新播放');
  const statusBeforeStaleError = harness.page.data.speechStatusText;

  harness.speechAudio.emitErrorFromRegistration(0, { errMsg: 'late old error' });
  assert.equal(harness.page.data.speechStatusText, statusBeforeStaleError);
  harness.textToSpeechCalls.at(-1).success({ retcode: 0, filename: 'https://tts/new' });
  assert.deepEqual(harness.speechAudio.playCalls, ['https://tts/old', 'https://tts/new']);
});

test('page unload destroys the single audio context once', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  harness.page.onUnload();
  harness.page.onUnload();

  assert.equal(harness.audioContextCreateCalls, 1);
  assert.equal(harness.speechAudio.destroyCalls, 1);
});

test('a delayed TTS success cannot play after the page is hidden', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());

  harness.page.speakText('请向左转');
  const request = harness.textToSpeechCalls[0];
  harness.page.onHide();
  request.success({ retcode: 0, filename: 'https://example.test/late' });

  assert.deepEqual(harness.speechAudio.playCalls, []);
});

test('navigation prompts deduplicate by semantic key while stop and replay retain the current prompt', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());

  const first = harness.page.speakNavigationPrompt('前方左转', 'leg-0:step-2');
  assert.equal(first.ok, true);
  assert.equal(harness.page.speechQueueSource, 'navigation');
  assert.deepEqual(harness.page.currentNavigationPrompt, {
    text: '前方左转',
    key: 'leg-0:step-2'
  });
  assert.equal(harness.textToSpeechCalls.length, 1);

  const duplicate = harness.page.speakNavigationPrompt('前方左转', 'leg-0:step-2');
  assert.deepEqual(duplicate, { ok: false, reason: 'duplicate' });
  assert.equal(harness.textToSpeechCalls.length, 1);

  harness.page.stopAudio();
  assert.deepEqual(harness.page.currentNavigationPrompt, {
    text: '前方左转',
    key: 'leg-0:step-2'
  });
  const replay = harness.page.replayNavigation();
  assert.equal(replay.ok, true);
  assert.equal(harness.textToSpeechCalls.length, 2);
  assert.equal(harness.textToSpeechCalls[1].content, '前方左转');
});

test('pause and resume cancel only speech playback and force the same prompt without moving the route', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page } = harness;
  const engineCalls = { pause: 0, resume: 0 };
  page.simNavEngine = {
    pause() {
      engineCalls.pause += 1;
      return true;
    },
    resume() {
      engineCalls.resume += 1;
      return true;
    },
    currentLeg: { instruction: '前方右转' }
  };
  page.stepNavLegIndex = 1;
  page.stepNavPointIndex = 3;
  page.setData({
    currentImageIndex: 1,
    navInstruction: '前方右转',
    navProgress: 47,
    navDistanceText: '25m',
    navEtaText: '22秒',
    markerX: 42,
    markerY: 61,
    markerAngle: 180,
    stepMode: false
  });
  page.speakNavigationPrompt('前方右转', 'leg-1:entry');
  harness.textToSpeechCalls[0].success({ retcode: 0, filename: 'https://tts/current' });
  const snapshot = () => ({
    legIndex: page.stepNavLegIndex,
    pointIndex: page.stepNavPointIndex,
    currentImageIndex: page.data.currentImageIndex,
    instruction: page.data.navInstruction,
    progress: page.data.navProgress,
    distance: page.data.navDistanceText,
    eta: page.data.navEtaText,
    markerX: page.data.markerX,
    markerY: page.data.markerY,
    markerAngle: page.data.markerAngle
  });
  const before = snapshot();
  const stopsBeforePause = harness.speechAudio.stopCalls;

  page.pauseNavigation();
  assert.deepEqual(snapshot(), before);
  assert.equal(harness.speechAudio.stopCalls, stopsBeforePause + 1);
  assert.deepEqual(page.currentNavigationPrompt, {
    text: '前方右转',
    key: 'leg-1:entry'
  });

  page.resumeNavigation();
  assert.deepEqual(snapshot(), before);
  assert.deepEqual(engineCalls, { pause: 1, resume: 1 });
  assert.equal(harness.textToSpeechCalls.length, 2);
  assert.equal(harness.textToSpeechCalls[1].content, '前方右转');
});

test('loop leg entry and transfer use TTS without cancelling the transfer at the next leg boundary', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page } = harness;
  const firstLeg = {
    title: '儿科门诊到电梯',
    floor: '1楼',
    instruction: '从儿科门诊直行前往一号电梯',
    transferInstruction: '已到达一号电梯，请乘坐电梯前往二楼',
    kind: 'fromLocation',
    departmentName: '儿科门诊',
    points: [[10, 10], [20, 20]],
    imageSize: [100, 100],
    hasRoutePath: true
  };
  const secondLeg = {
    title: '电梯到检验科',
    floor: '2楼',
    instruction: '出电梯后右转前往检验科',
    kind: 'toDestination',
    departmentName: '检验科',
    points: [[20, 20], [30, 30]],
    imageSize: [100, 100],
    hasRoutePath: true
  };
  page.currentPlan = { legs: [firstLeg, secondLeg] };

  page.handleNavLegChange({
    leg: firstLeg,
    legIndex: 0,
    remainingDistanceText: '60m',
    etaText: '50秒',
    progress: 0
  });
  assert.equal(harness.textToSpeechCalls.length, 1);
  assert.equal(harness.textToSpeechCalls[0].content, firstLeg.instruction);
  assert.equal(harness.speechAudio.playCalls.length, 0);

  page.handleNavLegComplete({
    hasNext: true,
    leg: firstLeg,
    legIndex: 0,
    isLoopRestart: false
  });
  assert.equal(harness.textToSpeechCalls.length, 2);
  const transferRequest = harness.textToSpeechCalls[1];
  assert.match(transferRequest.content, /乘坐电梯前往二楼/);
  assert.match(transferRequest.content, /出电梯后右转前往检验科/);
  const transferToken = page.speechPlaybackToken;

  const nextLegTimer = setTimeout(() => page.handleNavLegChange({
    leg: secondLeg,
    legIndex: 1,
    remainingDistanceText: '35m',
    etaText: '30秒',
    progress: 50
  }), 900);
  assert.equal(harness.timers.runTimeout(nextLegTimer), true);

  assert.equal(harness.textToSpeechCalls.length, 2);
  assert.equal(page.speechPlaybackToken, transferToken);
  assert.equal(page.data.navInstruction, secondLeg.instruction);
  assert.deepEqual(page.currentNavigationPrompt, {
    text: secondLeg.instruction,
    key: 'leg-1:entry'
  });
  transferRequest.success({ retcode: 0, filename: 'https://tts/transfer' });
  assert.deepEqual(harness.speechAudio.playCalls, ['https://tts/transfer']);
});

test('all finish paths announce arrival once through the shared navigation prompt', t => {
  const harness = createNavigationPageHarness();
  t.after(() => harness.restore());
  const { page } = harness;

  page.handleNavFinish();
  assert.equal(harness.textToSpeechCalls.length, 1);
  assert.equal(harness.textToSpeechCalls[0].content, '已到达目的地');
  assert.deepEqual(page.currentNavigationPrompt, {
    text: '已到达目的地',
    key: 'arrival'
  });

  page.handleNavFinish();
  assert.equal(harness.textToSpeechCalls.length, 1);

  const leg = {
    arrivalName: '目的地',
    points: [[0, 0], [10, 0]],
    imageSize: [100, 100],
    hasRoutePath: true,
    routePath: { semanticPointIndexes: [0, 1] }
  };
  page.currentPlan = { legs: [leg] };
  page.stepNavLegIndex = 0;
  page.stepNavPointIndex = 1;
  page.setData({ stepMode: true, stepAnimating: false });
  page.stepNavigation();
  assert.equal(harness.textToSpeechCalls.length, 1);
});

test('plugin failure explicitly directs patients to screen text without claiming local speech', t => {
  const harness = createNavigationPageHarness({ pluginUnavailable: true });
  t.after(() => harness.restore());

  harness.page.getWechatSIPlugin();
  assert.match(harness.page.data.voiceTip, /查看屏幕文字/);
  assert.doesNotMatch(harness.page.data.voiceTip, /本地/);

  harness.page.speakAssistantReply('请继续直行');
  assert.match(harness.page.data.voiceTip, /查看屏幕文字/);
  assert.doesNotMatch(harness.page.data.voiceTip, /本地/);
  assert.match(harness.page.data.speechStatusText, /查看屏幕文字/);
  assert.equal(harness.speechAudio.playCalls.length, 0);
});

test('speech status is bound in both the home view and navigation overlay', () => {
  const wxmlPath = path.join(
    __dirname,
    '..',
    'miniprogram',
    'pages',
    'navigation',
    'navigation.wxml'
  );
  const wxml = fs.readFileSync(wxmlPath, 'utf8');
  assert.equal((wxml.match(/>\{\{speechStatusText\}\}<\/view>/g) || []).length, 2);
});
