const assert = require('node:assert/strict');
const test = require('node:test');

const routes = require('../miniprogram/data/routes.js');
const navAgent = require('../miniprogram/utils/navAgent.js');

test('generic pharmacy requests require a two-destination choice', () => {
  for (const text of ['药房', '取药', '拿药', '我要去药房']) {
    const result = navAgent.handleMessage(text, routes);
    assert.equal(result.action, 'chooseDepartment', text);
    assert.deepEqual(result.matches, ['中药房', '西药房'], text);
    assert.equal(result.destinationName, '', text);
  }
});

test('information questions answer without changing the destination', () => {
  const info = navAgent.handleMessage('我想知道放射科在几楼', routes);

  assert.equal(info.action, 'answerDepartment');
  assert.equal(info.destinationName, '');
});

test('department-purpose questions answer without changing the destination', () => {
  const info = navAgent.handleMessage('放射科是做什么的？', routes);

  assert.equal(info.action, 'answerDepartment');
  assert.equal(info.destinationName, '');
  assert.match(info.reply, /放射科在1楼/);
  assert.doesNotMatch(info.reply, /填入目的地/);
});

test('equivalent department-purpose wording remains informational', () => {
  for (const text of [
    '放射科是干什么的',
    '放射科主要是做什么的',
    '放射科主要负责什么',
    '放射科能做什么',
    '放射科看什么病',
    '介绍一下放射科'
  ]) {
    const info = navAgent.handleMessage(text, routes);
    assert.equal(info.action, 'answerDepartment', text);
    assert.equal(info.destinationName, '', text);
    assert.doesNotMatch(info.reply, /填入目的地/, text);
  }
});

test('explicit navigation and a plain destination still set the destination', () => {
  for (const [text, destinationName] of [
    ['我要去放射科', '放射科'],
    ['放射科', '放射科'],
    ['我要去多功能科', '多功能科'],
    ['多功能科', '多功能科']
  ]) {
    const result = navAgent.handleMessage(text, routes);
    assert.equal(result.action, 'setDestination', text);
    assert.equal(result.destinationName, destinationName, text);
  }
});

test('English aliases resolve case-insensitively after trimming', () => {
  for (const alias of ['ct', 'CT', 'dr', 'DR']) {
    const resolved = routes.resolveDepartmentName('  ' + alias + '  ');
    assert.equal(resolved.name, '放射科', alias);
  }
});

test('message handling always returns the stable patient-facing result fields', () => {
  for (const text of ['', '二楼有哪些科室', '完全无法识别的内容']) {
    const result = navAgent.handleMessage(text, routes);
    assert.equal(typeof result.action, 'string', text);
    assert.equal(typeof result.destinationName, 'string', text);
    assert.equal(Array.isArray(result.matches), true, text);
    assert.equal(typeof result.reply, 'string', text);
  }
});
