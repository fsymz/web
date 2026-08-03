'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
  pointToMapPixel,
  validateDepartmentEndpoint,
  validateElevatorEndpoint,
} = require('../scripts/check-routes.js');

test('pixel conversion matches the generator half-even rounding rule', () => {
  assert.deepEqual(pointToMapPixel([50, 50], [102, 102]), [50, 50]);
});

test('canonical serialization on the same rounded pixel is accepted', () => {
  const result = validateDepartmentEndpoint(
    [87.039, 61.77],
    { anchor: [87.041, 61.776] },
    [5587, 7163],
    120
  );

  assert.equal(Math.abs(61.776 - 61.77) > 0.002, true);
  assert.equal(result.ok, true);
  assert.equal(result.distancePx, 0);
});

test('raw anchor snap at the policy boundary is accepted', () => {
  const result = validateDepartmentEndpoint(
    [67, 50],
    { anchor: [50, 50] },
    [101, 101],
    17
  );

  assert.equal(result.ok, true);
  assert.equal(result.distancePx, 17);
});

test('raw anchor snap over the policy boundary is rejected', () => {
  const result = validateDepartmentEndpoint(
    [68, 50],
    { anchor: [50, 50] },
    [101, 101],
    17
  );

  assert.equal(result.ok, false);
  assert.match(result.message, /18\.000 px exceeds 17 px/);
});

test('door approach residual of one pixel is accepted', () => {
  const result = validateDepartmentEndpoint(
    [51, 50],
    { anchor: [10, 10], doorApproachPoint: [50, 50] },
    [101, 101],
    120
  );

  assert.equal(result.ok, true);
  assert.equal(result.distancePx, 1);
  assert.equal(result.endpointType, 'doorApproachPoint');
});

test('door approach residual over one pixel is rejected', () => {
  const result = validateDepartmentEndpoint(
    [52, 50],
    { anchor: [10, 10], doorApproachPoint: [50, 50] },
    [101, 101],
    120
  );

  assert.equal(result.ok, false);
  assert.match(result.message, /doorApproachPoint residual 2\.000 px exceeds 1 px/);
});

test('elevator endpoint on a different rounded pixel is rejected', () => {
  const result = validateElevatorEndpoint(
    [51, 50],
    [50, 50],
    [101, 101]
  );

  assert.equal(result.ok, false);
  assert.match(result.message, /different rounded pixel/);
});

test('malformed endpoint inputs fail closed with useful messages', () => {
  const malformedCases = [
    [
      validateDepartmentEndpoint([50, 50], {}, [101, 101], 120),
      /anchor/,
    ],
    [
      validateDepartmentEndpoint(
        [50, 50],
        { anchor: [10, 10], doorApproachPoint: ['bad', 50] },
        [101, 101],
        120
      ),
      /doorApproachPoint/,
    ],
    [
      validateDepartmentEndpoint([50, 50], { anchor: [50, 50] }, [1, 101], 120),
      /imageSize/,
    ],
    [
      validateDepartmentEndpoint([50, 50], { anchor: [50, 50] }, [101, 101], 0),
      /maxAnchorSnapPx/,
    ],
    [
      validateElevatorEndpoint([50, 50], undefined, [101, 101]),
      /elevatorAnchor/,
    ],
  ];

  for (const [result, messagePattern] of malformedCases) {
    assert.equal(result.ok, false);
    assert.match(result.message, messagePattern);
  }
});
