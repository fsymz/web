const assert = require('node:assert/strict');
const test = require('node:test');

const { loadPage } = require('./helpers/load-page.js');
const routeMath = require('../miniprogram/utils/routeMath.js');

function createPageInstance(definition) {
  const page = Object.create(definition);
  page.data = Object.assign({}, definition.data, {
    distanceMetersPerPercent: 1,
    stepWalkMetersPerSecond: 2,
    stepMode: false,
    stepAnimating: false
  });
  page.setData = function(update, callback) {
    Object.assign(this.data, update);
    if (callback) callback();
  };
  page.simNavEngine = {
    stop: () => true,
    pause: () => true
  };
  page.stopAudio = () => {};
  page.speakNavigationPrompt = () => {};
  return page;
}

function wait(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

test('step navigation uses the current leg image aspect for all route behavior', async t => {
  const loaded = loadPage();
  const page = createPageInstance(loaded.definition);
  t.after(() => {
    page.clearStepAnimation();
    loaded.restore();
  });

  const leg = {
    title: '纵向与斜向测试路段',
    floor: '1楼',
    arrivalName: '检验科',
    points: [[0, 0], [0, 10], [5, 20]],
    imageSize: [100, 200],
    hasRoutePath: true
  };
  page.currentPlan = { legs: [leg] };

  page.startStepLeg(0);
  const initialDistanceText = page.data.navDistanceText;

  page.stepNavLegIndex = 0;
  page.stepNavPointIndex = 1;
  page.data.stepMode = true;
  page.data.stepAnimating = false;
  page.stepNavigation();
  await wait(100);

  assert.deepEqual({
    initialDistanceText,
    instruction: page.data.navInstruction,
    etaText: page.data.navEtaText,
    markerAngle: page.data.markerAngle
  }, {
    initialDistanceText: '41m',
    instruction: '继续直行约21米，到达检验科',
    etaText: '11秒',
    markerAngle: 166
  });
});

test('page passes raw points and semantic indexes to the semantic instruction builder', t => {
  const originalBuilder = routeMath.buildSemanticStepInstruction;
  const builderCalls = [];
  routeMath.buildSemanticStepInstruction = (
    rawPoints,
    rawPointIndexes,
    targetSemanticIndex,
    options
  ) => {
    builderCalls.push({ rawPoints, rawPointIndexes, targetSemanticIndex, options });
    const rawPoint = rawPoints[rawPointIndexes[targetSemanticIndex]];
    return {
      text: `semantic prompt ${targetSemanticIndex}`,
      point: { x: rawPoint[0], y: rawPoint[1] },
      progress: targetSemanticIndex === rawPointIndexes.length - 1 ? 100 : 0,
      isArrival: targetSemanticIndex === rawPointIndexes.length - 1
    };
  };

  const loaded = loadPage();
  const page = createPageInstance(loaded.definition);
  const spokenPrompts = [];
  const animations = [];
  t.after(() => {
    routeMath.buildSemanticStepInstruction = originalBuilder;
    loaded.restore();
  });

  page.speakNavigationPrompt = (text, key) => spokenPrompts.push({ text, key });
  page.animateStepSegment = (legIndex, fromIndex, toIndex, prompt) => {
    animations.push({ legIndex, fromIndex, toIndex, prompt });
  };
  const leg = {
    arrivalName: 'destination',
    points: [[0, 0], [0, 4], [4, 4], [8, 4], [8, 0], [12, 0]],
    imageSize: [100, 100],
    hasRoutePath: true,
    routePath: { semanticPointIndexes: [0, 2, 5] }
  };
  page.currentPlan = { legs: [leg] };

  page.startStepLeg(0);
  page.stepNavigation();

  assert.deepEqual(builderCalls.map(call => ({
    rawPoints: call.rawPoints,
    rawPointIndexes: call.rawPointIndexes,
    targetSemanticIndex: call.targetSemanticIndex,
    arrivalName: call.options.arrivalName,
    hasNextLeg: call.options.hasNextLeg,
    distanceMetersPerPercent: call.options.distanceMetersPerPercent,
    imageSize: call.options.imageSize
  })), [
    {
      rawPoints: leg.points,
      rawPointIndexes: [0, 2, 5],
      targetSemanticIndex: 0,
      arrivalName: 'destination',
      hasNextLeg: false,
      distanceMetersPerPercent: 1,
      imageSize: [100, 100]
    },
    {
      rawPoints: leg.points,
      rawPointIndexes: [0, 2, 5],
      targetSemanticIndex: 1,
      arrivalName: 'destination',
      hasNextLeg: false,
      distanceMetersPerPercent: 1,
      imageSize: [100, 100]
    }
  ]);
  assert.deepEqual(spokenPrompts, [
    { text: 'semantic prompt 0', key: 'leg-0:step-0' }
  ]);
  assert.deepEqual(animations, [{
    legIndex: 0,
    fromIndex: 0,
    toIndex: 2,
    prompt: {
      text: 'semantic prompt 1',
      point: { x: 4, y: 4 },
      progress: 0,
      isArrival: false
    }
  }]);
});

test('semantic step navigation keeps raw-slice motion, text, and speech in sync', t => {
  const loaded = loadPage();
  const page = createPageInstance(loaded.definition);
  const spokenPrompts = [];
  const animationCallbacks = [];
  const animatedDistances = [];
  const originalSetInterval = global.setInterval;
  const originalDateNow = Date.now;
  let now = 0;

  global.setInterval = callback => {
    animationCallbacks.push(callback);
    return animationCallbacks.length;
  };
  Date.now = () => now;

  t.after(() => {
    global.setInterval = originalSetInterval;
    Date.now = originalDateNow;
    page.clearStepAnimation();
    loaded.restore();
  });

  page.getStepSegmentDuration = distance => {
    animatedDistances.push(distance);
    return 100;
  };
  page.speakNavigationPrompt = (text, key) => {
    spokenPrompts.push({ text, key });
  };
  const leg = {
    title: 'semantic leg',
    floor: '1F',
    arrivalName: 'destination',
    points: [[0, 0], [0, 4], [4, 4], [8, 4], [8, 0], [12, 0]],
    imageSize: [100, 100],
    hasRoutePath: true,
    routePath: { semanticPointIndexes: [0, 2, 5] }
  };
  page.currentPlan = { legs: [leg] };

  page.startStepLeg(0);
  page.stepNavigation();
  now = 50;
  animationCallbacks[0]();

  assert.deepEqual({ x: page.data.markerX, y: page.data.markerY }, { x: 0, y: 4 });
  assert.equal(page.data.navInstruction, '直行约8米');
  assert.deepEqual(animatedDistances, [8]);
  assert.deepEqual(spokenPrompts, [
    { text: '准备出发，请沿路线直行', key: 'leg-0:step-0' },
    { text: '直行约8米', key: 'leg-0:step-2' }
  ]);

  now = 100;
  animationCallbacks[0]();
  assert.equal(page.stepNavPointIndex, 2);
  assert.deepEqual(spokenPrompts.map(prompt => prompt.key), [
    'leg-0:step-0',
    'leg-0:step-2'
  ]);

  page.stepNavigation();
  assert.equal(page.data.navInstruction, '继续直行约12米，到达destination');
  assert.deepEqual(animatedDistances, [8, 12]);
  assert.deepEqual(spokenPrompts.at(-1), {
    text: '继续直行约12米，到达destination',
    key: 'leg-0:step-5'
  });

  now = 150;
  animationCallbacks[1]();
  assert.deepEqual({ x: page.data.markerX, y: page.data.markerY }, { x: 8, y: 2 });

  now = 200;
  animationCallbacks[1]();
  assert.equal(page.stepNavPointIndex, 5);
  assert.deepEqual(spokenPrompts.map(prompt => prompt.key), [
    'leg-0:step-0',
    'leg-0:step-2',
    'leg-0:step-5'
  ]);
});

test('step navigation does not announce elevator arrival before walking animation completes', t => {
  const loaded = loadPage();
  const page = createPageInstance(loaded.definition);
  const spokenPrompts = [];
  const animationCallbacks = [];
  const originalSetInterval = global.setInterval;
  const originalDateNow = Date.now;

  global.setInterval = callback => {
    animationCallbacks.push(callback);
    return animationCallbacks.length;
  };
  Date.now = () => 0;

  t.after(() => {
    global.setInterval = originalSetInterval;
    Date.now = originalDateNow;
    page.clearStepAnimation();
    loaded.restore();
  });

  page.getStepSegmentDuration = () => 100;
  page.speakNavigationPrompt = (text, key) => {
    spokenPrompts.push({ text, key });
  };
  const transferInstruction = '已到达一号电梯，请乘坐一号电梯前往2楼';
  page.currentPlan = {
    legs: [
      {
        title: '前往一号电梯',
        floor: '1楼',
        arrivalName: '一号电梯',
        transferFloor: '2楼',
        transferInstruction,
        points: [[0, 0], [2, 0]],
        imageSize: [100, 100],
        hasRoutePath: true,
        routePath: { semanticPointIndexes: [0, 1] }
      },
      {
        title: '从一号电梯前往目的地',
        floor: '2楼',
        arrivalName: '目的地',
        points: [[2, 0], [4, 0]],
        imageSize: [100, 100],
        hasRoutePath: true,
        routePath: { semanticPointIndexes: [0, 1] }
      }
    ]
  };

  page.startStepLeg(0);
  page.stepNavigation();

  assert.equal(animationCallbacks.length, 1);
  assert.equal(spokenPrompts.some(prompt => prompt.text.includes('已到达电梯')), false);
  assert.equal(spokenPrompts.some(prompt => prompt.text.includes('已到达一号电梯')), false);
  assert.equal(page.data.navInstruction.includes('已到达一号电梯'), false);
});

test('step navigation sets and speaks the exact transfer instruction once at animation completion', t => {
  const loaded = loadPage();
  const page = createPageInstance(loaded.definition);
  const spokenPrompts = [];
  const animationCallbacks = [];
  const originalSetInterval = global.setInterval;
  const originalDateNow = Date.now;
  let now = 0;

  global.setInterval = callback => {
    animationCallbacks.push(callback);
    return animationCallbacks.length;
  };
  Date.now = () => now;

  t.after(() => {
    global.setInterval = originalSetInterval;
    Date.now = originalDateNow;
    page.clearStepAnimation();
    loaded.restore();
  });

  page.getStepSegmentDuration = () => 100;
  page.speakNavigationPrompt = (text, key) => {
    spokenPrompts.push({ text, key });
  };
  const transferInstruction = '已到达一号电梯，请乘坐一号电梯前往2楼';
  page.currentPlan = {
    legs: [
      {
        title: '前往一号电梯',
        floor: '1楼',
        arrivalName: '一号电梯',
        transferFloor: '2楼',
        transferInstruction,
        points: [[0, 0], [2, 0]],
        imageSize: [100, 100],
        hasRoutePath: true,
        routePath: { semanticPointIndexes: [0, 1] }
      },
      {
        title: '从一号电梯前往目的地',
        floor: '2楼',
        arrivalName: '目的地',
        points: [[2, 0], [4, 0]],
        imageSize: [100, 100],
        hasRoutePath: true,
        routePath: { semanticPointIndexes: [0, 1] }
      }
    ]
  };

  page.startStepLeg(0);
  page.stepNavigation();
  now = 100;
  animationCallbacks[0]();

  assert.equal(page.data.navInstruction, transferInstruction);
  assert.deepEqual(
    spokenPrompts.filter(prompt => prompt.text === transferInstruction),
    [{ text: transferInstruction, key: 'leg-0:transfer-arrival' }]
  );
});

test('step navigation advances safely across a completely zero-length defensive route', t => {
  const loaded = loadPage();
  const page = createPageInstance(loaded.definition);
  t.after(() => {
    page.clearStepAnimation();
    loaded.restore();
  });

  page.currentPlan = {
    legs: [{
      title: '防御性零长度路线',
      floor: '1楼',
      arrivalName: '目的地',
      points: [[1, 1], [1, 1], [1, 1]],
      imageSize: [100, 100],
      hasRoutePath: true,
      routePath: { semanticPointIndexes: [0, 1, 2] }
    }]
  };

  page.startStepLeg(0);
  page.stepNavigation();

  assert.equal(page.stepNavPointIndex, 2);
  assert.equal(page.data.stepAnimating, false);
  assert.equal(page.data.navProgress, 100);
  assert.doesNotMatch(page.data.navInstruction, /约0米/);
});

test('replay buttons force the current semantic prompt without moving navigation state', t => {
  const loaded = loadPage();
  const page = createPageInstance(loaded.definition);
  t.after(() => loaded.restore());

  const calls = [];
  page.speakNavigationPrompt = loaded.definition.speakNavigationPrompt;
  page.speakText = (text, options) => {
    calls.push({ text, options });
    return { ok: true };
  };
  page.currentNavigationPrompt = {
    text: '前方左转后继续直行',
    key: 'leg-1:step-4'
  };
  page.currentPlan = { legs: [{}, {}] };
  page.stepNavLegIndex = 1;
  page.stepNavPointIndex = 4;
  page.setData({
    currentImageIndex: 1,
    markerX: 38.5,
    markerY: 44.25,
    markerAngle: 90,
    navProgress: 63,
    navDistanceText: '18m',
    navEtaText: '16秒',
    navInstruction: '前方左转后继续直行',
    stepMode: true,
    stepAnimating: false
  });
  const navigationSnapshot = () => ({
    legIndex: page.stepNavLegIndex,
    pointIndex: page.stepNavPointIndex,
    currentImageIndex: page.data.currentImageIndex,
    markerX: page.data.markerX,
    markerY: page.data.markerY,
    markerAngle: page.data.markerAngle,
    progress: page.data.navProgress,
    distance: page.data.navDistanceText,
    eta: page.data.navEtaText,
    instruction: page.data.navInstruction,
    stepMode: page.data.stepMode,
    stepAnimating: page.data.stepAnimating
  });
  const before = navigationSnapshot();

  page.replayNavigation();
  page.replayCurrentStep();

  assert.deepEqual(calls, [
    {
      text: '前方左转后继续直行',
      options: { key: 'leg-1:step-4', source: 'navigation', force: true }
    },
    {
      text: '前方左转后继续直行',
      options: { key: 'leg-1:step-4', source: 'navigation', force: true }
    }
  ]);
  assert.deepEqual(navigationSnapshot(), before);
});
