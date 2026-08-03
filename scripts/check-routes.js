'use strict';

const fs = require('node:fs');
const path = require('node:path');

const EXPECTED_DESTINATIONS = 42;
const EXPECTED_SAME_FLOOR_PATHS = 260;
const EXPECTED_FLOOR_NAV_PATHS = 470;
const EXPECTED_SHAFT_MAPPINGS = 53;
const LENGTH_UNIT = 'imageWidthPercent';
const COLOCATED_ANCHOR_TOLERANCE = 0.6;

function isProjectRoot(candidate) {
  return [
    'config/public-destinations.json',
    'config/department-anchors.json',
    'config/elevator-shafts.json',
    'config/routing-policy.json',
    'miniprogram/data/routes.js',
    'miniprogram/data/floorNavPaths.js',
    'miniprogram/data/sameFloorPaths.js',
    'miniprogram/data/elevatorShafts.js',
  ].every(relative => fs.existsSync(path.join(candidate, relative)));
}

function ancestors(start) {
  const values = [];
  let current = path.resolve(start);
  while (true) {
    values.push(current);
    const parent = path.dirname(current);
    if (parent === current) return values;
    current = parent;
  }
}

function locateProjectRoot() {
  const candidates = [
    ...ancestors(path.resolve(__dirname, '..')),
    ...ancestors(process.cwd()),
  ];
  return candidates.find(isProjectRoot) || null;
}

function pointIsValid(point) {
  return Array.isArray(point)
    && point.length === 2
    && point.every(value => Number.isFinite(value) && value >= 0 && value <= 100);
}

function imageSizeIsValid(imageSize) {
  return Array.isArray(imageSize)
    && imageSize.length === 2
    && imageSize.every(value => Number.isInteger(value) && value > 1);
}

function roundHalfEven(value) {
  const lower = Math.floor(value);
  if (value - lower === 0.5) return lower % 2 === 0 ? lower : lower + 1;
  return Math.round(value);
}

function pointToMapPixel(point, imageSize, label = 'point') {
  if (!pointIsValid(point)) throw new Error(`invalid ${label}`);
  if (!imageSizeIsValid(imageSize)) throw new Error(`invalid imageSize: ${JSON.stringify(imageSize)}`);
  return [
    roundHalfEven(point[0] / 100 * (imageSize[0] - 1)),
    roundHalfEven(point[1] / 100 * (imageSize[1] - 1)),
  ];
}

function departmentSemanticEndpoint(department) {
  if (!department || typeof department !== 'object' || !pointIsValid(department.anchor)) {
    throw new Error('invalid department anchor');
  }
  if (department.doorApproachPoint == null) return department.anchor;
  if (!pointIsValid(department.doorApproachPoint)) {
    throw new Error('invalid department doorApproachPoint');
  }
  return department.doorApproachPoint;
}

function validateDepartmentEndpoint(actual, department, imageSize, maxAnchorSnapPx) {
  if (!Number.isInteger(maxAnchorSnapPx) || maxAnchorSnapPx < 1) {
    return { ok: false, message: 'invalid routing policy maxAnchorSnapPx' };
  }
  let expected;
  let endpointType;
  try {
    expected = departmentSemanticEndpoint(department);
    endpointType = department.doorApproachPoint == null ? 'anchor' : 'doorApproachPoint';
    const actualPixel = pointToMapPixel(actual, imageSize, 'route endpoint');
    const expectedPixel = pointToMapPixel(expected, imageSize, endpointType);
    const distancePx = Math.hypot(
      actualPixel[0] - expectedPixel[0],
      actualPixel[1] - expectedPixel[1]
    );
    const tolerancePx = endpointType === 'doorApproachPoint' ? 1 : maxAnchorSnapPx;
    if (distancePx > tolerancePx) {
      return {
        ok: false,
        distancePx,
        endpointType,
        message: `${endpointType} residual ${distancePx.toFixed(3)} px exceeds ${tolerancePx} px`,
      };
    }
    return { ok: true, distancePx, endpointType };
  } catch (error) {
    return { ok: false, message: error.message };
  }
}

function validateElevatorEndpoint(actual, elevatorAnchor, imageSize) {
  try {
    const actualPixel = pointToMapPixel(actual, imageSize, 'route elevator endpoint');
    const expectedPixel = pointToMapPixel(elevatorAnchor, imageSize, 'runtime elevatorAnchor');
    const ok = actualPixel[0] === expectedPixel[0] && actualPixel[1] === expectedPixel[1];
    return ok
      ? { ok: true, distancePx: 0 }
      : {
        ok: false,
        distancePx: Math.hypot(
          actualPixel[0] - expectedPixel[0],
          actualPixel[1] - expectedPixel[1]
        ),
        message: 'elevator endpoint is on a different rounded pixel from runtime elevatorAnchor',
      };
  } catch (error) {
    return { ok: false, message: error.message };
  }
}

function maxAnchorSnapPxForFloor(routingPolicy, floor) {
  if (!routingPolicy || typeof routingPolicy !== 'object' || routingPolicy.schemaVersion !== 1) {
    throw new Error('invalid routing policy schema');
  }
  const defaults = routingPolicy.defaults;
  const floorPolicy = routingPolicy.floors && routingPolicy.floors[floor];
  if (!defaults || typeof defaults !== 'object' || !floorPolicy || typeof floorPolicy !== 'object') {
    throw new Error(`missing routing policy for ${String(floor)}`);
  }
  const value = Object.hasOwn(floorPolicy, 'maxAnchorSnapPx')
    ? floorPolicy.maxAnchorSnapPx
    : defaults.maxAnchorSnapPx;
  if (!Number.isInteger(value) || value < 1) {
    throw new Error(`invalid routing policy maxAnchorSnapPx for ${String(floor)}`);
  }
  return value;
}

function pointsEqual(left, right, tolerance = 0.002) {
  return pointIsValid(left) && pointIsValid(right)
    && Math.abs(left[0] - right[0]) <= tolerance
    && Math.abs(left[1] - right[1]) <= tolerance;
}

function calculateLength(points, imageSize) {
  if (!Array.isArray(points) || !Array.isArray(imageSize) || imageSize.length !== 2) return NaN;
  const width = Number(imageSize[0]);
  const height = Number(imageSize[1]);
  if (!(width > 0) || !(height > 0)) return NaN;
  const aspect = height / width;
  let total = 0;
  for (let index = 1; index < points.length; index += 1) {
    if (!pointIsValid(points[index - 1]) || !pointIsValid(points[index])) return NaN;
    const dx = points[index][0] - points[index - 1][0];
    const dy = (points[index][1] - points[index - 1][1]) * aspect;
    total += Math.hypot(dx, dy);
  }
  return total;
}

function calculatePointDistance(left, right, imageSize) {
  return calculateLength([left, right], imageSize);
}

function validateImage(projectRoot, item, label, errors) {
  if (!imageSizeIsValid(item.imageSize)) {
    errors.push(`${label}: invalid imageSize`);
  }
  if (!/^\/assets\/floor-maps\/(?:[1-9]|1[0-3])F\.jpg$/.test(item.image || '')) {
    errors.push(`${label}: invalid production floor-map URL ${String(item.image)}`);
    return;
  }
  const asset = path.join(projectRoot, 'miniprogram', ...item.image.slice(1).split('/'));
  if (!fs.existsSync(asset)) errors.push(`${label}: missing asset ${item.image}`);
}

function validatePathRecord(projectRoot, item, label, options, errors) {
  if (!item || typeof item !== 'object') {
    errors.push(`${label}: missing record`);
    return;
  }
  validateImage(projectRoot, item, label, errors);
  if (item.routeLengthUnit !== LENGTH_UNIT) errors.push(`${label}: invalid routeLengthUnit`);
  const coLocated = options && options.coLocated;
  const minimum = coLocated ? 1 : 2;
  if (!Array.isArray(item.points) || item.points.length < minimum || !item.points.every(pointIsValid)) {
    errors.push(`${label}: invalid points`);
    return;
  }
  const actual = calculateLength(item.points, item.imageSize);
  if (!Number.isFinite(actual) || !Number.isFinite(item.routeLength)
      || Math.abs(actual - item.routeLength) > 0.00001) {
    errors.push(`${label}: routeLength does not match aspect-correct geometry`);
  }
  if (coLocated) {
    if (item.coLocated !== true || item.routeLength !== 0) {
      errors.push(`${label}: invalid coLocated record`);
    }
  } else if (!(item.routeLength > 0)) {
    errors.push(`${label}: non-positive routeLength`);
  }
}

function loadModule(projectRoot, relative) {
  const absolute = path.join(projectRoot, relative);
  delete require.cache[require.resolve(absolute)];
  return require(absolute);
}

function checkRoutes(projectRoot) {
  const errors = [];
  const publicConfig = JSON.parse(fs.readFileSync(
    path.join(projectRoot, 'config/public-destinations.json'), 'utf8'
  ));
  const anchors = JSON.parse(fs.readFileSync(
    path.join(projectRoot, 'config/department-anchors.json'), 'utf8'
  ));
  const shaftConfig = JSON.parse(fs.readFileSync(
    path.join(projectRoot, 'config/elevator-shafts.json'), 'utf8'
  ));
  const routingPolicy = JSON.parse(fs.readFileSync(
    path.join(projectRoot, 'config/routing-policy.json'), 'utf8'
  ));
  const routes = loadModule(projectRoot, 'miniprogram/data/routes.js');
  const floorNavPaths = loadModule(projectRoot, 'miniprogram/data/floorNavPaths.js');
  const sameFloorPaths = loadModule(projectRoot, 'miniprogram/data/sameFloorPaths.js');
  const runtimeShafts = loadModule(projectRoot, 'miniprogram/data/elevatorShafts.js');

  const destinations = publicConfig.publicDestinations || [];
  if (destinations.length !== EXPECTED_DESTINATIONS) {
    errors.push(`expected ${EXPECTED_DESTINATIONS} public destinations, got ${destinations.length}`);
  }
  const names = destinations.map(item => item.name);
  if (new Set(names).size !== names.length) errors.push('public destination names are not unique');
  if (anchors.length !== EXPECTED_DESTINATIONS) errors.push('department anchor count is not 42');
  if (routes.getDepartmentNames().length !== EXPECTED_DESTINATIONS) {
    errors.push('runtime destination count is not 42');
  }
  const anchorByName = new Map(anchors.map(item => [item.name, item]));
  for (const destination of destinations) {
    const anchor = anchorByName.get(destination.name);
    if (!anchor || anchor.floor !== destination.floor) {
      errors.push(`${destination.name}: missing or invalid authoritative anchor`);
      continue;
    }
    try {
      departmentSemanticEndpoint(anchor);
      maxAnchorSnapPxForFloor(routingPolicy, anchor.floor);
    } catch (error) {
      errors.push(`${destination.name}: ${error.message}`);
    }
  }

  const expectedSameFloor = new Set();
  for (const from of destinations) {
    for (const to of destinations) {
      if (from.name !== to.name && from.floor === to.floor) {
        expectedSameFloor.add(`${from.name}|||${to.name}`);
      }
    }
  }
  if (expectedSameFloor.size !== EXPECTED_SAME_FLOOR_PATHS
      || Object.keys(sameFloorPaths).length !== EXPECTED_SAME_FLOOR_PATHS) {
    errors.push(`expected ${EXPECTED_SAME_FLOOR_PATHS} same-floor records`);
  }
  for (const key of expectedSameFloor) {
    const item = sameFloorPaths[key];
    const [fromName, toName] = key.split('|||');
    const fromAnchor = anchorByName.get(fromName);
    const toAnchor = anchorByName.get(toName);
    if (!item || !fromAnchor || !toAnchor) {
      errors.push(`${key}: missing same-floor record`);
      continue;
    }
    const coLocated = item.coLocated === true;
    validatePathRecord(projectRoot, item, key, { coLocated }, errors);
    if (item.floor !== fromAnchor.floor || item.floor !== toAnchor.floor) {
      errors.push(`${key}: floor mismatch`);
    }
    let maxAnchorSnapPx;
    try {
      maxAnchorSnapPx = maxAnchorSnapPxForFloor(routingPolicy, fromAnchor.floor);
    } catch (error) {
      errors.push(`${key}: ${error.message}`);
    }
    const startResult = validateDepartmentEndpoint(
      item.points && item.points[0], fromAnchor, item.imageSize, maxAnchorSnapPx
    );
    if (!startResult.ok) errors.push(`${key}: start department endpoint mismatch: ${startResult.message}`);
    if (coLocated) {
      let destinationDistance = NaN;
      let anchorDistance = NaN;
      try {
        const fromEndpoint = departmentSemanticEndpoint(fromAnchor);
        const toEndpoint = departmentSemanticEndpoint(toAnchor);
        destinationDistance = calculatePointDistance(
          item.points[item.points.length - 1], toEndpoint, item.imageSize
        );
        anchorDistance = calculatePointDistance(fromEndpoint, toEndpoint, item.imageSize);
      } catch (error) {
        errors.push(`${key}: invalid coLocated semantic endpoint: ${error.message}`);
      }
      if (item.points.length !== 1
          || !Number.isFinite(destinationDistance)
          || destinationDistance > COLOCATED_ANCHOR_TOLERANCE
          || !Number.isFinite(anchorDistance)
          || anchorDistance > COLOCATED_ANCHOR_TOLERANCE) {
        errors.push(`${key}: coLocated destination anchor is outside the 0.6 image-width-percent tolerance`);
      }
    } else {
      const endResult = validateDepartmentEndpoint(
        item.points && item.points[item.points.length - 1],
        toAnchor,
        item.imageSize,
        maxAnchorSnapPx
      );
      if (!endResult.ok) errors.push(`${key}: end department endpoint mismatch: ${endResult.message}`);
    }
  }

  const runtimeById = new Map(runtimeShafts.map(shaft => [shaft.shaftId, shaft]));
  const configMappings = shaftConfig.reduce(
    (total, shaft) => total + Object.keys(shaft.floorMappings || {}).length, 0
  );
  const runtimeMappings = runtimeShafts.reduce(
    (total, shaft) => total + Object.keys(shaft.floorMappings || {}).length, 0
  );
  if (configMappings !== EXPECTED_SHAFT_MAPPINGS || runtimeMappings !== EXPECTED_SHAFT_MAPPINGS) {
    errors.push(`expected ${EXPECTED_SHAFT_MAPPINGS} verified shaft mappings`);
  }
  const expectedFloorKeys = new Set();
  for (const anchor of anchors) {
    for (const configuredShaft of shaftConfig) {
      const mapping = configuredShaft.floorMappings && configuredShaft.floorMappings[anchor.floor];
      if (!mapping || mapping.confirmed !== true || configuredShaft.patientAccessible !== true) continue;
      for (const direction of ['toElevator', 'fromElevator']) {
        expectedFloorKeys.add(`${anchor.name}|||${configuredShaft.shaftId}|||${direction}`);
      }
    }
  }
  if (expectedFloorKeys.size !== EXPECTED_FLOOR_NAV_PATHS
      || Object.keys(floorNavPaths).length !== EXPECTED_FLOOR_NAV_PATHS) {
    errors.push(`expected ${EXPECTED_FLOOR_NAV_PATHS} per-shaft route records`);
  }
  for (const key of expectedFloorKeys) {
    const [departmentName, shaftId, direction] = key.split('|||');
    const item = floorNavPaths[key];
    const anchor = anchorByName.get(departmentName);
    if (!anchor) {
      errors.push(`${key}: missing department anchor`);
      continue;
    }
    const shaft = runtimeById.get(shaftId);
    const mapping = shaft && shaft.floorMappings && shaft.floorMappings[anchor.floor];
    if (!item || !mapping) {
      errors.push(`${key}: missing path or runtime mapping`);
      continue;
    }
    validatePathRecord(projectRoot, item, key, { coLocated: false }, errors);
    if (item.departmentName !== departmentName || item.shaftId !== shaftId
        || item.direction !== direction || item.floor !== anchor.floor
        || item.elevatorGroupId !== mapping.elevatorGroupId) {
      errors.push(`${key}: metadata mismatch`);
    }
    let maxAnchorSnapPx;
    try {
      maxAnchorSnapPx = maxAnchorSnapPxForFloor(routingPolicy, anchor.floor);
    } catch (error) {
      errors.push(`${key}: ${error.message}`);
    }
    const departmentPoint = direction === 'toElevator'
      ? item.points && item.points[0]
      : item.points && item.points[item.points.length - 1];
    const elevatorPoint = direction === 'toElevator'
      ? item.points && item.points[item.points.length - 1]
      : item.points && item.points[0];
    const departmentResult = validateDepartmentEndpoint(
      departmentPoint, anchor, item.imageSize, maxAnchorSnapPx
    );
    const elevatorResult = validateElevatorEndpoint(
      elevatorPoint, mapping.elevatorAnchor, item.imageSize
    );
    const recordAnchorResult = validateElevatorEndpoint(
      item.elevatorAnchor, mapping.elevatorAnchor, item.imageSize
    );
    if (!departmentResult.ok) {
      errors.push(`${key}: department endpoint mismatch: ${departmentResult.message}`);
    }
    if (!elevatorResult.ok) {
      errors.push(`${key}: elevator endpoint mismatch: ${elevatorResult.message}`);
    }
    if (!recordAnchorResult.ok) {
      errors.push(`${key}: item.elevatorAnchor mismatch: ${recordAnchorResult.message}`);
    }
  }

  for (const from of destinations) {
    for (const to of destinations) {
      if (from.name === to.name || from.floor === to.floor) continue;
      let plan;
      try {
        plan = routes.createNavigationPlan(from.name, to.name);
      } catch (error) {
        errors.push(`${from.name} -> ${to.name}: planner threw ${error.message}`);
        continue;
      }
      if (!plan || !plan.ok || plan.status !== 'route' || plan.mode !== 'crossFloor'
          || !plan.selectedElevatorShaftId || !Array.isArray(plan.legs) || plan.legs.length !== 2) {
        errors.push(`${from.name} -> ${to.name}: invalid cross-floor plan`);
        continue;
      }
      if (plan.legs.some(leg => leg.selectedElevatorShaftId !== plan.selectedElevatorShaftId)) {
        errors.push(`${from.name} -> ${to.name}: plan switches elevator shafts`);
      }
    }
  }

  return {
    errors,
    summary: {
      destinations: destinations.length,
      sameFloorPaths: Object.keys(sameFloorPaths).length,
      floorNavPaths: Object.keys(floorNavPaths).length,
      shaftMappings: runtimeMappings,
    },
  };
}

function usage() {
  console.error('Usage: node scripts/check-routes.js');
}

function main() {
  if (process.argv.length !== 2) {
    usage();
    return 2;
  }
  const projectRoot = locateProjectRoot();
  if (!projectRoot) {
    usage();
    return 2;
  }
  try {
    const result = checkRoutes(projectRoot);
    console.log('Route data verification');
    console.log(`- public destinations: ${result.summary.destinations}`);
    console.log(`- same-floor paths: ${result.summary.sameFloorPaths}`);
    console.log(`- per-shaft paths: ${result.summary.floorNavPaths}`);
    console.log(`- verified shaft mappings: ${result.summary.shaftMappings}`);
    if (result.errors.length) {
      console.error(`Route verification failed with ${result.errors.length} error(s):`);
      result.errors.forEach(error => console.error(`- ${error}`));
      return 1;
    }
    console.log('PASS: route data and same-shaft plans are valid.');
    return 0;
  } catch (error) {
    console.error(`Route verification failed: ${error.stack || error.message}`);
    return 1;
  }
}

if (require.main === module) process.exitCode = main();

module.exports = {
  calculateLength,
  calculatePointDistance,
  checkRoutes,
  departmentSemanticEndpoint,
  locateProjectRoot,
  main,
  maxAnchorSnapPxForFloor,
  pointToMapPixel,
  validateDepartmentEndpoint,
  validateElevatorEndpoint,
};
