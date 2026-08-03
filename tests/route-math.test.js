const assert = require('node:assert/strict');
const test = require('node:test');

const routeMath = require('../miniprogram/utils/routeMath.js');

const portraitImageSize = [100, 200];

function getSemanticInstructionBuilder() {
  assert.equal(
    typeof routeMath.buildSemanticStepInstruction,
    'function',
    'routeMath must expose buildSemanticStepInstruction for raw-polyline guidance'
  );
  return routeMath.buildSemanticStepInstruction;
}

function getSpokenStepPathBuilder() {
  assert.equal(
    typeof routeMath.buildSpokenStepPath,
    'function',
    'routeMath must expose buildSpokenStepPath for runtime speech-safe steps'
  );
  return routeMath.buildSpokenStepPath;
}

test('route metrics scale vertical coordinates by the map aspect ratio', () => {
  const from = { x: 0, y: 0 };
  const verticalTo = { x: 0, y: 10 };
  const diagonalTo = { x: 10, y: 10 };

  assert.equal(routeMath.getSegmentLength(from, verticalTo, portraitImageSize), 20);
  assert.equal(
    routeMath.getSegmentAngle(from, diagonalTo, portraitImageSize),
    Math.atan2(20, 10) * 180 / Math.PI + 90
  );
  assert.equal(
    routeMath.buildPathMetrics([[0, 0], [0, 10]], portraitImageSize).total,
    20
  );
});

test('turn and step instructions use the same aspect-corrected vectors', () => {
  const points = [[0, 0], [10, 0], [20, 2.5]];
  const normalized = routeMath.normalizePoints(points);

  assert.equal(
    routeMath.getTurnDirection(normalized[0], normalized[1], normalized[2], portraitImageSize),
    '右转后直行'
  );

  const instruction = routeMath.buildStepInstruction(points, 2, {
    arrivalName: '检验科',
    distanceMetersPerPercent: 1,
    imageSize: portraitImageSize
  });
  assert.equal(instruction.text, '右转后直行约11米，到达检验科');
});

test('omitting imageSize preserves square-map geometry', () => {
  const from = { x: 0, y: 0 };
  const to = { x: 3, y: 4 };

  assert.equal(routeMath.getSegmentLength(from, to), 5);
  assert.equal(
    routeMath.getSegmentAngle(from, to),
    Math.atan2(4, 3) * 180 / Math.PI + 90
  );
  assert.equal(routeMath.buildPathMetrics([[0, 0], [3, 4]]).total, 5);
});

test('getImageAspect exposes valid ratios and defaults invalid sizes to one', () => {
  assert.equal(typeof routeMath.getImageAspect, 'function');
  if (typeof routeMath.getImageAspect !== 'function') return;

  assert.equal(routeMath.getImageAspect(portraitImageSize), 2);
  assert.equal(routeMath.getImageAspect(), 1);
  assert.equal(routeMath.getImageAspect([0, 200]), 1);
});

test('semantic step paths select exactly the generator semantic destinations', () => {
  const points = [[0, 0], [2, 1], [4, 2], [6, 2], [8, 1], [10, 0]];

  const stepPath = routeMath.buildSemanticStepPath(points, [0, 2, 5]);

  assert.deepEqual(stepPath.rawPointIndexes, [0, 2, 5]);
  assert.deepEqual(stepPath.points, [
    { x: 0, y: 0 },
    { x: 4, y: 2 },
    { x: 10, y: 0 }
  ]);
});

test('semantic step paths insert missing route endpoints', () => {
  const stepPath = routeMath.buildSemanticStepPath(
    [[0, 0], [2, 1], [4, 2]],
    [1]
  );

  assert.deepEqual(stepPath.rawPointIndexes, [0, 1, 2]);
});

test('semantic step paths use every raw point when metadata is malformed', () => {
  const points = [[0, 0], [2, 1], [4, 2]];

  const stepPath = routeMath.buildSemanticStepPath(points, [0, 1.5, 2]);

  assert.deepEqual(stepPath.rawPointIndexes, [0, 1, 2]);
  assert.deepEqual(stepPath.points, [
    { x: 0, y: 0 },
    { x: 2, y: 1 },
    { x: 4, y: 2 }
  ]);
});

test('spoken step paths merge an internal semantic segment that would round to zero metres', () => {
  const buildSpokenStepPath = getSpokenStepPathBuilder();
  const points = [[0, 0], [0.3, 0], [1.3, 0], [3.3, 0]];

  const stepPath = buildSpokenStepPath(points, [0, 1, 2, 3], {
    imageSize: [100, 100],
    distanceMetersPerPercent: 1.2,
    minimumSpokenStepMeters: 0.5,
    distanceRoundingMeters: 1
  });

  assert.deepEqual(stepPath.rawPointIndexes, [0, 2, 3]);
  assert.deepEqual(stepPath.points, [
    { x: 0, y: 0 },
    { x: 1.3, y: 0 },
    { x: 3.3, y: 0 }
  ]);
});

test('spoken step paths merge a sub-metre tail into the preceding instruction', () => {
  const buildSpokenStepPath = getSpokenStepPathBuilder();
  const points = [[0, 0], [2, 0], [2.3, 0]];

  const stepPath = buildSpokenStepPath(points, [0, 1, 2], {
    imageSize: [100, 100],
    distanceMetersPerPercent: 1.2,
    minimumSpokenStepMeters: 0.5,
    distanceRoundingMeters: 1
  });

  assert.deepEqual(stepPath.rawPointIndexes, [0, 2]);
  assert.deepEqual(stepPath.points, [
    { x: 0, y: 0 },
    { x: 2.3, y: 0 }
  ]);
});

test('spoken step paths skip a consecutive semantic point at the same coordinate', () => {
  const buildSpokenStepPath = getSpokenStepPathBuilder();
  const points = [[0, 0], [0, 0], [2, 0]];

  const stepPath = buildSpokenStepPath(points, [0, 1, 2], {
    imageSize: [100, 100],
    distanceMetersPerPercent: 1.2,
    minimumSpokenStepMeters: 0.5,
    distanceRoundingMeters: 1
  });

  assert.deepEqual(stepPath.rawPointIndexes, [0, 2]);
  assert.deepEqual(stepPath.points, [
    { x: 0, y: 0 },
    { x: 2, y: 0 }
  ]);
});

test('spoken step paths preserve endpoints and monotonically cover every raw slice', () => {
  const buildSpokenStepPath = getSpokenStepPathBuilder();
  const points = [[0, 0], [0.2, 0], [1, 0], [2, 0], [2.2, 0], [4, 0]];

  const stepPath = buildSpokenStepPath(points, [0, 1, 3, 4, 5], {
    imageSize: [100, 100],
    distanceMetersPerPercent: 1.2,
    minimumSpokenStepMeters: 0.5,
    distanceRoundingMeters: 1
  });

  assert.equal(stepPath.rawPointIndexes[0], 0);
  assert.equal(stepPath.rawPointIndexes.at(-1), points.length - 1);
  assert.ok(stepPath.rawPointIndexes.every((index, position, indexes) => (
    position === 0 || index > indexes[position - 1]
  )));

  const coveredRawIndexes = [];
  for (let index = 1; index < stepPath.rawPointIndexes.length; index += 1) {
    const from = stepPath.rawPointIndexes[index - 1];
    const to = stepPath.rawPointIndexes[index];
    for (let rawIndex = from; rawIndex <= to; rawIndex += 1) {
      if (coveredRawIndexes.at(-1) !== rawIndex) coveredRawIndexes.push(rawIndex);
    }
  }
  assert.deepEqual(coveredRawIndexes, [0, 1, 2, 3, 4, 5]);
});

test('semantic step instructions sum the complete raw polyline between destinations', () => {
  const buildSemanticStepInstruction = getSemanticInstructionBuilder();
  const rawPoints = [[0, 0], [0, 4], [4, 4]];

  const instruction = buildSemanticStepInstruction(rawPoints, [0, 2], 1, {
    arrivalName: '检验科',
    distanceMetersPerPercent: 1,
    imageSize: [100, 100]
  });

  assert.equal(instruction.text, '直行约8米，到达检验科');
  assert.deepEqual(instruction.point, { x: 4, y: 4 });
  assert.equal(instruction.progress, 100);
  assert.equal(instruction.isArrival, true);
});

test('semantic step instructions derive turns from adjacent raw segments at departure', () => {
  const buildSemanticStepInstruction = getSemanticInstructionBuilder();
  const rawPoints = [
    [0, 0],
    [0, 4],
    [4, 4],
    [8, 4],
    [8, 0],
    [12, 0]
  ];

  const instruction = buildSemanticStepInstruction(rawPoints, [0, 2, 5], 2, {
    arrivalName: 'destination',
    distanceMetersPerPercent: 1,
    imageSize: [100, 100]
  });

  assert.equal(instruction.text, '继续直行约12米，到达destination');
  assert.deepEqual(instruction.point, { x: 12, y: 0 });
});

test('semantic step instructions integerize a fractional target index safely', () => {
  const buildSemanticStepInstruction = getSemanticInstructionBuilder();
  const rawPoints = [[0, 0], [1, 0], [3, 0]];

  const instruction = buildSemanticStepInstruction(rawPoints, [0, 1, 2], 1.8, {
    arrivalName: '目的地',
    distanceMetersPerPercent: 1,
    imageSize: [100, 100]
  });

  assert.equal(instruction.text, '直行约1米');
  assert.deepEqual(instruction.point, { x: 1, y: 0 });
  assert.equal(instruction.progress, 50);
  assert.equal(instruction.isArrival, false);
  assert.doesNotMatch(instruction.text, /约0米|undefined/);
});

test('semantic transfer approach keeps the target floor without announcing arrival early', () => {
  const buildSemanticStepInstruction = getSemanticInstructionBuilder();
  const instruction = buildSemanticStepInstruction([[0, 0], [2, 0]], [0, 1], 1, {
    hasNextLeg: true,
    transferFloor: '2楼',
    distanceMetersPerPercent: 1,
    imageSize: [100, 100]
  });

  assert.equal(instruction.text, '直行约2米，到达电梯后，请乘坐电梯前往2楼');
  assert.doesNotMatch(instruction.text, /已到达/);
});

test('merged spoken steps retain an immediate internal turn in one composite instruction', () => {
  const rawPoints = [[0, 0], [0.3, 0], [0.3, 5]];
  const stepPath = routeMath.buildSpokenStepPath(rawPoints, [0, 1, 2], {
    imageSize: [100, 100],
    distanceMetersPerPercent: 1.2,
    minimumSpokenStepMeters: 0.5,
    distanceRoundingMeters: 1
  });
  assert.deepEqual(stepPath.rawPointIndexes, [0, 2]);

  const instruction = routeMath.buildSemanticStepInstruction(
    rawPoints,
    stepPath.rawPointIndexes,
    1,
    {
      arrivalName: '目的地',
      sourceSemanticPointIndexes: [0, 1, 2],
      distanceMetersPerPercent: 1.2,
      distanceRoundingMeters: 1,
      imageSize: [100, 100]
    }
  );

  assert.equal(
    instruction.text,
    '直行，前方即到转向点；右转后直行约6米，到达目的地'
  );
  assert.deepEqual(instruction.sourceRawPointIndexes, [0, 1, 2]);
});

test('merged spoken steps retain a final short turn without saying zero metres', () => {
  const rawPoints = [[0, 0], [5, 0], [5, 0.3]];
  const stepPath = routeMath.buildSpokenStepPath(rawPoints, [0, 1, 2], {
    imageSize: [100, 100],
    distanceMetersPerPercent: 1.2,
    minimumSpokenStepMeters: 0.5,
    distanceRoundingMeters: 1
  });
  assert.deepEqual(stepPath.rawPointIndexes, [0, 2]);

  const instruction = routeMath.buildSemanticStepInstruction(
    rawPoints,
    stepPath.rawPointIndexes,
    1,
    {
      arrivalName: '目的地',
      sourceSemanticPointIndexes: [0, 1, 2],
      distanceMetersPerPercent: 1.2,
      distanceRoundingMeters: 1,
      imageSize: [100, 100]
    }
  );

  assert.equal(
    instruction.text,
    '直行约6米；右转后直行，前方即到达目的地'
  );
  assert.doesNotMatch(instruction.text, /约0米/);
  assert.deepEqual(instruction.sourceRawPointIndexes, [0, 1, 2]);
});
