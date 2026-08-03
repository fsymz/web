const assert = require('node:assert/strict');
const test = require('node:test');

const { SimNavEngine } = require('../miniprogram/utils/simNavEngine.js');

function createLeg(overrides) {
  return Object.assign({
    title: '测试路段',
    points: [[0, 0], [10, 0]],
    imageSize: [100, 100]
  }, overrides);
}

function createEngine(callbacks, options) {
  return new SimNavEngine(Object.assign({
    callbacks,
    frameIntervalMs: 1000,
    statsIntervalMs: 1000,
    autoSwitchDelayMs: 1,
    speedPercentPerSecond: 1000,
    distanceMetersPerPercent: 1,
    minDurationMs: 1,
    maxDurationMs: 1
  }, options));
}

function completeCurrentLeg(engine) {
  engine.clearTimer();
  engine.startedAt = Date.now() - 5;
  engine.tick();
}

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function installFakeIntervals() {
  const originalSetInterval = globalThis.setInterval;
  const originalClearInterval = globalThis.clearInterval;
  const activeIntervals = new Set();

  globalThis.setInterval = callback => {
    const handle = { callback };
    activeIntervals.add(handle);
    return handle;
  };
  globalThis.clearInterval = handle => {
    activeIntervals.delete(handle);
  };

  return {
    activeIntervals,
    restore: () => {
      globalThis.setInterval = originalSetInterval;
      globalThis.clearInterval = originalClearInterval;
    }
  };
}

test('a completed leg emits exactly one final frame and stats payload', () => {
  const frames = [];
  const stats = [];
  const engine = createEngine({
    onFrame: frame => frames.push(frame),
    onStats: payload => stats.push(payload)
  });

  try {
    assert.equal(engine.start([createLeg()]), true);
    completeCurrentLeg(engine);

    assert.equal(frames.filter(frame => frame.ratio === 1).length, 1);
    assert.equal(stats.filter(payload => payload.ratio === 1).length, 1);
  } finally {
    engine.stop();
  }
});

test('start without options preserves constructor loop behavior at completion', () => {
  let completionPayload = null;
  const engine = createEngine({
    onLegComplete: payload => {
      completionPayload = payload;
    }
  }, { loop: true });

  try {
    assert.equal(engine.start([createLeg()]), true);
    completeCurrentLeg(engine);

    assert.equal(completionPayload.isLoopRestart, true);
    assert.equal(engine.status, 'switching');
    assert.notEqual(engine.switchTimer, null);
  } finally {
    engine.stop();
  }
});

test('a leg restart inside onLegComplete cancels the stale automatic switch', async () => {
  const legChanges = [];
  let engine;
  engine = createEngine({
    onLegChange: payload => legChanges.push(payload.legIndex),
    onLegComplete: () => {
      engine.startLeg(0);
    }
  });

  try {
    assert.equal(engine.start([createLeg(), createLeg({ title: '第二段' })]), true);
    completeCurrentLeg(engine);
    await wait(20);

    assert.deepEqual(legChanges, [0, 0]);
    assert.equal(engine.currentLegIndex, 0);
  } finally {
    engine.stop();
  }
});

test('stopping inside onLegComplete cancels the stale automatic switch', async () => {
  const legChanges = [];
  let engine;
  engine = createEngine({
    onLegChange: payload => legChanges.push(payload.legIndex),
    onLegComplete: () => {
      engine.stop();
    }
  });

  try {
    assert.equal(engine.start([createLeg(), createLeg({ title: '第二段' })]), true);
    completeCurrentLeg(engine);
    await wait(20);

    assert.deepEqual(legChanges, [0]);
    assert.equal(engine.status, 'idle');
  } finally {
    engine.stop();
  }
});

test('an automatic switch uses the next leg chosen when completion occurred', async () => {
  const legChanges = [];
  const engine = createEngine({
    onLegChange: payload => legChanges.push(payload.legIndex)
  });

  try {
    assert.equal(engine.start([
      createLeg(),
      createLeg({ title: '第二段' }),
      createLeg({ title: '第三段' })
    ]), true);
    completeCurrentLeg(engine);
    engine.currentLegIndex = 1;
    await wait(20);

    assert.deepEqual(legChanges, [0, 1]);
  } finally {
    engine.stop();
  }
});

test('startLeg builds metrics with the leg imageSize', () => {
  const engine = createEngine({});

  try {
    assert.equal(engine.start([
      createLeg({
        points: [[0, 0], [0, 10]],
        imageSize: [100, 200]
      })
    ]), true);
    assert.equal(engine.metrics.total, 20);
  } finally {
    engine.stop();
  }
});

test('a reentrant leg start owns one interval and one initial frame', () => {
  const fakeIntervals = installFakeIntervals();
  const frames = [];
  let engine;
  engine = createEngine({
    onLegChange: payload => {
      if (payload.legIndex === 0) engine.startLeg(1);
    },
    onFrame: frame => frames.push(frame.legIndex)
  });

  try {
    assert.equal(engine.start([createLeg(), createLeg({ title: '第二段' })]), true);
    assert.deepEqual({
      frames,
      activeIntervalCount: fakeIntervals.activeIntervals.size,
      activeIntervalIsTracked: fakeIntervals.activeIntervals.has(engine.timer)
    }, {
      frames: [1],
      activeIntervalCount: 1,
      activeIntervalIsTracked: true
    });

    engine.stop();
    assert.equal(fakeIntervals.activeIntervals.size, 0);
  } finally {
    engine.stop();
    fakeIntervals.restore();
  }
});

test('a reentrant stop during leg change leaves no interval or initial frame', () => {
  const fakeIntervals = installFakeIntervals();
  const frames = [];
  let engine;
  engine = createEngine({
    onLegChange: () => engine.stop(),
    onFrame: frame => frames.push(frame.legIndex)
  });

  try {
    assert.equal(engine.start([createLeg()]), true);
    assert.deepEqual({
      frames,
      activeIntervalCount: fakeIntervals.activeIntervals.size,
      hasTrackedInterval: engine.timer !== null,
      status: engine.status
    }, {
      frames: [],
      activeIntervalCount: 0,
      hasTrackedInterval: false,
      status: 'idle'
    });
  } finally {
    engine.stop();
    fakeIntervals.restore();
  }
});
