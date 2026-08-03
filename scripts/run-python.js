#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const projectRoot = path.resolve(__dirname, '..');
const expectedPytestBase = path.join(projectRoot, 'reports', 'test-temp', 'python');

function pythonCandidates(platform = process.platform, root = projectRoot) {
  const pathApi = platform === 'win32' ? path.win32 : path.posix;
  if (platform === 'win32') {
    return [
      { command: pathApi.join(root, '.venv', 'Scripts', 'python.exe'), prefix: [], local: true },
      { command: 'python', prefix: [], local: false },
      { command: 'py', prefix: ['-3'], local: false },
    ];
  }
  return [
    { command: pathApi.join(root, '.venv', 'bin', 'python'), prefix: [], local: true },
    { command: pathApi.join(root, '.venv', 'bin', 'python3'), prefix: [], local: true },
    { command: 'python3', prefix: [], local: false },
    { command: 'python', prefix: [], local: false },
  ];
}

function samePath(left, right) {
  const normalize = value => {
    const resolved = path.resolve(value);
    return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
  };
  return normalize(left) === normalize(right);
}

function validatePytestBase(argumentsList) {
  if (argumentsList.includes('--basetemp')) {
    throw new Error('Use exactly one --basetemp=<path> argument.');
  }
  const baseArguments = argumentsList.filter(argument => argument.startsWith('--basetemp='));
  if (baseArguments.length > 1) {
    throw new Error('Use exactly one --basetemp argument.');
  }

  const isPytest = argumentsList[0] === '-m' && argumentsList[1] === 'pytest';
  if (isPytest && baseArguments.length !== 1) {
    throw new Error('Project pytest runs require the managed --basetemp path.');
  }
  if (!baseArguments.length) return null;

  const requested = path.resolve(projectRoot, baseArguments[0].slice('--basetemp='.length));
  if (!samePath(requested, expectedPytestBase)) {
    throw new Error('The pytest base directory must be reports/test-temp/python.');
  }
  return requested;
}

function preparePytestBase(argumentsList) {
  const pytestBase = validatePytestBase(argumentsList);
  if (!pytestBase) return;

  const managedPaths = [
    path.join(projectRoot, 'reports'),
    path.join(projectRoot, 'reports', 'test-temp'),
    pytestBase,
  ];
  for (const managedPath of managedPaths) {
    if (fs.existsSync(managedPath) && fs.lstatSync(managedPath).isSymbolicLink()) {
      throw new Error('The managed pytest directory cannot be a link.');
    }
  }
  fs.mkdirSync(path.dirname(pytestBase), { recursive: true });
}

function runMain(argumentsList = process.argv.slice(2)) {
  try {
    preparePytestBase(argumentsList);
  } catch (error) {
    console.error(error.message);
    return 2;
  }

  for (const candidate of pythonCandidates()) {
    if (candidate.local && !fs.existsSync(candidate.command)) continue;
    const result = spawnSync(candidate.command, [...candidate.prefix, ...argumentsList], {
      cwd: projectRoot,
      stdio: 'inherit',
      windowsHide: true,
    });
    if (result.error && result.error.code === 'ENOENT') continue;
    if (result.error) {
      console.error(`Unable to start Python: ${result.error.message}`);
      return 1;
    }
    return result.status === null ? 1 : result.status;
  }

  console.error('Python is required. Create .venv or install Python on PATH.');
  return 1;
}

if (require.main === module) {
  process.exit(runMain());
}

module.exports = {
  expectedPytestBase,
  pythonCandidates,
  validatePytestBase,
};
