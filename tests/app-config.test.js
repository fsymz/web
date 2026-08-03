const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const { createNavigationPageHarness } = require('./helpers/navigation-page-harness.js');

const projectRoot = path.resolve(__dirname, '..');

function readJson(relativePath) {
  return JSON.parse(fs.readFileSync(path.join(projectRoot, relativePath), 'utf8'));
}

test('app config declares the single approved WechatSI plugin source', () => {
  const appConfig = readJson('miniprogram/app.json');

  assert.deepEqual(appConfig.plugins, {
    WechatSI: {
      version: '0.3.5',
      provider: 'wx069ba97219f66d99'
    }
  });
  assert.equal(Object.hasOwn(appConfig, 'permission'), false);
  assert.equal(fs.existsSync(path.join(projectRoot, 'config/wechat-si.plugin.fragment.json')), false);
});

test('plugin-free page load and manual navigation remain usable', t => {
  const harness = createNavigationPageHarness({ pluginUnavailable: true });
  t.after(() => harness.restore());
  harness.page.data.inputVal1 = '急诊科';
  harness.page.data.inputVal2 = '挂号缴费';

  assert.doesNotThrow(() => harness.page.startNavigation());
  assert.equal(harness.page.data.showNavigationPopup, true);
});

test('voice controls expose recovery and disable every unsafe recognition transition', () => {
  const wxml = fs.readFileSync(
    path.join(projectRoot, 'miniprogram/pages/navigation/navigation.wxml'),
    'utf8'
  );

  assert.match(wxml, /recordPermissionDenied/);
  assert.match(wxml, /bindtap="openRecordPermissionSettings"/);
  const disabledExpressions = [...wxml.matchAll(/disabled="\{\{([^"\r\n]+)\}\}"/g)]
    .map(match => match[1].trim())
    .filter(expression => expression.includes('recordState'));
  assert.deepEqual(disabledExpressions, [
    "voiceRecognitionTainted || voiceDrainActive || recordState === 'starting' || recordState === 'stopping' || (recordState === 'recording' && voiceMode !== 'destination')",
    "voiceRecognitionTainted || voiceDrainActive || recordState === 'starting' || recordState === 'stopping' || (recordState === 'recording' && voiceMode !== 'agent')"
  ]);
});
