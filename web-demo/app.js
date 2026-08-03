(function () {
  'use strict';

  const routes = window.__navigationRequire('/data/routes.js');
  const elements = {
    form: document.getElementById('route-form'),
    from: document.getElementById('from-select'),
    to: document.getElementById('to-select'),
    status: document.querySelector('[data-testid="plan-status"]'),
    result: document.getElementById('route-result'),
    legCount: document.getElementById('leg-count'),
    shaft: document.getElementById('selected-shaft'),
    legTitle: document.getElementById('leg-title'),
    instruction: document.getElementById('leg-instruction'),
    mapHost: document.getElementById('map-host'),
    previous: document.getElementById('previous-leg'),
    next: document.getElementById('next-leg')
  };
  const state = {
    plan: null,
    legIndex: 0
  };

  function appendDestinationOptions(select) {
    routes.getDepartmentNames().forEach(name => {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      select.appendChild(option);
    });
  }

  function toWebAssetPath(miniprogramPath) {
    const value = String(miniprogramPath || '');
    if (!value.startsWith('/assets/')) throw new Error('无效的本地资源路径');
    return '/miniprogram' + value;
  }

  function setStatus(status, message) {
    elements.status.dataset.status = status || '';
    elements.status.textContent = message || '';
  }

  function clearMap() {
    elements.mapHost.replaceChildren();
    elements.legTitle.textContent = '';
    elements.instruction.textContent = '';
  }

  function createRouteSvg(points) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'route-line');
    svg.setAttribute('viewBox', '0 0 100 100');
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('aria-hidden', 'true');
    const polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    polyline.setAttribute('points', (points || []).map(point => point[0] + ',' + point[1]).join(' '));
    polyline.setAttribute('fill', 'none');
    polyline.setAttribute('stroke', '#d91f26');
    polyline.setAttribute('stroke-width', '0.8');
    polyline.setAttribute('stroke-linecap', 'round');
    polyline.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(polyline);
    return svg;
  }

  function renderActiveLeg() {
    clearMap();
    const plan = state.plan;
    if (!plan || plan.status !== 'route' || !plan.legs.length) return;

    const leg = plan.legs[state.legIndex];
    const stage = document.createElement('div');
    stage.className = 'map-stage';
    const image = document.createElement('img');
    image.dataset.testid = 'active-floor-map';
    image.dataset.activeFloorMap = 'true';
    image.alt = leg.floor + '路线图：' + leg.title;
    image.src = toWebAssetPath(leg.image);
    stage.appendChild(image);
    stage.appendChild(createRouteSvg(leg.points));
    elements.mapHost.appendChild(stage);

    elements.legTitle.textContent = '第 ' + (state.legIndex + 1) + ' / ' + plan.legs.length + ' 段 · ' + leg.floor;
    elements.instruction.textContent = leg.instruction || '请沿图中红色路线行进。';
    elements.previous.disabled = state.legIndex === 0;
    elements.next.disabled = state.legIndex >= plan.legs.length - 1;
  }

  function renderPlan(plan) {
    state.plan = plan;
    state.legIndex = 0;
    clearMap();

    if (plan.ok && plan.status === 'coLocated') {
      elements.result.classList.add('hidden');
      setStatus('coLocated', plan.message);
      return;
    }
    if (!plan.ok || plan.status !== 'route') {
      elements.result.classList.add('hidden');
      setStatus(plan.status || 'error', plan.message || '暂时无法生成路线，请咨询现场工作人员。');
      return;
    }

    const isCrossFloor = plan.mode === 'crossFloor';
    setStatus('route', isCrossFloor
      ? '已找到跨层路线。全程使用同一部电梯，请按分段地图行进。'
      : '已找到同层路线，请按地图中的红线行进。');
    elements.result.classList.remove('hidden');
    elements.legCount.textContent = String(plan.legs.length);
    elements.shaft.textContent = plan.selectedElevatorDisplayName
      || plan.selectedElevatorShaftId
      || '无需乘梯';
    renderActiveLeg();
  }

  elements.form.addEventListener('submit', event => {
    event.preventDefault();
    renderPlan(routes.createNavigationPlan(elements.from.value, elements.to.value));
  });
  elements.previous.addEventListener('click', () => {
    if (!state.plan || state.legIndex <= 0) return;
    state.legIndex -= 1;
    renderActiveLeg();
  });
  elements.next.addEventListener('click', () => {
    if (!state.plan || state.legIndex >= state.plan.legs.length - 1) return;
    state.legIndex += 1;
    renderActiveLeg();
  });
  appendDestinationOptions(elements.from);
  appendDestinationOptions(elements.to);
})();
