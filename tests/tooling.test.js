'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const projectRoot = path.resolve(__dirname, '..');
const checker = path.join(projectRoot, 'scripts', 'check-routes.js');
const syntaxChecker = path.join(projectRoot, 'scripts', 'check-syntax.js');
const releaseVerifier = path.join(projectRoot, 'scripts', 'verify-release.py');
const pythonRunner = path.join(projectRoot, 'scripts', 'run-python.js');
const {
  expectedPytestBase,
  pythonCandidates,
  validatePytestBase,
} = require(pythonRunner);

function runNode(script, cwd) {
  return spawnSync(process.execPath, [script], {
    cwd,
    encoding: 'utf8',
    windowsHide: true,
  });
}

function writeCommonJsJson(file, value) {
  fs.writeFileSync(file, `module.exports = ${JSON.stringify(value)};\n`, 'utf8');
}

function createRouteGateFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hospital-nav-route-gate-'));
  fs.cpSync(path.join(projectRoot, 'config'), path.join(root, 'config'), { recursive: true });
  fs.cpSync(path.join(projectRoot, 'miniprogram'), path.join(root, 'miniprogram'), { recursive: true });
  fs.mkdirSync(path.join(root, 'scripts'));
  fs.copyFileSync(checker, path.join(root, 'scripts', 'check-routes.js'));
  return root;
}

test('check-routes locates the project without arguments from an arbitrary cwd', () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'hospital-nav-cwd-'));
  try {
    const result = runNode(checker, cwd);
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  } finally {
    fs.rmSync(cwd, { recursive: true, force: true });
  }
});

test('check-routes rejects a non-co-located pair disguised as coLocated', () => {
  const root = createRouteGateFixture();
  try {
    const dataFile = path.join(root, 'miniprogram', 'data', 'sameFloorPaths.js');
    const paths = require(dataFile);
    assert.equal(Object.values(paths).filter(item => item.coLocated === true).length, 10);
    const key = '儿科门诊|||挂号缴费';
    const sourcePoint = paths[key].points[0];
    paths[key] = {
      ...paths[key],
      points: [sourcePoint],
      routeLength: 0,
      coLocated: true,
    };
    writeCommonJsJson(dataFile, paths);

    const result = runNode(path.join(root, 'scripts', 'check-routes.js'), os.tmpdir());
    assert.equal(result.status, 1, `${result.stdout}\n${result.stderr}`);
    assert.match(`${result.stdout}${result.stderr}`, /coLocated.*destination|destination.*coLocated/i);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('an isolated check-routes copy prints one usage and exits 2', () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'hospital-nav-isolated-'));
  const isolated = path.join(cwd, 'check-routes.js');
  try {
    fs.copyFileSync(checker, isolated);
    const result = runNode(isolated, cwd);
    const output = `${result.stdout}${result.stderr}`;
    assert.equal(result.status, 2, output);
    assert.equal((output.match(/usage:/gi) || []).length, 1, output);
  } finally {
    fs.rmSync(cwd, { recursive: true, force: true });
  }
});

test('check-syntax validates the complete mini-program source tree', () => {
  const result = runNode(syntaxChecker, os.tmpdir());
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /JavaScript/i);
  assert.match(result.stdout, /WXML/i);
  assert.match(result.stdout, /WXSS/i);
});

test('verify-release candidate mode executes all deterministic gates without authorizing release', () => {
  const result = spawnSync(process.execPath, [
    pythonRunner,
    path.relative(projectRoot, releaseVerifier),
    '--candidate',
  ], {
    cwd: projectRoot,
    encoding: 'utf8',
    windowsHide: true,
  });
  assert.match(result.stdout, /route-turn quality gate: passed/i);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /package total/i);
  assert.match(result.stdout, /maps:/i);
  assert.match(result.stdout, /audio: 0 B \(0 files\)/i);
  assert.match(result.stdout, /largest file/i);
  assert.match(result.stdout, /NOT RELEASE AUTHORIZATION/);
});

test('project Python commands use the bundled virtual environment and a writable pytest base', () => {
  const packageJson = require('../package.json');
  assert.match(packageJson.scripts['test:python'], /node scripts\/run-python\.js/);
  assert.match(packageJson.scripts['test:python'], /--basetemp=reports\/test-temp\/python/);
  assert.match(packageJson.scripts.verify, /^node scripts\/run-python\.js scripts\/verify-release\.py(?: --release)?$/);
  assert.doesNotMatch(packageJson.scripts.verify, /--candidate/);
  assert.equal(typeof packageJson.scripts['verify:candidate'], 'string');
  assert.match(packageJson.scripts['verify:candidate'], /^node scripts\/run-python\.js scripts\/verify-release\.py --candidate$/);
  assert.equal(typeof packageJson.scripts['verify:release'], 'string');
  assert.match(packageJson.scripts['verify:release'], /^node scripts\/run-python\.js scripts\/verify-release\.py --release$/);

  const result = spawnSync(process.execPath, [pythonRunner, '-c', 'import sys; print(sys.executable)'], {
    cwd: os.tmpdir(),
    encoding: 'utf8',
    windowsHide: true,
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  const interpreter = result.stdout.trim().replace(/\\/g, '/');
  const virtualEnvironment = process.platform === 'win32'
    ? path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
    : path.join(projectRoot, '.venv', 'bin', 'python');
  if (fs.existsSync(virtualEnvironment)) {
    assert.match(interpreter, /\/\.venv\/(Scripts|bin)\/python(?:3)?(?:\.exe)?$/i);
  } else {
    assert.notEqual(interpreter, '');
  }
});

test('Python candidate order covers virtual environments and system fallbacks', () => {
  assert.deepEqual(pythonCandidates('win32', 'C:\\project'), [
    { command: 'C:\\project\\.venv\\Scripts\\python.exe', prefix: [], local: true },
    { command: 'python', prefix: [], local: false },
    { command: 'py', prefix: ['-3'], local: false },
  ]);
  assert.deepEqual(pythonCandidates('linux', '/project'), [
    { command: '/project/.venv/bin/python', prefix: [], local: true },
    { command: '/project/.venv/bin/python3', prefix: [], local: true },
    { command: 'python3', prefix: [], local: false },
    { command: 'python', prefix: [], local: false },
  ]);
});

test('pytest base validation rejects duplicate, split, missing, and external paths', () => {
  const valid = ['-m', 'pytest', '--basetemp=reports/test-temp/python'];
  assert.equal(validatePytestBase(valid), expectedPytestBase);
  assert.throws(() => validatePytestBase(['-m', 'pytest']), /require/i);
  assert.throws(() => validatePytestBase([...valid, '--basetemp=reports/test-temp/python']), /exactly one/i);
  assert.throws(() => validatePytestBase(['-m', 'pytest', '--basetemp', 'reports/test-temp/python']), /exactly one/i);
  assert.throws(() => validatePytestBase(['-m', 'pytest', '--basetemp=../outside']), /must be/i);
});

test('Python runner preserves the child process exit code', () => {
  const result = spawnSync(process.execPath, [pythonRunner, '-c', 'import sys; sys.exit(7)'], {
    cwd: os.tmpdir(),
    encoding: 'utf8',
    windowsHide: true,
  });
  assert.equal(result.status, 7, `${result.stdout}\n${result.stderr}`);
});
