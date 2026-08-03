const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const elevatorGroups = require('../miniprogram/data/elevatorGroups.js');
const planner = require('../miniprogram/utils/elevatorPlanner.js');
const routes = require('../miniprogram/data/routes.js');

const fromRoute = { name: '起点科室', floor: '1楼' };
const toRoute = { name: '终点科室', floor: '2楼' };

function makeShaft(shaftId, options) {
  const config = options || {};
  const groupId = config.elevatorGroupId || 'E1';
  return {
    shaftId,
    displayName: shaftId + '号电梯',
    patientAccessible: config.patientAccessible !== false,
    serviceFloors: ['1楼', '2楼'],
    floorMappings: {
      '1楼': {
        elevatorGroupId: groupId,
        elevatorAnchor: config.fromAnchor || [50, 50],
        confirmed: config.fromConfirmed !== false
      },
      '2楼': {
        elevatorGroupId: groupId,
        elevatorAnchor: config.toAnchor || [50, 50],
        confirmed: config.toConfirmed !== false
      }
    }
  };
}

function makePath(departmentRoute, shaft, direction, routeLength) {
  const mapping = shaft.floorMappings[departmentRoute.floor];
  return {
    departmentName: departmentRoute.name,
    shaftId: shaft.shaftId,
    direction,
    floor: departmentRoute.floor,
    elevatorGroupId: mapping.elevatorGroupId,
    elevatorAnchor: mapping.elevatorAnchor,
    points: [[10, 10], [20, 20]],
    routeLength,
    routeLengthUnit: 'imageWidthPercent'
  };
}

function makeCandidatePaths(shaft, sourceLength, destinationLength) {
  return {
    [shaft.shaftId + ':toElevator']: makePath(fromRoute, shaft, 'toElevator', sourceLength),
    [shaft.shaftId + ':fromElevator']: makePath(
      toRoute,
      shaft,
      'fromElevator',
      destinationLength === undefined ? 6 : destinationLength
    )
  };
}

function combineCandidatePaths(candidatePaths) {
  const floorNavPaths = {};
  for (const candidate of candidatePaths) {
    for (const pathRecord of Object.values(candidate)) {
      floorNavPaths[
        pathRecord.departmentName + '|||' + pathRecord.shaftId + '|||' + pathRecord.direction
      ] = pathRecord;
    }
  }
  return floorNavPaths;
}

test('path keys keep multiple shafts for the same department distinct', () => {
  const getKey = planner.getElevatorPathKey;

  assert.equal(
    getKey && getKey('科室', 'S1', 'toElevator'),
    '科室|||S1|||toElevator'
  );
  assert.equal(
    getKey && getKey('科室', 'S2', 'toElevator'),
    '科室|||S2|||toElevator'
  );
});

test('nearest shaft uses the source walkable route length instead of anchor proximity', () => {
  const s1 = makeShaft('S1', { fromAnchor: [80, 80] });
  const s2 = makeShaft('S2', { fromAnchor: [12, 12] });
  const floorNavPaths = combineCandidatePaths([
    makeCandidatePaths(s1, 8),
    makeCandidatePaths(s2, 12)
  ]);
  const select = planner.selectNearestElevatorShaft;
  const result = select && select({
    fromRoute,
    toRoute,
    shafts: [s1, s2],
    floorNavPaths
  });

  assert.equal(result && result.ok, true);
  assert.equal(result && result.selectedElevatorShaftId, 'S1');
  assert.equal(result && result.toElevatorPath.routeLength, 8);
});

test('equal source route lengths use ascending ASCII shaftId order', () => {
  const s2 = makeShaft('S2');
  const s1 = makeShaft('S1');
  const result = planner.selectNearestElevatorShaft({
    fromRoute,
    toRoute,
    shafts: [s2, s1],
    floorNavPaths: combineCandidatePaths([
      makeCandidatePaths(s2, 8),
      makeCandidatePaths(s1, 8)
    ])
  });

  assert.equal(result.selectedElevatorShaftId, 'S1');
});

test('a nearer shaft missing the destination fromElevator path is excluded', () => {
  const s1 = makeShaft('S1');
  const s2 = makeShaft('S2');
  const floorNavPaths = combineCandidatePaths([
    makeCandidatePaths(s1, 5),
    makeCandidatePaths(s2, 8)
  ]);
  delete floorNavPaths['终点科室|||S1|||fromElevator'];

  const result = planner.selectNearestElevatorShaft({
    fromRoute,
    toRoute,
    shafts: [s1, s2],
    floorNavPaths
  });

  assert.equal(result.selectedElevatorShaftId, 'S2');
  assert.equal(result.fromElevatorPath.shaftId, 'S2');
});

test('patient-inaccessible shafts and unconfirmed floor mappings are excluded', () => {
  const inaccessible = makeShaft('S1', { patientAccessible: false });
  const unconfirmed = makeShaft('S2', { toConfirmed: false });
  const verified = makeShaft('S3');
  const result = planner.selectNearestElevatorShaft({
    fromRoute,
    toRoute,
    shafts: [inaccessible, unconfirmed, verified],
    floorNavPaths: combineCandidatePaths([
      makeCandidatePaths(inaccessible, 3),
      makeCandidatePaths(unconfirmed, 5),
      makeCandidatePaths(verified, 8)
    ])
  });

  assert.equal(result.selectedElevatorShaftId, 'S3');
});

test('no commonly served verified shaft returns a safe noCommonElevator result', () => {
  const sourceFloorOnly = makeShaft('S1');
  sourceFloorOnly.serviceFloors = ['1楼'];

  const result = planner.selectNearestElevatorShaft({
    fromRoute,
    toRoute,
    shafts: [sourceFloorOnly],
    floorNavPaths: combineCandidatePaths([
      makeCandidatePaths(sourceFloorOnly, 4)
    ])
  });

  assert.equal(result.ok, false);
  assert.equal(result.status, 'noCommonElevator');
  assert.equal(typeof result.reason, 'string');
  assert.ok(result.reason.length > 0);
});

test('a complete matching verified path is valid', () => {
  const shaft = makeShaft('S1');
  const candidate = makePath(fromRoute, shaft, 'toElevator', 8);
  const isValid = planner.isValidElevatorPath;

  assert.equal(
    isValid && isValid(candidate, fromRoute, shaft, 'toElevator'),
    true
  );
});

test('invalid elevator paths are rejected, including a missing or different length unit', () => {
  const shaft = makeShaft('S1');
  const validPath = makePath(fromRoute, shaft, 'toElevator', 8);
  const invalidPaths = [
    ['department', { ...validPath, departmentName: '其他科室' }],
    ['floor', { ...validPath, floor: '2楼' }],
    ['shaft', { ...validPath, shaftId: 'S2' }],
    ['direction', { ...validPath, direction: 'fromElevator' }],
    ['one point', { ...validPath, points: [[10, 10]] }],
    ['duplicate points', { ...validPath, points: [[10, 10], [10, 10]] }],
    ['zero length', { ...validPath, routeLength: 0 }],
    ['infinite length', { ...validPath, routeLength: Number.POSITIVE_INFINITY }],
    ['missing unit', { ...validPath, routeLengthUnit: undefined }],
    ['different unit', { ...validPath, routeLengthUnit: 'imageHeightPercent' }],
    ['elevator group', { ...validPath, elevatorGroupId: 'E9' }]
  ];

  for (const [label, candidate] of invalidPaths) {
    assert.equal(
      planner.isValidElevatorPath(candidate, fromRoute, shaft, 'toElevator'),
      false,
      label
    );
  }
  assert.equal(
    planner.isValidElevatorPath(validPath, { name: '', floor: '1楼' }, shaft, 'toElevator'),
    false,
    'public department'
  );
});

test('selection excludes candidates with an invalid path record', () => {
  const s1 = makeShaft('S1');
  const s2 = makeShaft('S2');
  const floorNavPaths = combineCandidatePaths([
    makeCandidatePaths(s1, 4),
    makeCandidatePaths(s2, 8)
  ]);
  floorNavPaths['起点科室|||S1|||toElevator'].routeLengthUnit = 'imageHeightPercent';

  const result = planner.selectNearestElevatorShaft({
    fromRoute,
    toRoute,
    shafts: [s1, s2],
    floorNavPaths
  });

  assert.equal(result.selectedElevatorShaftId, 'S2');
});

test('co-located paths use explicit metadata or generic formatted-zero distance', () => {
  const isCoLocatedPath = routes.isCoLocatedPath;

  assert.equal(typeof isCoLocatedPath, 'function');
  assert.equal(isCoLocatedPath({ coLocated: true, points: [[10, 10]] }), true);
  assert.equal(isCoLocatedPath({ coLocated: false, routeLength: 0.4 }), true);
  assert.equal(isCoLocatedPath({ routeLength: 0.5 }), false);
});

test('missing or illegal path metrics cannot masquerade as co-located', () => {
  const invalid = [
    null,
    {},
    { routeLength: '0' },
    { routeLength: -0.1 },
    { routeLength: Number.NaN },
    { routeLength: Number.POSITIVE_INFINITY }
  ];
  for (const pathRecord of invalid) {
    assert.equal(routes.isCoLocatedPath(pathRecord), false);
  }
  assert.equal(routes.isCoLocatedPath({ routeLength: 0 }, 0), false);
  assert.equal(routes.isCoLocatedPath({ routeLength: 0 }, -1), false);
});

test('stable shaft config exactly matches the confirmed elevator groups on every service floor', () => {
  const configPath = path.resolve(__dirname, '..', 'config', 'elevator-shafts.json');
  assert.equal(fs.existsSync(configPath), true, 'elevator-shafts.json exists');

  const shafts = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  const floors1To13 = Array.from({ length: 13 }, (_, index) => (index + 1) + '楼');
  const floors1To4 = floors1To13.slice(0, 4);
  const expected = [
    { shaftId: 'S1', displayName: '1号电梯', groupId: 'E1', floors: floors1To13 },
    { shaftId: 'S2', displayName: '2号电梯', groupId: 'E2', floors: floors1To13 },
    { shaftId: 'S3', displayName: '3号电梯', groupId: 'E3', floors: floors1To13 },
    { shaftId: 'S4', displayName: '4号电梯', groupId: 'E4', floors: floors1To4 },
    { shaftId: 'S5', displayName: '5号电梯', groupId: 'E5', floors: floors1To4 },
    { shaftId: 'S6', displayName: '6号电梯', groupId: 'E6', floors: floors1To4 },
    { shaftId: 'S7', displayName: '7号电梯', groupId: 'E7', floors: ['3楼', '4楼'] }
  ];

  assert.equal(Array.isArray(shafts), true);
  assert.deepEqual(shafts.map(shaft => shaft.shaftId), expected.map(shaft => shaft.shaftId));

  for (let index = 0; index < expected.length; index += 1) {
    const shaft = shafts[index];
    const expectedShaft = expected[index];
    assert.equal(shaft.displayName, expectedShaft.displayName, shaft.shaftId);
    assert.equal(shaft.patientAccessible, true, shaft.shaftId);
    assert.deepEqual(shaft.serviceFloors, expectedShaft.floors, shaft.shaftId);
    assert.deepEqual(Object.keys(shaft.floorMappings), expectedShaft.floors, shaft.shaftId);

    for (const floor of expectedShaft.floors) {
      assert.match(floor, /^(?:[1-9]|1[0-3])楼$/);
      const mapping = shaft.floorMappings[floor];
      assert.deepEqual(
        Object.keys(mapping).sort(),
        ['confirmed', 'elevatorGroupId'],
        shaft.shaftId + ' ' + floor
      );
      assert.equal(mapping.elevatorGroupId, expectedShaft.groupId, shaft.shaftId + ' ' + floor);
      assert.equal(mapping.confirmed, true, shaft.shaftId + ' ' + floor);
      assert.equal(
        (elevatorGroups[floor] || []).some(group => group.id === mapping.elevatorGroupId),
        true,
        shaft.shaftId + ' ' + floor + ' group exists'
      );
    }
  }
});
