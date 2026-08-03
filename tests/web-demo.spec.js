'use strict';

const { test, expect } = require('@playwright/test');
const { createWebDemoServer } = require('../scripts/serve-web-demo');

let webServer;

test.beforeAll(async () => {
  webServer = createWebDemoServer();
  await new Promise((resolve, reject) => {
    const onError = error => reject(error);
    webServer.once('error', onError);
    webServer.listen(41739, '127.0.0.1', () => {
      webServer.off('error', onError);
      resolve();
    });
  });
});

test.afterAll(async () => {
  if (!webServer) return;
  if (typeof webServer.closeAllConnections === 'function') {
    webServer.closeAllConnections();
  }
  await new Promise((resolve, reject) => {
    webServer.close(error => error ? reject(error) : resolve());
  });
});

test('local test server exposes only the demo and packaged map assets', async ({ request }) => {
  await expect.poll(async () => (await request.get('/web-demo/index.html')).status()).toBe(200);
  expect((await request.get('/package.json')).status()).toBe(400);
  expect((await request.get('/.venv/pyvenv.cfg')).status()).toBe(400);
  expect((await request.get('/node_modules/@playwright/test/package.json')).status()).toBe(400);
});

function observePage(page) {
  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];
  const requests = [];
  const responses = [];

  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('requestfailed', request => {
    failedRequests.push(request.url() + ': ' + (request.failure()?.errorText || 'unknown'));
  });
  page.on('request', request => requests.push(request.url()));
  page.on('response', response => responses.push({ url: response.url(), status: response.status() }));

  return { consoleErrors, pageErrors, failedRequests, requests, responses };
}

async function planRoute(page, from, to) {
  await page.getByTestId('from-select').selectOption({ label: from });
  await page.getByTestId('to-select').selectOption({ label: to });
  await page.getByRole('button', { name: '查询路线' }).click();
}

test('离线 Web 演示使用同一运行时完成同层、跨层与共址查询', async ({ page }) => {
  const observed = observePage(page);

  await page.goto('/web-demo/');
  await expect(page).toHaveTitle('院内导航离线演示');
  await expect(page.locator('link[rel="icon"]')).toHaveAttribute('href', 'data:,');
  await expect(page.getByTestId('from-select').locator('option')).toHaveCount(43);
  await expect(page.getByTestId('to-select').locator('option')).toHaveCount(43);

  await planRoute(page, '儿科门诊', '挂号缴费');
  await expect(page.getByTestId('plan-status')).toHaveAttribute('data-status', 'route');
  await expect(page.getByTestId('plan-status')).toContainText('同层');
  await expect(page.locator('[data-active-floor-map="true"]')).toHaveCount(1);
  await expect(page.getByTestId('active-floor-map')).toHaveAttribute('src', /\/miniprogram\/assets\/floor-maps\/1F\.jpg$/);

  await expect(page.locator('#play-audio')).toHaveCount(0);
  await expect(page.locator('audio')).toHaveCount(0);

  await planRoute(page, '儿科门诊', '妇科门诊');
  await expect(page.getByTestId('plan-status')).toHaveAttribute('data-status', 'route');
  await expect(page.getByTestId('plan-status')).toContainText('跨层');
  await expect(page.getByTestId('selected-shaft')).toHaveText('6号电梯');
  await expect(page.getByTestId('leg-count')).toHaveText('2');
  await expect(page.locator('[data-active-floor-map="true"]')).toHaveCount(1);
  const firstMap = await page.getByTestId('active-floor-map').getAttribute('src');
  await page.getByRole('button', { name: '下一段' }).click();
  await expect(page.locator('[data-active-floor-map="true"]')).toHaveCount(1);
  await expect(page.getByTestId('active-floor-map')).not.toHaveAttribute('src', firstMap);

  await planRoute(page, '中药房', '西药房');
  await expect(page.getByTestId('plan-status')).toHaveAttribute('data-status', 'coLocated');
  await expect(page.getByTestId('plan-status')).toContainText('同一区域');
  await expect(page.locator('[data-active-floor-map="true"]')).toHaveCount(0);

  expect(observed.consoleErrors).toEqual([]);
  expect(observed.pageErrors).toEqual([]);
  expect(observed.failedRequests).toEqual([]);
  expect(observed.responses.filter(item => (
    /\/miniprogram\/assets\/floor-maps\//.test(item.url) && item.status >= 400
  ))).toEqual([]);
  expect(observed.requests.filter(item => /\.(?:mp3|wav|m4a|aac|ogg)(?:[?#]|$)/i.test(item))).toEqual([]);
  expect(observed.requests.length).toBeGreaterThan(0);
  for (const requestUrl of observed.requests) {
    const url = new URL(requestUrl);
    expect(['127.0.0.1', 'localhost']).toContain(url.hostname);
  }
});
