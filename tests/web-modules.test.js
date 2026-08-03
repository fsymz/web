const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');
const vm = require('node:vm');
const bundleBuilder = require('../scripts/build-web-bundle');

const projectRoot = path.resolve(__dirname, '..');
const miniprogramRoot = path.join(projectRoot, 'miniprogram');
const floorMapPattern = /^\/assets\/floor-maps\/(?:[1-9]|1[0-3])F\.jpg$/;

test('bundle metadata is identical for LF and CRLF source text', () => {
  const lf = "module.exports = { ok: true };\n";
  const crlf = lf.replace(/\n/g, '\r\n');
  assert.equal(bundleBuilder.hashNormalizedSource(lf), bundleBuilder.hashNormalizedSource(crlf));
});

test('the checked-in browser bundle is a required fresh build artifact', t => {
  const committedBundle = path.join(projectRoot, 'web-demo', 'navigation.bundle.js');
  const check = spawnSync(
    process.execPath,
    [path.join(projectRoot, 'scripts', 'build-web-bundle.js'), '--check', committedBundle],
    { cwd: projectRoot, encoding: 'utf8' }
  );
  assert.equal(check.status, 0, check.stderr || check.stdout);

  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'stale-navigation-bundle-'));
  t.after(() => fs.rmSync(tempRoot, { recursive: true, force: true }));
  const crlfBundle = path.join(tempRoot, 'navigation-crlf.bundle.js');
  const committedSource = fs.readFileSync(committedBundle, 'utf8');
  const normalizedCommitted = bundleBuilder.normalizeSource(committedSource);
  fs.writeFileSync(crlfBundle, normalizedCommitted.replace(/\n/g, '\r\n'), 'utf8');
  const crlfCheck = spawnSync(
    process.execPath,
    [path.join(projectRoot, 'scripts', 'build-web-bundle.js'), '--check', crlfBundle],
    { cwd: projectRoot, encoding: 'utf8' }
  );
  assert.equal(crlfCheck.status, 0, crlfCheck.stderr || crlfCheck.stdout);

  const staleBundle = path.join(tempRoot, 'navigation.bundle.js');
  fs.writeFileSync(staleBundle, '// stale\n', 'utf8');
  const staleCheck = spawnSync(
    process.execPath,
    [path.join(projectRoot, 'scripts', 'build-web-bundle.js'), '--check', staleBundle],
    { cwd: projectRoot, encoding: 'utf8' }
  );
  assert.equal(staleCheck.status, 1, staleCheck.stdout + staleCheck.stderr);

  const packageJson = require('../package.json');
  assert.match(packageJson.scripts['test:web'], /build-web-bundle\.js --check/);
  const verifier = fs.readFileSync(path.join(projectRoot, 'scripts', 'verify-release.py'), 'utf8');
  assert.match(verifier, /build-web-bundle\.js.*--check/);
});

test('browser bundle isolates CommonJS modules and preserves module cache state', t => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'navigation-bundle-'));
  t.after(() => fs.rmSync(tempRoot, { recursive: true, force: true }));
  const outputPath = path.join(tempRoot, 'navigation.bundle.js');
  const build = spawnSync(
    process.execPath,
    [path.join(projectRoot, 'scripts', 'build-web-bundle.js'), '--output', outputPath],
    { cwd: projectRoot, encoding: 'utf8' }
  );
  assert.equal(build.status, 0, build.stderr || build.stdout);

  const source = fs.readFileSync(outputPath, 'utf8');
  const sandbox = { window: {} };
  vm.runInNewContext(source, sandbox, { filename: outputPath });
  assert.equal(typeof sandbox.window.__navigationRequire, 'function');

  const browserRoutes = sandbox.window.__navigationRequire('/data/routes.js');
  const before = JSON.parse(JSON.stringify(browserRoutes.resolveDepartmentName('二科')));
  sandbox.window.__navigationRequire('/utils/navAgent.js');
  const after = JSON.parse(JSON.stringify(browserRoutes.resolveDepartmentName('二科')));

  assert.deepEqual(before, after);
  assert.equal(before.status, 'ambiguous');
  assert.deepEqual(before.matches, ['外二科病房', '骨二科病房', '内二科病房']);
  assert.doesNotMatch(source, /https?:\/\//);
  assert.doesNotMatch(source, /\/images\//);
});

test('production routes expose only shared floor maps and no legacy static-map API', () => {
  const routesPath = path.join(miniprogramRoot, 'data', 'routes.js');
  const routesSource = fs.readFileSync(routesPath, 'utf8');
  const routes = require(routesPath);

  assert.equal(fs.existsSync(path.join(miniprogramRoot, 'data', 'routePaths.js')), false);
  for (const directory of ['data', 'utils']) {
    for (const entry of fs.readdirSync(path.join(miniprogramRoot, directory))) {
      if (!entry.endsWith('.js')) continue;
      const source = fs.readFileSync(path.join(miniprogramRoot, directory, entry), 'utf8');
      assert.doesNotMatch(source, /\/images\//, directory + '/' + entry);
    }
  }
  for (const forbidden of ['/images/', 'imageMap1', 'imageMap2', 'FALLBACK_POINTS', 'getImageFileName', 'getRoutePathConfig']) {
    assert.equal(routesSource.includes(forbidden), false, forbidden);
  }

  for (const route of routes.departmentRoutes) {
    assert.match(route.fromElevator.image, floorMapPattern, route.name);
    assert.match(route.toDestination.image, floorMapPattern, route.name);
  }

  for (const [from, to] of [['儿科门诊', '挂号缴费'], ['儿科门诊', '内二科病房']]) {
    const plan = routes.createNavigationPlan(from, to);
    assert.equal(plan.ok, true);
    for (const leg of plan.legs) assert.match(leg.image, floorMapPattern, leg.title);
  }
});
