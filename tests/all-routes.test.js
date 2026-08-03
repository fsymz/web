const assert = require('node:assert/strict');
const test = require('node:test');

const floorNavPaths = require('../miniprogram/data/floorNavPaths.js');
const sameFloorPaths = require('../miniprogram/data/sameFloorPaths.js');
const elevatorGroups = require('../miniprogram/data/elevatorGroups.js');
const elevatorShafts = require('../miniprogram/data/elevatorShafts.js');
const planner = require('../miniprogram/utils/elevatorPlanner.js');
const routeMath = require('../miniprogram/utils/routeMath.js');
const routes = require('../miniprogram/data/routes.js');
const departmentAnchors = require('../config/department-anchors.json');
const routingPolicy = require('../config/routing-policy.json');
const {
  maxAnchorSnapPxForFloor,
  validateDepartmentEndpoint,
  validateElevatorEndpoint,
} = require('../scripts/check-routes.js');

const COLOCATED_MESSAGE = '当前位置与目的地位于同一区域，请根据现场标识确认';
const NO_COMMON_ELEVATOR = {
  ok: false,
  status: 'noCommonElevator',
  message: '当前楼层与目标楼层没有已确认可直达的同一电梯，请咨询导医台或现场工作人员。',
  legs: []
};

function hasDistinctFinitePoints(points) {
  if (!Array.isArray(points) || points.length < 2) return false;
  if (!points.every(point => (
    Array.isArray(point)
    && point.length >= 2
    && Number.isFinite(point[0])
    && Number.isFinite(point[1])
  ))) return false;
  return points.some(point => point[0] !== points[0][0] || point[1] !== points[0][1]);
}

function imageWidthPercentLength(points, imageSize) {
  const [width, height] = imageSize;
  const aspect = height / width;
  let total = 0;
  for (let index = 1; index < points.length; index += 1) {
    const dx = points[index][0] - points[index - 1][0];
    const dy = points[index][1] - points[index - 1][1];
    total += Math.sqrt(dx * dx + (dy * aspect) * (dy * aspect));
  }
  return total;
}

function expectedRawDepartureTurn(points, departureIndex, imageSize) {
  const current = points[departureIndex];
  let previousIndex = departureIndex - 1;
  while (previousIndex >= 0
    && points[previousIndex][0] === current[0]
    && points[previousIndex][1] === current[1]) {
    previousIndex -= 1;
  }
  let nextIndex = departureIndex + 1;
  while (nextIndex < points.length
    && points[nextIndex][0] === current[0]
    && points[nextIndex][1] === current[1]) {
    nextIndex += 1;
  }
  if (previousIndex < 0 || nextIndex >= points.length) return '直行';

  const aspect = imageSize[1] / imageSize[0];
  const ax = current[0] - points[previousIndex][0];
  const ay = (current[1] - points[previousIndex][1]) * aspect;
  const bx = points[nextIndex][0] - current[0];
  const by = (points[nextIndex][1] - current[1]) * aspect;
  const lenA = Math.sqrt(ax * ax + ay * ay);
  const lenB = Math.sqrt(bx * bx + by * by);
  const cosine = Math.max(-1, Math.min(1, (ax * bx + ay * by) / (lenA * lenB)));
  const degrees = Math.acos(cosine) * 180 / Math.PI;
  if (degrees < 25) return '继续直行';
  if (degrees > 145) return '掉头后直行';
  return ax * by - ay * bx > 0 ? '右转后直行' : '左转后直行';
}

function assertEndpoint(result, label) {
  assert.equal(result.ok, true, `${label}: ${result.message || 'endpoint rejected'}`);
}

function expectedCandidates(fromRoute, toRoute) {
  return elevatorShafts.map(shaft => ({
    shaft,
    toElevatorPath: floorNavPaths[
      planner.getElevatorPathKey(fromRoute.name, shaft.shaftId, 'toElevator')
    ],
    fromElevatorPath: floorNavPaths[
      planner.getElevatorPathKey(toRoute.name, shaft.shaftId, 'fromElevator')
    ]
  })).filter(candidate => (
    planner.isValidElevatorPath(
      candidate.toElevatorPath,
      fromRoute,
      candidate.shaft,
      'toElevator'
    )
    && planner.isValidElevatorPath(
      candidate.fromElevatorPath,
      toRoute,
      candidate.shaft,
      'fromElevator'
    )
  )).sort((left, right) => (
    left.toElevatorPath.routeLength - right.toElevatorPath.routeLength
    || (left.shaft.shaftId < right.shaft.shaftId ? -1 : 1)
  ));
}

test('generated runtime data contains all independent shaft and same-floor records', () => {
  assert.equal(routes.getDepartmentNames().length, 42);
  assert.equal(Object.keys(floorNavPaths).length, 470);
  assert.equal(Object.keys(sameFloorPaths).length, 260);
  assert.equal(elevatorShafts.length, 7);
  assert.equal(
    elevatorShafts.reduce((count, shaft) => count + Object.keys(shaft.floorMappings).length, 0),
    53
  );
  for (const shaft of elevatorShafts) {
    for (const mapping of Object.values(shaft.floorMappings)) {
      assert.equal(mapping.confirmed, true);
      assert.equal(Array.isArray(mapping.elevatorAnchor), true);
      assert.equal(mapping.elevatorAnchor.length, 2);
    }
  }
  const anchorByName = Object.fromEntries(departmentAnchors.map(item => [item.name, item]));
  const shaftById = Object.fromEntries(elevatorShafts.map(item => [item.shaftId, item]));
  for (const [key, pathRecord] of Object.entries(floorNavPaths)) {
    assert.equal(pathRecord.routeLengthUnit, 'imageWidthPercent');
    assert.equal(hasDistinctFinitePoints(pathRecord.points), true);
    assert.equal(
      key,
      `${pathRecord.departmentName}|||${pathRecord.shaftId}|||${pathRecord.direction}`
    );
    const department = anchorByName[pathRecord.departmentName];
    const maxAnchorSnapPx = maxAnchorSnapPxForFloor(routingPolicy, pathRecord.floor);
    const mapping = shaftById[pathRecord.shaftId].floorMappings[pathRecord.floor];
    assertEndpoint(
      validateElevatorEndpoint(pathRecord.elevatorAnchor, mapping.elevatorAnchor, pathRecord.imageSize),
      `${key}: item.elevatorAnchor`
    );
    if (pathRecord.direction === 'toElevator') {
      assertEndpoint(
        validateDepartmentEndpoint(
          pathRecord.points[0], department, pathRecord.imageSize, maxAnchorSnapPx
        ),
        `${key}: department start`
      );
      assertEndpoint(
        validateElevatorEndpoint(
          pathRecord.points.at(-1), mapping.elevatorAnchor, pathRecord.imageSize
        ),
        `${key}: elevator end`
      );
    } else {
      assert.equal(pathRecord.direction, 'fromElevator', key);
      assertEndpoint(
        validateElevatorEndpoint(
          pathRecord.points[0], mapping.elevatorAnchor, pathRecord.imageSize
        ),
        `${key}: elevator start`
      );
      assertEndpoint(
        validateDepartmentEndpoint(
          pathRecord.points.at(-1), department, pathRecord.imageSize, maxAnchorSnapPx
        ),
        `${key}: department end`
      );
    }
    assert.ok(
      Math.abs(
        pathRecord.routeLength
        - imageWidthPercentLength(pathRecord.points, pathRecord.imageSize)
      ) < 1e-6,
      key
    );
    const group = elevatorGroups[pathRecord.floor].find(
      item => item.id === pathRecord.elevatorGroupId
    );
    const anchorPixel = [
      pathRecord.elevatorAnchor[0] / 100 * pathRecord.imageSize[0],
      pathRecord.elevatorAnchor[1] / 100 * pathRecord.imageSize[1]
    ];
    assert.equal(
      group.bbox[0] <= anchorPixel[0]
        && anchorPixel[0] <= group.bbox[2]
        && group.bbox[1] <= anchorPixel[1]
        && anchorPixel[1] <= group.bbox[3],
      false,
      `${key}: corridor anchor is outside elevator rectangle`
    );
  }
  for (const [key, pathRecord] of Object.entries(sameFloorPaths)) {
    const [sourceName, targetName] = key.split('|||');
    const source = anchorByName[sourceName];
    const target = anchorByName[targetName];
    const maxAnchorSnapPx = maxAnchorSnapPxForFloor(routingPolicy, pathRecord.floor);
    assertEndpoint(
      validateDepartmentEndpoint(
        pathRecord.points[0], source, pathRecord.imageSize, maxAnchorSnapPx
      ),
      `${key}: source endpoint`
    );
    if (pathRecord.coLocated === true) {
      assert.equal(pathRecord.points.length, 1, key);
      assert.equal(pathRecord.routeLength, 0, key);
    } else {
      assert.equal(hasDistinctFinitePoints(pathRecord.points), true, key);
      assertEndpoint(
        validateDepartmentEndpoint(
          pathRecord.points.at(-1), target, pathRecord.imageSize, maxAnchorSnapPx
        ),
        `${key}: target endpoint`
      );
      assert.ok(
        Math.abs(
          pathRecord.routeLength
          - imageWidthPercentLength(pathRecord.points, pathRecord.imageSize)
        ) < 1e-6,
        key
      );
    }
  }
});

test('all 730 production routes keep semantic guidance aligned with complete raw slices', () => {
  assert.equal(
    typeof routeMath.buildSemanticStepInstruction,
    'function',
    'routeMath must expose buildSemanticStepInstruction for production-route validation'
  );

  const records = [
    ...Object.entries(floorNavPaths),
    ...Object.entries(sameFloorPaths)
  ];
  assert.equal(records.length, 730);

  let checkedRoutes = 0;
  let coLocatedRoutes = 0;
  for (const [key, record] of records) {
    checkedRoutes += 1;
    if (record.coLocated === true) {
      coLocatedRoutes += 1;
      assert.equal(record.points.length, 1, key);
      continue;
    }

    assert.ok(Array.isArray(record.semanticPointIndexes), `${key}: semanticPointIndexes`);
    const stepPath = routeMath.buildSemanticStepPath(
      record.points,
      record.semanticPointIndexes
    );
    assert.deepEqual(
      stepPath.rawPointIndexes,
      record.semanticPointIndexes,
      `${key}: semantic indexes must be used without fallback`
    );

    for (let targetSemanticIndex = 1;
      targetSemanticIndex < stepPath.rawPointIndexes.length;
      targetSemanticIndex += 1) {
      const fromRawIndex = stepPath.rawPointIndexes[targetSemanticIndex - 1];
      const toRawIndex = stepPath.rawPointIndexes[targetSemanticIndex];
      const rawSlice = record.points.slice(fromRawIndex, toRawIndex + 1);
      const expectedMeters = Math.round(imageWidthPercentLength(rawSlice, record.imageSize));
      const instruction = routeMath.buildSemanticStepInstruction(
        record.points,
        stepPath.rawPointIndexes,
        targetSemanticIndex,
        {
          arrivalName: '目的地',
          distanceMetersPerPercent: 1,
          imageSize: record.imageSize
        }
      );

      if (expectedMeters > 0) {
        assert.match(
          instruction.text,
          new RegExp(`约${expectedMeters}米`),
          `${key}: semantic segment ${targetSemanticIndex} must use raw-slice distance`
        );
      } else {
        assert.match(
          instruction.text,
          /前方即到/,
          `${key}: sub-metre semantic segment ${targetSemanticIndex} must avoid zero-metre speech`
        );
        assert.doesNotMatch(instruction.text, /约0米/, key);
      }
      if (targetSemanticIndex >= 2) {
        const expectedTurn = expectedRawDepartureTurn(
          record.points,
          fromRawIndex,
          record.imageSize
        );
        assert.ok(
          instruction.text.startsWith(expectedTurn),
          `${key}: semantic segment ${targetSemanticIndex} must use local raw turn ${expectedTurn}`
        );
      }
      assert.deepEqual(instruction.point, {
        x: record.points[toRawIndex][0],
        y: record.points[toRawIndex][1]
      }, `${key}: semantic segment ${targetSemanticIndex} target`);

      if (key === '产科门诊|||S7|||fromElevator' && targetSemanticIndex === 2) {
        assert.match(instruction.text, /^右转后直行约/);
        assert.doesNotMatch(instruction.text, /^左转后直行/);
      }
    }
  }

  assert.equal(checkedRoutes, 730);
  assert.equal(coLocatedRoutes, 10);
});

test('all 730 production routes build runtime spoken steps that round to at least one metre', () => {
  assert.equal(
    typeof routeMath.buildSpokenStepPath,
    'function',
    'routeMath must expose buildSpokenStepPath for production runtime validation'
  );

  const records = [
    ...Object.entries(floorNavPaths),
    ...Object.entries(sameFloorPaths)
  ];
  assert.equal(records.length, 730);

  let checkedRoutes = 0;
  let checkedSpokenSteps = 0;
  for (const [key, record] of records) {
    checkedRoutes += 1;
    if (record.coLocated === true) continue;

    const stepPath = routeMath.buildSpokenStepPath(
      record.points,
      record.semanticPointIndexes,
      {
        imageSize: record.imageSize,
        distanceMetersPerPercent: 1.2,
        minimumSpokenStepMeters: 0.5,
        distanceRoundingMeters: 1
      }
    );
    assert.ok(
      stepPath.rawPointIndexes.length - 1 <= 10,
      `${key}: runtime route must not expose more than 10 movement steps`
    );
    assert.equal(stepPath.rawPointIndexes[0], 0, `${key}: runtime start endpoint`);
    assert.equal(
      stepPath.rawPointIndexes.at(-1),
      record.points.length - 1,
      `${key}: runtime end endpoint`
    );
    assert.ok(
      stepPath.rawPointIndexes.every((index, position, indexes) => (
        Number.isInteger(index)
        && (position === 0 || index > indexes[position - 1])
      )),
      `${key}: runtime indexes must be strictly increasing`
    );

    for (let targetSemanticIndex = 1;
      targetSemanticIndex < stepPath.rawPointIndexes.length;
      targetSemanticIndex += 1) {
      checkedSpokenSteps += 1;
      const fromRawIndex = stepPath.rawPointIndexes[targetSemanticIndex - 1];
      const toRawIndex = stepPath.rawPointIndexes[targetSemanticIndex];
      const rawSlice = record.points.slice(fromRawIndex, toRawIndex + 1);
      const roundedMeters = Math.round(
        imageWidthPercentLength(rawSlice, record.imageSize) * 1.2
      );
      assert.ok(
        roundedMeters >= 1,
        `${key}: runtime spoken segment ${targetSemanticIndex} rounded to ${roundedMeters}m`
      );

      const instruction = routeMath.buildSemanticStepInstruction(
        record.points,
        stepPath.rawPointIndexes,
        targetSemanticIndex,
        {
          arrivalName: '目的地',
          sourceSemanticPointIndexes: record.semanticPointIndexes,
          distanceMetersPerPercent: 1.2,
          imageSize: record.imageSize
        }
      );
      assert.doesNotMatch(
        instruction.text,
        /约0米/,
        `${key}: runtime spoken segment ${targetSemanticIndex} must not announce zero metres`
      );
      const expectedSourceIndexes = record.semanticPointIndexes.filter(index => (
        index >= fromRawIndex && index <= toRawIndex
      ));
      if (expectedSourceIndexes[0] !== fromRawIndex) expectedSourceIndexes.unshift(fromRawIndex);
      if (expectedSourceIndexes.at(-1) !== toRawIndex) expectedSourceIndexes.push(toRawIndex);
      assert.deepEqual(
        instruction.sourceRawPointIndexes,
        expectedSourceIndexes,
        `${key}: merged instruction must retain every source semantic turn`
      );
      for (let sourceIndex = 1; sourceIndex < expectedSourceIndexes.length - 1; sourceIndex += 1) {
        const turn = expectedRawDepartureTurn(
          record.points,
          expectedSourceIndexes[sourceIndex],
          record.imageSize
        );
        assert.ok(
          instruction.text.includes(turn),
          `${key}: merged instruction must speak internal turn ${turn}`
        );
      }
    }
  }

  assert.equal(checkedRoutes, 730);
  assert.ok(checkedSpokenSteps > 0);
});

test('routes starting from the pediatric ward stay within ten meaningful movement steps', () => {
  const departmentNames = routes.getDepartmentNames();

  for (const destinationName of departmentNames) {
    if (destinationName === '儿科病房') continue;

    const plan = routes.createNavigationPlan('儿科病房', destinationName);
    if (plan.status !== 'route') continue;

    let movementSteps = 0;
    for (const leg of plan.legs) {
      const stepPath = routeMath.buildSpokenStepPath(
        leg.points,
        leg.routePath && leg.routePath.semanticPointIndexes,
        {
          imageSize: leg.imageSize,
          distanceMetersPerPercent: 1.2,
          minimumSpokenStepMeters: 0.5,
          distanceRoundingMeters: 1
        }
      );
      movementSteps += Math.max(0, stepPath.rawPointIndexes.length - 1);
    }

    assert.ok(
      movementSteps <= 10,
      `儿科病房 -> ${destinationName}: ${movementSteps} meaningful movement steps`
    );
  }
});

test('all 42 by 41 directed destination pairs return only safe route outcomes', () => {
  const departmentNames = routes.getDepartmentNames();
  let evaluated = 0;
  let coLocated = 0;

  for (const fromName of departmentNames) {
    for (const toName of departmentNames) {
      if (fromName === toName) continue;
      evaluated += 1;
      const fromRoute = routes.getDepartmentRoute(fromName);
      const toRoute = routes.getDepartmentRoute(toName);
      const plan = routes.createNavigationPlan(fromName, toName);
      assert.ok(
        ['route', 'coLocated', 'noCommonElevator'].includes(plan.status),
        `${fromName} -> ${toName}: unexpected status ${plan.status}`
      );

      if (plan.status === 'coLocated') {
        coLocated += 1;
        assert.equal(plan.ok, true, `${fromName} -> ${toName}`);
        assert.equal(plan.message, COLOCATED_MESSAGE, `${fromName} -> ${toName}`);
        assert.deepEqual(plan.legs, [], `${fromName} -> ${toName}`);
        continue;
      }

      if (plan.status === 'noCommonElevator') {
        assert.deepEqual(plan, NO_COMMON_ELEVATOR, `${fromName} -> ${toName}`);
        continue;
      }

      assert.equal(plan.ok, true, `${fromName} -> ${toName}`);
      assert.equal(typeof plan.message, 'string', `${fromName} -> ${toName}`);
      const sameFloor = fromRoute.floor === toRoute.floor;
      assert.equal(plan.legs.length, sameFloor ? 1 : 2, `${fromName} -> ${toName}`);
      for (const leg of plan.legs) {
        assert.equal(
          hasDistinctFinitePoints(leg.points),
          true,
          `${fromName} -> ${toName}: ${leg.kind}`
        );
      }

      if (!sameFloor) {
        const candidates = expectedCandidates(fromRoute, toRoute);
        assert.ok(candidates.length > 0, `${fromName} -> ${toName}: expected candidate`);
        const expected = candidates[0];
        assert.equal(plan.selectedElevatorShaftId, expected.shaft.shaftId);
        assert.equal(plan.legs[0].selectedElevatorShaftId, expected.shaft.shaftId);
        assert.equal(plan.legs[1].selectedElevatorShaftId, expected.shaft.shaftId);
        assert.deepEqual(plan.legs[0].points, expected.toElevatorPath.points);
        assert.deepEqual(plan.legs[1].points, expected.fromElevatorPath.points);
      }
    }
  }

  assert.equal(evaluated, 42 * 41);
  assert.equal(coLocated, 10);
});

test('all five configured co-located pairs return the explicit outcome both ways', () => {
  const pairs = [
    ['中药房', '西药房'],
    ['耳鼻喉科门诊', '眼科门诊'],
    ['血液透析科', '内镜诊疗中心'],
    ['病理科', '重症医学科'],
    ['妇产科病房', '产房']
  ];
  for (const [left, right] of pairs) {
    for (const [fromName, toName] of [[left, right], [right, left]]) {
      assert.deepEqual(routes.createNavigationPlan(fromName, toName), {
        ok: true,
        status: 'coLocated',
        message: COLOCATED_MESSAGE,
        legs: []
      });
    }
  }
});

test('invalidated candidate paths return noCommonElevator without fallback points', () => {
  const fromName = '儿科门诊';
  const toName = '内二科病房';
  const sourceRoute = routes.getDepartmentRoute(fromName);
  const destinationRoute = routes.getDepartmentRoute(toName);
  const commonShafts = elevatorShafts.filter(shaft => (
    shaft.serviceFloors.includes(sourceRoute.floor)
    && shaft.serviceFloors.includes(destinationRoute.floor)
  ));
  const removed = [];
  try {
    for (const shaft of commonShafts) {
      const key = planner.getElevatorPathKey(fromName, shaft.shaftId, 'toElevator');
      removed.push([key, floorNavPaths[key]]);
      delete floorNavPaths[key];
    }
    assert.deepEqual(routes.createNavigationPlan(fromName, toName), NO_COMMON_ELEVATOR);
  } finally {
    for (const [key, value] of removed) floorNavPaths[key] = value;
  }
});
