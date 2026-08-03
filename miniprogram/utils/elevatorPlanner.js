function getElevatorPathKey(departmentName, shaftId, direction) {
  return departmentName + '|||' + shaftId + '|||' + direction;
}

function hasDistinctValidPoints(points) {
  if (!Array.isArray(points) || points.length < 2) return false;
  if (!points.every(point => (
    Array.isArray(point)
    && point.length >= 2
    && Number.isFinite(point[0])
    && Number.isFinite(point[1])
  ))) return false;

  const firstPoint = points[0];
  return points.some(point => point[0] !== firstPoint[0] || point[1] !== firstPoint[1]);
}

function isValidElevatorPath(path, departmentRoute, shaft, direction) {
  if (!path || !departmentRoute || !shaft) return false;
  if (direction !== 'toElevator' && direction !== 'fromElevator') return false;
  if (typeof departmentRoute.name !== 'string' || !departmentRoute.name.trim()) return false;

  const floor = departmentRoute.floor;
  const floorMapping = shaft.floorMappings && shaft.floorMappings[floor];
  if (
    shaft.patientAccessible !== true
    || !Array.isArray(shaft.serviceFloors)
    || !shaft.serviceFloors.includes(floor)
    || !floorMapping
    || floorMapping.confirmed !== true
  ) return false;

  return path.departmentName === departmentRoute.name
    && path.floor === floor
    && path.shaftId === shaft.shaftId
    && path.direction === direction
    && path.elevatorGroupId === floorMapping.elevatorGroupId
    && hasDistinctValidPoints(path.points)
    && Number.isFinite(path.routeLength)
    && path.routeLength > 0
    && path.routeLengthUnit === 'imageWidthPercent';
}

function selectNearestElevatorShaft(options) {
  const config = options || {};
  const fromRoute = config.fromRoute;
  const toRoute = config.toRoute;
  const shafts = Array.isArray(config.shafts) ? config.shafts : [];
  const floorNavPaths = config.floorNavPaths || {};
  const candidates = shafts
    .filter(shaft => shaft && fromRoute && toRoute)
    .map(shaft => ({
      shaft,
      toElevatorPath: floorNavPaths[
        getElevatorPathKey(fromRoute.name, shaft.shaftId, 'toElevator')
      ],
      fromElevatorPath: floorNavPaths[
        getElevatorPathKey(toRoute.name, shaft.shaftId, 'fromElevator')
      ]
    }))
    .filter(candidate => (
      isValidElevatorPath(
        candidate.toElevatorPath,
        fromRoute,
        candidate.shaft,
        'toElevator'
      )
      && isValidElevatorPath(
        candidate.fromElevatorPath,
        toRoute,
        candidate.shaft,
        'fromElevator'
      )
    ));

  candidates.sort((left, right) => {
    const routeLengthDifference = (
      left.toElevatorPath.routeLength - right.toElevatorPath.routeLength
    );
    if (routeLengthDifference) return routeLengthDifference;
    if (left.shaft.shaftId < right.shaft.shaftId) return -1;
    if (left.shaft.shaftId > right.shaft.shaftId) return 1;
    return 0;
  });
  const selected = candidates[0];

  if (!selected) {
    return {
      ok: false,
      status: 'noCommonElevator',
      reason: '未找到同时服务起点和终点楼层、且两端路径完整的患者可用电梯。'
    };
  }

  return {
    ok: true,
    selectedElevatorShaftId: selected.shaft.shaftId,
    shaft: selected.shaft,
    toElevatorPath: selected.toElevatorPath,
    fromElevatorPath: selected.fromElevatorPath
  };
}

module.exports = {
  getElevatorPathKey,
  isValidElevatorPath,
  selectNearestElevatorShaft
};
