#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const projectRoot = path.resolve(__dirname, '..');
const repairRoot = path.resolve(projectRoot, '..');
const legacyRoutesPath = path.join(
  repairRoot,
  '院内导航_模拟导航整合版',
  '替换到院内导航页面目录下',
  'data',
  'routes.js'
);

const excludedPatientDestinations = Object.freeze([
  '三楼行政办公区',
  '四楼行政办公区',
  '消毒供应中心',
  '手术室',
  '新生儿重症监护病房'
]);

function floorMapImage(floor) {
  const match = String(floor || '').match(/^(\d{1,2})楼$/);
  const floorNumber = match ? Number(match[1]) : 0;
  if (floorNumber < 1 || floorNumber > 13) {
    throw new Error('Unsupported destination floor: ' + floor);
  }
  return '/assets/floor-maps/' + floorNumber + 'F.jpg';
}

function migrateDepartmentRoute(route) {
  const image = floorMapImage(route.floor);
  return {
    name: route.name,
    floor: route.floor,
    fromElevator: {
      image,
      floor: route.floor,
      instruction: route.fromElevator.instruction
    },
    toDestination: {
      image,
      floor: route.floor,
      instruction: route.toDestination.instruction
    }
  };
}

function buildPublicDestinationConfig() {
  if (!fs.existsSync(legacyRoutesPath)) {
    throw new Error('Legacy routes source not found: ' + legacyRoutesPath);
  }

  const legacy = require(legacyRoutesPath);
  const routes = legacy && legacy.departmentRoutes;
  if (!Array.isArray(routes) || routes.length !== 47) {
    throw new Error('Expected exactly 47 legacy department routes');
  }

  const names = routes.map(route => route.name);
  for (const excludedName of excludedPatientDestinations) {
    if (!names.includes(excludedName)) {
      throw new Error('Legacy routes are missing excluded destination: ' + excludedName);
    }
  }

  const excludedSet = new Set(excludedPatientDestinations);
  const publicDestinations = routes
    .filter(route => !excludedSet.has(route.name))
    .map(migrateDepartmentRoute);

  if (publicDestinations.length !== 42) {
    throw new Error('Expected exactly 42 public destinations');
  }
  for (const retainedName of ['消控室', '计算机机房']) {
    if (!publicDestinations.some(route => route.name === retainedName)) {
      throw new Error('Required public destination was removed: ' + retainedName);
    }
  }

  return {
    excludedPatientDestinations: Array.from(excludedPatientDestinations),
    publicDestinations
  };
}

function parseOutputPath(argv) {
  const writeIndex = argv.indexOf('--write');
  if (writeIndex === -1 || !argv[writeIndex + 1] || argv.length !== 2) {
    throw new Error('Usage: node scripts/migrate-maintenance-data.js --write <output.json>');
  }
  return path.resolve(projectRoot, argv[writeIndex + 1]);
}

function main(argv) {
  const outputPath = parseOutputPath(argv);
  const content = JSON.stringify(buildPublicDestinationConfig(), null, 2) + '\n';
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, content, 'utf8');
  process.stdout.write('Wrote ' + path.relative(projectRoot, outputPath) + '\n');
}

if (require.main === module) {
  try {
    main(process.argv.slice(2));
  } catch (error) {
    process.stderr.write(error.message + '\n');
    process.exitCode = 1;
  }
}

module.exports = {
  buildPublicDestinationConfig,
  excludedPatientDestinations
};
