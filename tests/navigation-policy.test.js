'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const projectRoot = path.resolve(__dirname, '..');
const configPath = path.join(projectRoot, 'config', 'navigation-policy.json');
const generatorPath = path.join(projectRoot, 'scripts', 'generate-navigation-policy.js');
const runtimePath = path.join(projectRoot, 'miniprogram', 'data', 'navigationPolicy.js');
const {
  renderNavigationPolicy,
  validateNavigationPolicy,
} = require(generatorPath);

const EXPECTED_POLICY = {
  schemaVersion: 1,
  pathDistanceTieTolerancePx: 6,
  turnAngleDegrees: 25,
  uTurnAngleDegrees: 145,
  minimumSpokenStepMeters: 0.5,
  distanceRoundingMeters: 1,
  elevatorDistanceTieToleranceMeters: 0.5,
};

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function mutate(key, value) {
  return { ...EXPECTED_POLICY, [key]: value };
}

test('JSON policy and generated frozen CommonJS policy match field-for-field', () => {
  const sourcePolicy = readJson(configPath);
  delete require.cache[require.resolve(runtimePath)];
  const runtimePolicy = require(runtimePath);

  assert.deepEqual(sourcePolicy, EXPECTED_POLICY);
  assert.deepEqual(runtimePolicy, sourcePolicy);
  assert.equal(Object.isFrozen(runtimePolicy), true);
  assert.equal(
    fs.readFileSync(runtimePath, 'utf8').replace(/\r\n?/g, '\n'),
    renderNavigationPolicy(sourcePolicy)
  );
});

test('navigation policy rejects missing and unknown fields', () => {
  const missing = { ...EXPECTED_POLICY };
  delete missing.minimumSpokenStepMeters;
  const unknown = { ...EXPECTED_POLICY, inventedTolerance: 1 };

  assert.throws(() => validateNavigationPolicy(missing), /exactly.*keys/i);
  assert.throws(() => validateNavigationPolicy(unknown), /exactly.*keys/i);
});

test('navigation policy rejects booleans, strings, and non-finite numbers', () => {
  for (const key of Object.keys(EXPECTED_POLICY)) {
    assert.throws(() => validateNavigationPolicy(mutate(key, true)), new RegExp(key));
    assert.throws(() => validateNavigationPolicy(mutate(key, String(EXPECTED_POLICY[key]))), new RegExp(key));
    assert.throws(() => validateNavigationPolicy(mutate(key, Number.NaN)), new RegExp(key));
    assert.throws(() => validateNavigationPolicy(mutate(key, Number.POSITIVE_INFINITY)), new RegExp(key));
  }
});

test('navigation policy enforces the binding natural-domain ranges', () => {
  const invalidPolicies = [
    mutate('schemaVersion', 2),
    mutate('pathDistanceTieTolerancePx', -0.1),
    mutate('turnAngleDegrees', 0),
    mutate('turnAngleDegrees', 180),
    mutate('uTurnAngleDegrees', 25),
    mutate('uTurnAngleDegrees', 180.1),
    mutate('minimumSpokenStepMeters', 0),
    mutate('distanceRoundingMeters', 0),
    mutate('elevatorDistanceTieToleranceMeters', -0.1),
  ];

  for (const policy of invalidPolicies) {
    assert.throws(() => validateNavigationPolicy(policy), /navigation policy/i);
  }

  assert.doesNotThrow(() => validateNavigationPolicy({
    ...EXPECTED_POLICY,
    pathDistanceTieTolerancePx: 0,
    uTurnAngleDegrees: 180,
    elevatorDistanceTieToleranceMeters: 0,
  }));
});

test('--output writes a candidate without modifying runtime data', () => {
  const temporaryDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'navigation-policy-'));
  const candidatePath = path.join(temporaryDirectory, 'candidate-navigationPolicy.js');
  const runtimeBefore = fs.readFileSync(runtimePath, 'utf8');
  try {
    const result = spawnSync(process.execPath, [generatorPath, '--output', candidatePath], {
      cwd: os.tmpdir(),
      encoding: 'utf8',
      windowsHide: true,
    });

    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.equal(fs.readFileSync(runtimePath, 'utf8'), runtimeBefore);
    delete require.cache[require.resolve(candidatePath)];
    const candidate = require(candidatePath);
    assert.deepEqual(candidate, EXPECTED_POLICY);
    assert.equal(Object.isFrozen(candidate), true);
  } finally {
    fs.rmSync(temporaryDirectory, { recursive: true, force: true });
  }
});
