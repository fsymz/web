'use strict';

const fs = require('node:fs');
const path = require('node:path');

const EXPECTED_PLUGIN_VERSION = '0.3.5';
const EXPECTED_PLUGIN_PROVIDER = 'wx069ba97219f66d99';
const AUDIO_EXTENSIONS = new Set([
  '.aac', '.amr', '.caf', '.flac', '.m4a', '.mp2', '.mp3',
  '.mpeg', '.ogg', '.opus', '.wav', '.webm', '.wma'
]);
const ERROR_CATEGORIES = new Set([
  'devtools-access-token-missing',
  'devtools-operation-timeout',
  'automation-connection-failed',
  'devtools-cli-auto-failed',
  'voice-configuration-invalid',
  'smoke-assertion-failed',
  'devtools-cleanup-failed',
  'unexpected-smoke-failure'
]);

function readJsonOrNull(filePath) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch (error) {
    return null;
  }
}

function resolveMiniProgramRoot(projectRoot) {
  const root = path.resolve(projectRoot);
  const projectConfig = readJsonOrNull(path.join(root, 'project.config.json'));
  const configuredRoot = projectConfig && typeof projectConfig.miniprogramRoot === 'string'
    ? projectConfig.miniprogramRoot.trim()
    : 'miniprogram';
  const candidate = path.resolve(root, configuredRoot || 'miniprogram');
  const relative = path.relative(root, candidate);
  if (relative.startsWith('..') || path.isAbsolute(relative)) {
    return path.join(root, 'miniprogram');
  }
  return candidate;
}

function hasRealPrivateAppId(projectRoot) {
  const config = readJsonOrNull(path.join(projectRoot, 'project.private.config.json'));
  const appId = config && typeof config.appid === 'string' ? config.appid.trim() : '';
  return Boolean(appId) && appId.toLowerCase() !== 'touristappid';
}

function inspectPlugin(miniProgramRoot) {
  const appConfig = readJsonOrNull(path.join(miniProgramRoot, 'app.json'));
  const plugins = appConfig && appConfig.plugins;
  const pluginNames = plugins && typeof plugins === 'object' ? Object.keys(plugins) : [];
  const plugin = pluginNames.length === 1 && pluginNames[0] === 'WechatSI'
    ? plugins.WechatSI
    : null;
  const valid = Boolean(
    plugin
    && plugin.version === EXPECTED_PLUGIN_VERSION
    && plugin.provider === EXPECTED_PLUGIN_PROVIDER
  );
  return {
    pluginDeclared: valid,
    pluginVersion: valid ? EXPECTED_PLUGIN_VERSION : null,
    provider: valid ? EXPECTED_PLUGIN_PROVIDER : null
  };
}

function countPackagedAudioFiles(packageRoot) {
  let count = 0;
  const pending = [packageRoot];
  while (pending.length > 0) {
    const current = pending.pop();
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch (error) {
      continue;
    }
    entries.forEach(entry => {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        pending.push(fullPath);
      } else if (entry.isFile() && AUDIO_EXTENSIONS.has(path.extname(entry.name).toLowerCase())) {
        count += 1;
      }
    });
  }
  return count;
}

function buildVoiceConfigurationEvidence(projectRoot, initialSnapshot) {
  const miniProgramRoot = resolveMiniProgramRoot(projectRoot);
  const realPrivateAppIdConfigured = hasRealPrivateAppId(projectRoot);
  const plugin = inspectPlugin(miniProgramRoot);
  const packagedAudioFileCount = countPackagedAudioFiles(miniProgramRoot);
  const initialRecordState = initialSnapshot && initialSnapshot.recordState === 'idle'
    ? 'idle'
    : null;
  const initialVoiceModeEmpty = Boolean(initialSnapshot) && initialSnapshot.voiceMode === '';
  const failureCategories = [];

  if (!realPrivateAppIdConfigured) failureCategories.push('private-appid-missing');
  if (!plugin.pluginDeclared) failureCategories.push('plugin-declaration-invalid');
  if (packagedAudioFileCount !== 0) failureCategories.push('packaged-audio-present');
  if (initialRecordState !== 'idle') failureCategories.push('initial-record-state-not-idle');
  if (!initialVoiceModeEmpty) failureCategories.push('initial-voice-mode-not-empty');

  return {
    voiceConfiguration: {
      realPrivateAppIdConfigured,
      pluginDeclared: plugin.pluginDeclared,
      pluginVersion: plugin.pluginVersion,
      provider: plugin.provider,
      packagedAudioFileCount,
      initialRecordState,
      manualDeviceVerificationRequired: true
    },
    failureCategories: failureCategories.sort()
  };
}

function normalizeVersion(value) {
  const text = String(value == null ? '' : value).trim();
  return /^v?\d+(?:\.\d+){0,3}(?:-[0-9A-Za-z.-]+)?$/.test(text) ? text : null;
}

function payloadText(value) {
  if (Array.isArray(value)) return value.map(payloadText).join('\n');
  if (value && typeof value.message === 'string') return value.message;
  try {
    return JSON.stringify(value);
  } catch (error) {
    return String(value == null ? '' : value);
  }
}

function formatSmokeFailure(message, observedLogs) {
  const directCategory = payloadText(message).trim();
  if (ERROR_CATEGORIES.has(directCategory)) return directCategory;
  const evidence = [message].concat(Array.isArray(observedLogs) ? observedLogs : [])
    .map(payloadText)
    .join('\n');

  if (/access_token\s+missing/i.test(evidence)) return 'devtools-access-token-missing';
  if (/Assertion failed:/i.test(evidence)) return 'smoke-assertion-failed';
  if (/timed out after/i.test(evidence)) return 'devtools-operation-timeout';
  if (/automator\.connect|automation endpoint|Connection closed/i.test(evidence)) {
    return 'automation-connection-failed';
  }
  if (/CLI auto exited|cli auto/i.test(evidence)) return 'devtools-cli-auto-failed';
  if (/voice configuration/i.test(evidence)) return 'voice-configuration-invalid';
  if (/close DevTools|cleanup/i.test(evidence)) return 'devtools-cleanup-failed';
  return 'unexpected-smoke-failure';
}

function createSafeSmokeReport(options) {
  const config = options || {};
  const evidence = buildVoiceConfigurationEvidence(
    config.projectRoot,
    config.initialSnapshot || null
  );
  const rawLaunchError = config.rawLaunchError || null;
  const rawCliOutput = config.rawCliOutput || '';
  const consoleErrorCount = Array.isArray(config.consolePayloads)
    ? config.consolePayloads.length
    : 0;
  const exceptionCount = Array.isArray(config.exceptionPayloads)
    ? config.exceptionPayloads.length
    : 0;

  return {
    schemaVersion: 2,
    startedAtUtc: new Date().toISOString(),
    completedAtUtc: '',
    result: 'failed',
    exitCode: 1,
    command: {
      name: 'npm run test:wechat',
      project: '.'
    },
    runtime: {
      node: normalizeVersion(config.nodeVersion),
      miniprogramAutomator: normalizeVersion(config.automatorVersion)
    },
    devTools: null,
    automation: {
      launch: {
        status: rawLaunchError ? 'failed' : 'not-run',
        errorCategory: rawLaunchError ? formatSmokeFailure(rawLaunchError, []) : null
      },
      cliConnectFallback: {
        status: 'not-run',
        cliExitCode: null,
        errorCategory: rawCliOutput ? formatSmokeFailure(rawCliOutput, []) : null
      }
    },
    voiceConfiguration: evidence.voiceConfiguration,
    voiceConfigurationFailureCategories: evidence.failureCategories,
    assertions: [],
    consoleErrorCount,
    exceptionCount,
    errorCategory: rawLaunchError ? formatSmokeFailure(rawLaunchError, []) : null
  };
}

function applyVoiceConfigurationEvidence(report, projectRoot, initialSnapshot) {
  const evidence = buildVoiceConfigurationEvidence(projectRoot, initialSnapshot);
  report.voiceConfiguration = evidence.voiceConfiguration;
  report.voiceConfigurationFailureCategories = evidence.failureCategories;
  return evidence;
}

function finalizeRuntimeDiagnostics(report) {
  if (!report || report.exitCode !== 0) return false;
  const clean = report.consoleErrorCount === 0 && report.exceptionCount === 0;
  if (clean) return true;
  report.result = 'failed';
  report.exitCode = 1;
  report.errorCategory = 'smoke-assertion-failed';
  return false;
}

function formatSmokeSummary(report) {
  const allowedResults = new Set(['passed', 'passed-via-cli-connect-fallback', 'failed']);
  const result = allowedResults.has(report && report.result) ? report.result : 'failed';
  const category = ERROR_CATEGORIES.has(report && report.errorCategory)
    ? report.errorCategory
    : 'none';
  const consoleErrorCount = Number.isSafeInteger(report && report.consoleErrorCount)
    ? Math.max(0, report.consoleErrorCount)
    : 0;
  const exceptionCount = Number.isSafeInteger(report && report.exceptionCount)
    ? Math.max(0, report.exceptionCount)
    : 0;
  return '[wechat-smoke] result=' + result
    + '; errorCategory=' + category
    + '; consoleErrorCount=' + consoleErrorCount
    + '; exceptionCount=' + exceptionCount;
}

function hasRenderablePreview(snapshot, expectedLegCount, expectedFloor) {
  const leg = snapshot && snapshot.currentPreviewLeg;
  return Boolean(
    snapshot
    && snapshot.previewLegCount === expectedLegCount
    && leg
    && leg.floor === expectedFloor
    && /^\/assets\/floor-maps\/(?:[1-9]|1[0-3])F\.jpg$/.test(String(leg.image || ''))
    && Array.isArray(leg.lineSegments)
    && leg.lineSegments.length > 0
  );
}

function hasSameVerifiedShaft(firstSnapshot, secondSnapshot) {
  const firstShaft = firstSnapshot && firstSnapshot.currentLeg
    ? firstSnapshot.currentLeg.selectedElevatorShaftId
    : '';
  const secondShaft = secondSnapshot && secondSnapshot.currentLeg
    ? secondSnapshot.currentLeg.selectedElevatorShaftId
    : '';
  return /^S[1-7]$/.test(String(firstShaft || '')) && firstShaft === secondShaft;
}

function withHardTimeout(promise, timeoutMs, label, onLateResolve) {
  let state = 'pending';
  let timeoutId;

  return new Promise((resolve, reject) => {
    timeoutId = setTimeout(() => {
      if (state !== 'pending') return;
      state = 'timed-out';
      reject(new Error(label + ' timed out after ' + timeoutMs + ' ms'));
    }, timeoutMs);

    Promise.resolve(promise).then(value => {
      if (state === 'timed-out') {
        if (typeof onLateResolve === 'function') {
          Promise.resolve()
            .then(() => onLateResolve(value))
            .catch(() => {});
        }
        return;
      }
      if (state !== 'pending') return;
      state = 'settled';
      clearTimeout(timeoutId);
      resolve(value);
    }, error => {
      if (state !== 'pending') return;
      state = 'settled';
      clearTimeout(timeoutId);
      reject(error);
    });
  });
}

module.exports = {
  applyVoiceConfigurationEvidence,
  buildVoiceConfigurationEvidence,
  countPackagedAudioFiles,
  createSafeSmokeReport,
  finalizeRuntimeDiagnostics,
  formatSmokeFailure,
  formatSmokeSummary,
  hasRenderablePreview,
  hasSameVerifiedShaft,
  withHardTimeout
};
