'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const {
  buildVoiceConfigurationEvidence,
  createSafeSmokeReport,
  finalizeRuntimeDiagnostics,
  formatSmokeFailure,
  formatSmokeSummary,
  hasRenderablePreview,
  hasSameVerifiedShaft,
  withHardTimeout
} = require('../scripts/wechat-smoke-diagnostics');

const SENTINELS = [
  'wx1234567890secretappid',
  'o6zAJs_secret_openid',
  '请带我去消控室',
  '计算机机房',
  'https://tts.invalid/private/voice.mp3'
];

function makeProjectFixture(t, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wechat-smoke-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'miniprogram'), { recursive: true });
  fs.writeFileSync(path.join(root, 'project.config.json'), JSON.stringify({
    appid: 'touristappid',
    miniprogramRoot: 'miniprogram/'
  }));
  if (options.privateAppId !== null) {
    fs.writeFileSync(path.join(root, 'project.private.config.json'), JSON.stringify({
      appid: options.privateAppId || SENTINELS[0]
    }));
  }
  fs.writeFileSync(path.join(root, 'miniprogram', 'app.json'), JSON.stringify({
    plugins: {
      WechatSI: options.plugin || {
        version: '0.3.5',
        provider: 'wx069ba97219f66d99'
      }
    }
  }));
  return root;
}

test('voice evidence exposes only the approved schema and redacts all runtime text', t => {
  const root = makeProjectFixture(t);
  const evidence = buildVoiceConfigurationEvidence(root, {
    recordState: 'idle',
    voiceMode: '',
    openid: SENTINELS[1],
    recognizedText: SENTINELS[2],
    inputVal2: SENTINELS[3],
    ttsUrl: SENTINELS[4]
  });

  assert.deepEqual(evidence.voiceConfiguration, {
    realPrivateAppIdConfigured: true,
    pluginDeclared: true,
    pluginVersion: '0.3.5',
    provider: 'wx069ba97219f66d99',
    packagedAudioFileCount: 0,
    initialRecordState: 'idle',
    manualDeviceVerificationRequired: true
  });
  assert.deepEqual(evidence.failureCategories, []);
  const serialized = JSON.stringify(evidence);
  SENTINELS.forEach(value => assert.equal(serialized.includes(value), false));
});

test('invalid private config, plugin, audio, or recording state yields fixed categories only', t => {
  const root = makeProjectFixture(t, {
    privateAppId: 'touristappid',
    plugin: { version: SENTINELS[2], provider: SENTINELS[1] }
  });
  const audioRoot = path.join(root, 'miniprogram', 'nested', 'audio');
  fs.mkdirSync(audioRoot, { recursive: true });
  ['notice.MP3', 'notice.wav', 'notice.webm'].forEach(name => {
    fs.writeFileSync(path.join(audioRoot, name), 'not audio');
  });

  const evidence = buildVoiceConfigurationEvidence(root, {
    recordState: SENTINELS[3],
    voiceMode: SENTINELS[4]
  });

  assert.deepEqual(evidence.voiceConfiguration, {
    realPrivateAppIdConfigured: false,
    pluginDeclared: false,
    pluginVersion: null,
    provider: null,
    packagedAudioFileCount: 3,
    initialRecordState: null,
    manualDeviceVerificationRequired: true
  });
  assert.deepEqual(evidence.failureCategories, [
    'initial-record-state-not-idle',
    'initial-voice-mode-not-empty',
    'packaged-audio-present',
    'plugin-declaration-invalid',
    'private-appid-missing'
  ]);
  const serialized = JSON.stringify(evidence);
  SENTINELS.forEach(value => assert.equal(serialized.includes(value), false));
});

test('missing private config is reported as a boolean and a fixed category', t => {
  const root = makeProjectFixture(t, { privateAppId: null });
  const evidence = buildVoiceConfigurationEvidence(root, {
    recordState: 'idle',
    voiceMode: ''
  });
  assert.equal(evidence.voiceConfiguration.realPrivateAppIdConfigured, false);
  assert.deepEqual(evidence.failureCategories, ['private-appid-missing']);
});

test('WechatSI is not declared valid when any additional plugin is packaged', t => {
  const root = makeProjectFixture(t);
  const appPath = path.join(root, 'miniprogram', 'app.json');
  const appConfig = JSON.parse(fs.readFileSync(appPath, 'utf8'));
  appConfig.plugins.UnapprovedPlugin = {
    version: SENTINELS[2],
    provider: SENTINELS[1]
  };
  fs.writeFileSync(appPath, JSON.stringify(appConfig));

  const evidence = buildVoiceConfigurationEvidence(root, {
    recordState: 'idle',
    voiceMode: ''
  });
  assert.equal(evidence.voiceConfiguration.pluginDeclared, false);
  assert.equal(evidence.voiceConfiguration.pluginVersion, null);
  assert.equal(evidence.voiceConfiguration.provider, null);
  assert.deepEqual(evidence.failureCategories, ['plugin-declaration-invalid']);
  SENTINELS.forEach(value => {
    assert.equal(JSON.stringify(evidence).includes(value), false);
  });
});

test('smoke report and printable summary cannot retain injected secrets or raw failures', t => {
  const root = makeProjectFixture(t);
  const report = createSafeSmokeReport({
    projectRoot: root,
    initialSnapshot: {
      recordState: 'idle',
      voiceMode: '',
      recognizedText: SENTINELS[2],
      destination: SENTINELS[3],
      ttsUrl: SENTINELS[4]
    },
    rawLaunchError: new Error(SENTINELS.join('|')),
    rawCliOutput: SENTINELS.join('|'),
    consolePayloads: [{ openid: SENTINELS[1], text: SENTINELS[2] }],
    exceptionPayloads: [{ destination: SENTINELS[3], url: SENTINELS[4] }]
  });
  const serialized = JSON.stringify(report);
  const printable = formatSmokeSummary(report);

  SENTINELS.forEach(value => {
    assert.equal(serialized.includes(value), false);
    assert.equal(printable.includes(value), false);
  });
  assert.deepEqual(report.voiceConfiguration, {
    realPrivateAppIdConfigured: true,
    pluginDeclared: true,
    pluginVersion: '0.3.5',
    provider: 'wx069ba97219f66d99',
    packagedAudioFileCount: 0,
    initialRecordState: 'idle',
    manualDeviceVerificationRequired: true
  });
  assert.equal(Object.hasOwn(report.automation.cliConnectFallback, 'cliOutput'), false);
  assert.equal(Object.hasOwn(report.automation.launch, 'error'), false);
  assert.equal(Object.hasOwn(report.automation.cliConnectFallback, 'error'), false);
  assert.equal(Object.hasOwn(report, 'consoleErrors'), false);
  assert.equal(Object.hasOwn(report, 'exceptions'), false);
  assert.equal(Object.hasOwn(report, 'error'), false);
  assert.equal(report.consoleErrorCount, 1);
  assert.equal(report.exceptionCount, 1);
  assert.equal(report.automation.launch.errorCategory, 'unexpected-smoke-failure');
});

test('a generic reLaunch timeout is classified without returning its raw text', () => {
  const timeout = 'miniProgram.reLaunch timed out after 20000 ms';
  assert.equal(formatSmokeFailure(timeout, []), 'devtools-operation-timeout');
  assert.equal(formatSmokeFailure(new Error(timeout), []), 'devtools-operation-timeout');
});

test('missing access token evidence maps to a fixed sign-in category', () => {
  const timeout = 'miniProgram.reLaunch timed out after 20000 ms';
  assert.equal(
    formatSmokeFailure(timeout, ['DevTools: access_token missing ' + SENTINELS[0]]),
    'devtools-access-token-missing'
  );
});

test('already-classified failures remain the same safe enum when rethrown', () => {
  [
    'devtools-access-token-missing',
    'devtools-operation-timeout',
    'automation-connection-failed',
    'devtools-cli-auto-failed',
    'voice-configuration-invalid',
    'smoke-assertion-failed',
    'devtools-cleanup-failed',
    'unexpected-smoke-failure'
  ].forEach(category => {
    assert.equal(formatSmokeFailure(new Error(category), []), category);
  });
});

test('late runtime errors downgrade an otherwise passed report before it is written', () => {
  const report = {
    result: 'passed',
    exitCode: 0,
    consoleErrorCount: 1,
    exceptionCount: 0,
    errorCategory: null
  };

  assert.equal(finalizeRuntimeDiagnostics(report), false);
  assert.deepEqual(report, {
    result: 'failed',
    exitCode: 1,
    consoleErrorCount: 1,
    exceptionCount: 0,
    errorCategory: 'smoke-assertion-failed'
  });

  const cleanReport = {
    result: 'passed-via-cli-connect-fallback',
    exitCode: 0,
    consoleErrorCount: 0,
    exceptionCount: 0,
    errorCategory: null
  };
  assert.equal(finalizeRuntimeDiagnostics(cleanReport), true);
  assert.equal(cleanReport.result, 'passed-via-cli-connect-fallback');
});

test('automated smoke never initiates microphone, privacy, or recognition actions', () => {
  const source = fs.readFileSync(
    path.resolve(__dirname, '..', 'scripts', 'wechat-smoke.js'),
    'utf8'
  );
  const forbidden = [
    /page\s*\.\s*tap\s*\(/,
    /\b(?:toggleVoiceInput|toggleAgentVoiceInput|startVoiceRecognition|requestRecordPermission|startRecorderForSession|openRecordPermissionSettings|applyVoiceText|applyAgentVoiceText)\b/,
    /wx\s*\.\s*authorize\s*\(/,
    /getRecordRecognitionManager\s*\(/,
    /requirePrivacyAuthorize|openPrivacyContract/
  ];

  forbidden.forEach(pattern => assert.doesNotMatch(source, pattern));
  [
    "page.tap('#voice-button')",
    "page.callMethod('toggleVoiceInput')",
    "page.callMethod('toggleAgentVoiceInput')",
    "page.callMethod('startVoiceRecognition')",
    "page.callMethod('requestRecordPermission')",
    "page.callMethod('startRecorderForSession')",
    "page.callMethod('openRecordPermissionSettings')",
    "page.callMethod('applyVoiceText')",
    "page.callMethod('applyAgentVoiceText')",
    "wx.authorize({ scope: 'scope.record' })",
    'plugin.getRecordRecognitionManager()',
    'requirePrivacyAuthorize()'
  ].forEach(mutant => {
    assert.equal(
      forbidden.some(pattern => pattern.test(mutant)),
      true,
      'forbidden automation mutation was not detected: ' + mutant
    );
  });
});

test('acceptance evidence policy forbids retaining recordings and patient voice text', () => {
  const acceptance = fs.readFileSync(
    path.resolve(__dirname, '..', 'docs', 'acceptance', 'voice-acceptance.md'),
    'utf8'
  );
  assert.match(
    acceptance,
    /任何录音、识别文字、患者输入、临时 TTS URL 均不留存，也不得作为证据/
  );
  assert.doesNotMatch(acceptance, /录音(?:仅)?存放在/);
  assert.equal(
    acceptance.split(/\r?\n/).filter(line => /^\| (?:[1-9]|1[0-2]) \|/.test(line)).length,
    12
  );
  assert.match(acceptance, /当前状态：\*\*Pending（待验收）\*\*/);
  assert.match(acceptance, /Android 与 iPhone 两台真机的全部项目都通过后/);
  assert.match(acceptance, /欢迎语音 650ms 时间窗后/);
  assert.doesNotMatch(acceptance, /欢迎语音 500ms 时间窗后/);
});

test('the outer launch guard rejects a promise that never settles', async () => {
  await assert.rejects(
    withHardTimeout(new Promise(() => {}), 5, 'automator.launch'),
    /automator\.launch timed out after 5 ms/
  );
});

test('the outer launch guard cleans up a launch that resolves after timeout', async () => {
  let resolveLaunch;
  let cleanedValue = null;
  const launchPromise = new Promise(resolve => {
    resolveLaunch = resolve;
  });

  await assert.rejects(
    withHardTimeout(launchPromise, 5, 'automator.launch', value => {
      cleanedValue = value;
    }),
    /automator\.launch timed out/
  );

  resolveLaunch('late mini program');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(cleanedValue, 'late mini program');
});

test('a compact reactive preview is valid without leaking the source leg kind', () => {
  const snapshot = {
    previewLegCount: 1,
    currentPreviewLeg: {
      floor: '1楼',
      image: '/assets/floor-maps/1F.jpg',
      lineSegments: [{ left: 10, top: 20, width: 5, angle: 0 }]
    }
  };

  assert.equal(hasRenderablePreview(snapshot, 1, '1楼'), true);
  assert.equal(Object.hasOwn(snapshot.currentPreviewLeg, 'kind'), false);
  assert.equal(hasRenderablePreview(snapshot, 2, '1楼'), false);
  assert.equal(hasRenderablePreview({
    previewLegCount: 1,
    currentPreviewLeg: {
      ...snapshot.currentPreviewLeg,
      floor: '20楼',
      image: '/assets/floor-maps/20F.jpg'
    }
  }, 1, '20楼'), false);
});

test('cross-floor runtime snapshots must retain one verified shaft on both legs', () => {
  const firstLeg = { currentLeg: { selectedElevatorShaftId: 'S2' } };
  const secondLeg = { currentLeg: { selectedElevatorShaftId: 'S2' } };

  assert.equal(hasSameVerifiedShaft(firstLeg, secondLeg), true);
  assert.equal(
    hasSameVerifiedShaft(firstLeg, { currentLeg: { selectedElevatorShaftId: 'S3' } }),
    false
  );
  assert.equal(
    hasSameVerifiedShaft(firstLeg, { currentLeg: { selectedElevatorShaftId: 'legacy-E2' } }),
    false
  );
});
