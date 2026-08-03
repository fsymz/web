#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const childProcess = require('node:child_process');
const automatorPackage = require('miniprogram-automator/package.json');
const {
  applyVoiceConfigurationEvidence,
  createSafeSmokeReport,
  finalizeRuntimeDiagnostics,
  formatSmokeFailure,
  formatSmokeSummary,
  hasRenderablePreview,
  hasSameVerifiedShaft,
  withHardTimeout
} = require('./wechat-smoke-diagnostics');

const projectRoot = path.resolve(__dirname, '..');
const expectedPagePath = 'pages/navigation/navigation';

process.on('unhandledRejection', reason => {
  const category = formatSmokeFailure(reason, []);
  if (process.exitCode === 1 && category === 'automation-connection-failed') return;
  process.stderr.write('Unhandled automation error: ' + category + '\n');
  process.exitCode = 1;
});

function installWindowsBatchSpawnAdapter() {
  if (process.platform !== 'win32') return;
  const originalSpawn = childProcess.spawn;
  childProcess.spawn = function spawnWithBatchSupport(command, args, options) {
    if (!/\.(bat|cmd)$/i.test(String(command || ''))) {
      return originalSpawn.call(childProcess, command, args, options);
    }
    return originalSpawn.call(
      childProcess,
      process.env.ComSpec || 'C:\\Windows\\System32\\cmd.exe',
      ['/d', '/c', 'call', command].concat(args || []),
      options
    );
  };
}

installWindowsBatchSpawnAdapter();
const automator = require('miniprogram-automator');

function parseArguments(argv) {
  if (argv.length === 0) return { reportPath: '' };
  if (argv.length === 2 && argv[0] === '--report' && argv[1]) {
    return { reportPath: path.resolve(projectRoot, argv[1]) };
  }
  throw new Error('Usage: node scripts/wechat-smoke.js [--report <report.json>]');
}

function assertCondition(condition, name, details, report) {
  const entry = { name, passed: Boolean(condition) };
  report.assertions.push(entry);
  if (!condition) throw new Error('Assertion failed: ' + name);
}

function writeReport(reportPath, report) {
  if (!reportPath) return;
  fs.mkdirSync(path.dirname(reportPath), { recursive: true });
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + '\n', 'utf8');
}

async function launchWithSlowCliGrace(options) {
  return withHardTimeout(
    automator.launch(options),
    30000,
    'automator.launch',
    miniProgram => {
      if (miniProgram && typeof miniProgram.disconnect === 'function') {
        miniProgram.disconnect();
        return;
      }
      if (miniProgram && typeof miniProgram.close === 'function') {
        return miniProgram.close();
      }
    }
  );
}

function runCliAuto(cliPath, port) {
  const args = [
    'auto',
    '--project', projectRoot,
    '--auto-port', String(port),
    '--trust-project'
  ];
  return childProcess.spawnSync(
    process.env.ComSpec || 'C:\\Windows\\System32\\cmd.exe',
    ['/d', '/c', 'call', cliPath].concat(args),
    {
      cwd: projectRoot,
      encoding: 'utf8',
      timeout: 30000,
      windowsHide: true,
      maxBuffer: 1024 * 1024
    }
  );
}

function runCliClose(cliPath) {
  return childProcess.spawnSync(
    process.env.ComSpec || 'C:\\Windows\\System32\\cmd.exe',
    ['/d', '/c', 'call', cliPath, 'close', '--project', projectRoot],
    {
      cwd: projectRoot,
      encoding: 'utf8',
      timeout: 10000,
      windowsHide: true,
      maxBuffer: 1024 * 1024
    }
  );
}

function withTimeout(promise, timeoutMs, label) {
  let timeoutId;
  const timeout = new Promise((resolve, reject) => {
    timeoutId = setTimeout(() => reject(new Error(label + ' timed out after ' + timeoutMs + ' ms')), timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timeoutId));
}

function progress(stage) {
  process.stdout.write('[wechat-smoke] ' + stage + '\n');
}

async function connectWithRetries(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      return await withTimeout(
        automator.connect({ wsEndpoint: 'ws://127.0.0.1:' + port }),
        4000,
        'automator.connect'
      );
    } catch (error) {
      lastError = error;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  }
  throw lastError || new Error('Timed out connecting to WeChat automation endpoint');
}

async function run() {
  const { reportPath } = parseArguments(process.argv.slice(2));
  const cliPath = process.env.WECHAT_DEVTOOLS_CLI ||
    'C:\\Program Files (x86)\\Tencent\\微信web开发者工具\\cli.bat';
  const report = createSafeSmokeReport({
    projectRoot,
    nodeVersion: process.version,
    automatorVersion: automatorPackage.version
  });
  let miniProgram = null;
  let consoleListener = null;
  let exceptionListener = null;

  try {
    assertCondition(fs.existsSync(cliPath), '微信开发者工具 CLI 存在', path.basename(cliPath), report);
    const automationPort = 9420;
    try {
      progress('尝试 miniprogram-automator.launch');
      miniProgram = await launchWithSlowCliGrace({
        cliPath,
        projectPath: projectRoot,
        port: automationPort,
        timeout: 20000,
        trustProject: true
      });
      report.automation.launch.status = 'passed';
    } catch (launchError) {
      report.automation.launch.status = 'failed';
      report.automation.launch.errorCategory = formatSmokeFailure(launchError, []);

      progress('launch 不兼容，执行 CLI auto 编译回退');
      const cliResult = runCliAuto(cliPath, automationPort);
      report.automation.cliConnectFallback.cliExitCode = Number.isInteger(cliResult.status)
        ? cliResult.status
        : 1;
      if (cliResult.error || cliResult.status !== 0) {
        report.automation.cliConnectFallback.status = 'failed';
        report.automation.cliConnectFallback.errorCategory = formatSmokeFailure(
          [cliResult.error, cliResult.stdout, cliResult.stderr],
          ['CLI auto exited']
        );
        throw new Error('CLI auto exited');
      }
      assertCondition(true, 'CLI auto 触发编译并以 0 退出', 'exit 0', report);

      try {
        progress('连接 CLI 自动化端口');
        miniProgram = await connectWithRetries(automationPort, 20000);
        report.automation.cliConnectFallback.status = 'passed';
        assertCondition(true, 'CLI 启动的自动化端口连接成功', String(automationPort), report);
      } catch (connectError) {
        report.automation.cliConnectFallback.status = 'failed';
        report.automation.cliConnectFallback.errorCategory = formatSmokeFailure(connectError, []);
        throw new Error('automation endpoint connection failed');
      }
    }
    assertCondition(Boolean(miniProgram), '自动化连接建立', '', report);

    consoleListener = payload => {
      const level = String(payload && (payload.type || payload.level) || '').toLowerCase();
      if (level === 'error') report.consoleErrorCount += 1;
    };
    exceptionListener = () => {
      report.exceptionCount += 1;
    };
    miniProgram.on('console', consoleListener);
    miniProgram.on('exception', exceptionListener);

    const toolInfo = await withTimeout(miniProgram.send('Tool.getInfo'), 5000, 'Tool.getInfo');
    const safeVersion = value => {
      const text = String(value == null ? '' : value).trim();
      return /^\d+(?:\.\d+){1,3}$/.test(text) ? text : null;
    };
    report.devTools = {
      version: safeVersion(toolInfo && toolInfo.version),
      SDKVersion: safeVersion(toolInfo && toolInfo.SDKVersion)
    };
    progress('重载导航页并等待编译结果');
    const page = await withTimeout(
      miniProgram.reLaunch('/' + expectedPagePath),
      20000,
      'miniProgram.reLaunch'
    );
    assertCondition(Boolean(page), '页面重载成功', '', report);
    assertCondition(String(page.path || '').replace(/^\//, '') === expectedPagePath,
      '页面路径正确', String(page.path || ''), report);

    await page.waitFor(650);
    const initialData = await withTimeout(page.data(), 8000, 'initial page.data');
    const initialImages = await withTimeout(page.$$('image'), 8000, 'initial image query');
    const voiceEvidence = applyVoiceConfigurationEvidence(report, projectRoot, initialData);
    assertCondition(voiceEvidence.voiceConfiguration.realPrivateAppIdConfigured,
      '私有配置已设置真实 AppID', '', report);
    assertCondition(voiceEvidence.voiceConfiguration.pluginDeclared,
      'WechatSI 插件声明准确', '', report);
    assertCondition(voiceEvidence.voiceConfiguration.packagedAudioFileCount === 0,
      '小程序包内无本地音频', '', report);
    assertCondition(voiceEvidence.voiceConfiguration.initialRecordState === 'idle',
      '欢迎语音等待窗口后录音状态为空闲', '', report);
    assertCondition(initialData.voiceMode === '',
      '欢迎语音等待窗口后录音模式为空', '', report);
    assertCondition(Array.isArray(initialData.departments) && initialData.departments.length === 42,
      '公开目的地为 42 项', String(initialData.departments && initialData.departments.length), report);
    assertCondition(initialImages.length <= 1,
      '初始化只渲染零或一张地图', String(initialImages.length), report);

    await withTimeout(page.setData({ inputVal1: '儿科门诊', inputVal2: '挂号缴费' }), 8000, 'same-floor setData');
    await withTimeout(page.callMethod('updateRoutePreview'), 8000, 'same-floor planning');
    await page.waitFor(250);
    const sameFloorData = await withTimeout(page.data(), 8000, 'same-floor page.data');
    const sameFloorImages = await withTimeout(page.$$('image'), 8000, 'same-floor image query');
    assertCondition(hasRenderablePreview(sameFloorData, 1, '1楼'),
      '同层路线规划成功', JSON.stringify({
        count: sameFloorData.previewLegCount,
        floor: sameFloorData.currentPreviewLeg && sameFloorData.currentPreviewLeg.floor,
        segments: sameFloorData.currentPreviewLeg && sameFloorData.currentPreviewLeg.lineSegments
          ? sameFloorData.currentPreviewLeg.lineSegments.length
          : 0
      }), report);
    assertCondition(sameFloorImages.length === 1,
      '同层路线只渲染一张地图', String(sameFloorImages.length), report);

    await withTimeout(page.setData({ inputVal1: '儿科门诊', inputVal2: '内二科病房' }), 8000, 'cross-floor setData');
    await withTimeout(page.callMethod('updateRoutePreview'), 8000, 'cross-floor planning');
    await page.waitFor(250);
    const crossFloorFirstPreview = await withTimeout(page.data(), 8000, 'cross-floor first page.data');
    const crossFloorFirstImages = await withTimeout(page.$$('image'), 8000, 'cross-floor first image query');
    assertCondition(hasRenderablePreview(crossFloorFirstPreview, 2, '1楼'),
      '跨层起点预览规划成功', String(crossFloorFirstPreview.previewLegCount), report);
    assertCondition(crossFloorFirstImages.length === 1,
      '跨层起点预览只渲染一张地图', String(crossFloorFirstImages.length), report);

    await withTimeout(page.callMethod('showNextPreviewLeg'), 8000, 'cross-floor second preview');
    await page.waitFor(100);
    const crossFloorSecondPreview = await withTimeout(page.data(), 8000, 'cross-floor second page.data');
    const crossFloorSecondImages = await withTimeout(page.$$('image'), 8000, 'cross-floor second image query');
    assertCondition(hasRenderablePreview(crossFloorSecondPreview, 2, '13楼'),
      '跨层终点预览规划成功', String(crossFloorSecondPreview.previewLegCount), report);
    assertCondition(crossFloorSecondImages.length === 1,
      '跨层终点预览只渲染一张地图', String(crossFloorSecondImages.length), report);

    await withTimeout(page.callMethod('startNavigation'), 8000, 'cross-floor start navigation');
    await page.waitFor(100);
    const firstRuntimeLeg = await withTimeout(page.data(), 8000, 'cross-floor first runtime leg');
    await withTimeout(page.callMethod('startStepLeg', 1), 8000, 'cross-floor second runtime leg');
    await page.waitFor(100);
    const secondRuntimeLeg = await withTimeout(page.data(), 8000, 'cross-floor second runtime leg data');
    const selectedShaft = firstRuntimeLeg.currentLeg && firstRuntimeLeg.currentLeg.selectedElevatorShaftId;
    assertCondition(hasSameVerifiedShaft(firstRuntimeLeg, secondRuntimeLeg),
      '跨层双段使用同一已确认梯井', String(selectedShaft || ''), report);

    await page.waitFor(250);
    assertCondition(report.consoleErrorCount === 0,
      '控制台无 error', '', report);
    assertCondition(report.exceptionCount === 0,
      '运行时无 exception', '', report);

    report.result = report.automation.launch.status === 'passed'
      ? 'passed'
      : 'passed-via-cli-connect-fallback';
    report.exitCode = 0;
  } catch (error) {
    report.errorCategory = formatSmokeFailure(error, []);
    process.exitCode = 1;
  } finally {
    if (miniProgram) {
      try {
        await withTimeout(miniProgram.close(), 8000, 'miniProgram.close');
      } catch (closeError) {
        try {
          miniProgram.disconnect();
        } catch (disconnectError) {
          // The CLI close below remains the final cleanup path.
        }
        runCliClose(cliPath);
        if (report.exitCode === 0) {
          report.result = 'failed';
          report.exitCode = 1;
          report.errorCategory = 'devtools-cleanup-failed';
          process.exitCode = 1;
        }
      }
      if (consoleListener) miniProgram.removeListener('console', consoleListener);
      if (exceptionListener) miniProgram.removeListener('exception', exceptionListener);
    }
    const wasSuccessful = report.exitCode === 0;
    const runtimeDiagnosticsClean = finalizeRuntimeDiagnostics(report);
    if (wasSuccessful && !runtimeDiagnosticsClean) process.exitCode = 1;
    report.completedAtUtc = new Date().toISOString();
    writeReport(reportPath, report);
  }

  if (report.exitCode !== 0) {
    throw new Error(report.errorCategory || 'unexpected-smoke-failure');
  }
  process.stdout.write(formatSmokeSummary(report) + '\n');
}

run().catch(error => {
  process.stderr.write(formatSmokeFailure(error, []) + '\n');
  process.exitCode = 1;
});
