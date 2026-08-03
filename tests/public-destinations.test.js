const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const projectRoot = path.resolve(__dirname, '..');
const routes = require('../miniprogram/data/routes.js');

const excludedPatientDestinations = [
  '三楼行政办公区',
  '四楼行政办公区',
  '消毒供应中心',
  '手术室',
  '新生儿重症监护病房'
];

test('patient destinations contain the exact 42-name public set', () => {
  const names = routes.departmentRoutes.map(route => route.name);

  assert.equal(names.length, 42);
  for (const name of excludedPatientDestinations) {
    assert.equal(names.includes(name), false, name);
  }
  for (const name of ['消控室', '计算机机房']) {
    assert.equal(names.includes(name), true, name);
  }
});

test('generated runtime policy is immutable and matches the authoritative JSON', () => {
  const config = JSON.parse(
    fs.readFileSync(path.join(projectRoot, 'config', 'public-destinations.json'), 'utf8')
  );
  const policy = require('../miniprogram/data/destinationPolicy.js');

  assert.deepEqual(config.excludedPatientDestinations, excludedPatientDestinations);
  assert.deepEqual(policy.excludedPatientDestinations, excludedPatientDestinations);
  assert.deepEqual(policy.publicDestinations, config.publicDestinations);
  assert.equal(Object.isFrozen(policy.excludedPatientDestinations), true);
  assert.equal(Object.isFrozen(policy.publicDestinations), true);
  assert.equal(policy.publicDestinations.every(item => Object.isFrozen(item)), true);

  const runtimeSource = fs.readFileSync(
    path.join(projectRoot, 'miniprogram', 'data', 'routes.js'),
    'utf8'
  );
  assert.doesNotMatch(runtimeSource, /config[\\/]public-destinations\.json/);
});

test('maintenance migration and runtime generation are deterministic build steps', t => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'navigation-policy-'));
  t.after(() => fs.rmSync(tempRoot, { recursive: true, force: true }));
  const migratedPath = path.join(tempRoot, 'public-destinations.json');

  const migration = spawnSync(
    process.execPath,
    [path.join(projectRoot, 'scripts', 'migrate-maintenance-data.js'), '--write', migratedPath],
    { cwd: projectRoot, encoding: 'utf8' }
  );
  assert.equal(migration.status, 0, migration.stderr || migration.stdout);

  const migrated = JSON.parse(fs.readFileSync(migratedPath, 'utf8'));
  assert.equal(migrated.publicDestinations.length, 42);
  assert.deepEqual(migrated.excludedPatientDestinations, excludedPatientDestinations);

  const check = spawnSync(
    process.execPath,
    [path.join(projectRoot, 'scripts', 'generate-runtime-config.js'), '--check'],
    { cwd: projectRoot, encoding: 'utf8' }
  );
  assert.equal(check.status, 0, check.stderr || check.stdout);
});
