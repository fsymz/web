const { defineConfig } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

function hasChrome() {
  return [
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    process.env.ProgramFiles && path.join(process.env.ProgramFiles, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    process.env['ProgramFiles(x86)'] && path.join(process.env['ProgramFiles(x86)'], 'Google', 'Chrome', 'Application', 'chrome.exe')
  ].filter(Boolean).some(candidate => fs.existsSync(candidate));
}

const browserChannel = process.env.PLAYWRIGHT_CHANNEL || (hasChrome() ? 'chrome' : 'msedge');

module.exports = defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.js',
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  expect: {
    timeout: 5000
  },
  use: {
    browserName: 'chromium',
    channel: browserChannel,
    baseURL: 'http://127.0.0.1:41739',
    headless: true
  }
});
