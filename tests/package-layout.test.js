const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const projectRoot = path.resolve(__dirname, '..');

test('the package exposes only the importable mini-program layout', () => {
  const project = JSON.parse(
    fs.readFileSync(path.join(projectRoot, 'project.config.json'), 'utf8')
  );
  const miniprogramRoot = path.join(projectRoot, project.miniprogramRoot);
  const app = JSON.parse(
    fs.readFileSync(path.join(miniprogramRoot, 'app.json'), 'utf8')
  );

  assert.equal(project.appid, 'touristappid');
  assert.equal(project.compileType, 'miniprogram');
  assert.equal(project.miniprogramRoot, 'miniprogram/');
  assert.equal(app.pages[0], 'pages/navigation/navigation');

  for (const forbidden of ['scripts', 'tests', 'web-demo', 'route-anchor-audit']) {
    assert.equal(fs.existsSync(path.join(miniprogramRoot, forbidden)), false);
  }

  assert.equal(
    fs.existsSync(path.join(miniprogramRoot, 'data', 'routePaths.js')),
    false
  );
});
