const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const { createNavigationPageHarness } = require('./helpers/navigation-page-harness.js');

const projectRoot = path.resolve(__dirname, '..');
const pageWxml = fs.readFileSync(
  path.join(projectRoot, 'miniprogram/pages/navigation/navigation.wxml'),
  'utf8'
);

test('route preview keeps all legs off reactive data and switches one current leg', () => {
  const harness = createNavigationPageHarness();
  try {
    const { page } = harness;
    page.data.inputVal1 = '儿科门诊';
    page.data.inputVal2 = '中医馆';
    page.updateRoutePreview();

    assert.equal(Object.hasOwn(page.data, 'previewLegs'), false);
    assert.equal(page.previewPlanLegs.length, 2);
    assert.equal(page.data.currentPreviewLeg.title, page.previewPlanLegs[0].title);
    assert.equal(page.data.previewLegIndex, 0);
    assert.equal(page.data.previewLegCount, 2);

    page.showNextPreviewLeg();
    assert.equal(page.data.currentPreviewLeg.title, page.previewPlanLegs[1].title);
    assert.equal(page.data.previewLegIndex, 1);
    page.showPreviousPreviewLeg();
    assert.equal(page.data.currentPreviewLeg.title, page.previewPlanLegs[0].title);
  } finally {
    harness.restore();
  }
});

test('cross-floor preview names the nearest elevator selected for the patient', () => {
  const harness = createNavigationPageHarness();
  try {
    const { page } = harness;
    page.data.inputVal1 = '儿科门诊';
    page.data.inputVal2 = '妇科门诊';
    page.updateRoutePreview();

    assert.deepEqual(
      page.previewPlanLegs.map(leg => leg.title),
      [
        '儿科门诊 → 6号电梯',
        '6号电梯 → 妇科门诊'
      ]
    );

    page.startStepLeg = () => {};
    page.startNavigation();
    assert.equal(page.currentPlan.selectedElevatorShaftId, 'S6');
    assert.deepEqual(
      page.currentPlan.legs.map(leg => leg.instruction),
      [
        '请沿红色路线前往6号电梯',
        '请从6号电梯出发，沿红色路线前往目的地'
      ]
    );
    assert.equal(
      page.currentPlan.legs[0].transferInstruction,
      '已到达6号电梯，请乘坐6号电梯前往2楼'
    );
  } finally {
    harness.restore();
  }
});

test('navigation keeps only the active leg in reactive data', () => {
  const harness = createNavigationPageHarness();
  try {
    const { page } = harness;
    page.data.inputVal1 = '儿科门诊';
    page.data.inputVal2 = '中医馆';
    page.startStepLeg = () => {};
    page.startNavigation();

    assert.equal(Object.hasOwn(page.data, 'navigationImages'), false);
    assert.equal(page.data.currentLeg, page.currentPlan.legs[0]);
    assert.equal(page.data.currentImageIndex, 0);
    assert.equal(page.data.navigationLegCount, 2);
  } finally {
    harness.restore();
  }
});

test('preview markup mounts exactly one zoomable map and unmounts it behind navigation', () => {
  assert.doesNotMatch(pageWxml, /wx:for="\{\{previewLegs\}\}"/);
  assert.match(pageWxml, /wx:if="\{\{currentPreviewLeg\s*&&\s*!showNavigationPopup\}\}"/);
  assert.match(pageWxml, /<movable-area[\s\S]*?<movable-view[\s\S]*?scale="\{\{true\}\}"[\s\S]*?<image/);
  assert.equal((pageWxml.match(/src="\{\{currentPreviewLeg\.image\}\}"/g) || []).length, 1);
});

test('preview image API receives only the current image URL', () => {
  const harness = createNavigationPageHarness();
  try {
    harness.page.previewPlanLegs = [
      { image: '/assets/floor-maps/1F.jpg' },
      { image: '/assets/floor-maps/4F.jpg' }
    ];
    harness.page.setPreviewLeg(1);
    harness.page.previewImage();
    assert.deepEqual(harness.previewImageCalls, [{
      current: '/assets/floor-maps/4F.jpg',
      urls: ['/assets/floor-maps/4F.jpg']
    }]);
  } finally {
    harness.restore();
  }
});

test('clearRoutePreview releases the non-reactive plan and current preview', () => {
  const harness = createNavigationPageHarness();
  try {
    harness.page.previewPlanLegs = [{ image: '/assets/floor-maps/1F.jpg' }];
    harness.page.setPreviewLeg(0);
    harness.page.clearRoutePreview();
    assert.deepEqual(harness.page.previewPlanLegs, []);
    assert.equal(harness.page.data.currentPreviewLeg, null);
    assert.equal(harness.page.data.previewLegCount, 0);
  } finally {
    harness.restore();
  }
});
