function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function toNumber(value, fallback) {
  const num = Number(value);
  return Number.isFinite(num) ? num : fallback;
}

function normalizePoint(point) {
  if (Array.isArray(point)) {
    return {
      x: clamp(toNumber(point[0], 0), 0, 100),
      y: clamp(toNumber(point[1], 0), 0, 100)
    };
  }

  return {
    x: clamp(toNumber(point && point.x, 0), 0, 100),
    y: clamp(toNumber(point && point.y, 0), 0, 100)
  };
}

function normalizePoints(points) {
  if (!Array.isArray(points)) return [];
  return points.map(normalizePoint);
}

function buildSemanticStepPath(points, semanticPointIndexes) {
  const normalizedPoints = normalizePoints(points);
  const fallbackIndexes = normalizedPoints.map((point, index) => index);

  if (!Array.isArray(semanticPointIndexes) || !semanticPointIndexes.length) {
    return { points: normalizedPoints, rawPointIndexes: fallbackIndexes };
  }

  const lastIndex = normalizedPoints.length - 1;
  const hasInvalidIndex = semanticPointIndexes.some(index => (
    !Number.isInteger(index) || index < 0 || index > lastIndex
  ));
  if (hasInvalidIndex) {
    return { points: normalizedPoints, rawPointIndexes: fallbackIndexes };
  }

  const rawPointIndexes = Array.from(new Set(semanticPointIndexes.concat([0, lastIndex])))
    .sort((left, right) => left - right);
  if (rawPointIndexes.length < 2) {
    return { points: normalizedPoints, rawPointIndexes: fallbackIndexes };
  }

  return {
    points: rawPointIndexes.map(index => normalizedPoints[index]),
    rawPointIndexes
  };
}

function getImageAspect(imageSize) {
  if (!Array.isArray(imageSize)) return 1;

  const width = Number(imageSize[0]);
  const height = Number(imageSize[1]);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    return 1;
  }

  return height / width;
}

function getSegmentAngle(from, to, imageSize) {
  const aspect = getImageAspect(imageSize);
  const dx = to.x - from.x;
  const dy = (to.y - from.y) * aspect;
  return Math.atan2(dy, dx) * 180 / Math.PI + 90;
}

function getSegmentLength(from, to, imageSize) {
  const aspect = getImageAspect(imageSize);
  const dx = to.x - from.x;
  const dy = (to.y - from.y) * aspect;
  return Math.sqrt(dx * dx + dy * dy);
}

function getTurnDirection(previousFrom, currentFrom, currentTo, imageSize) {
  if (!previousFrom || !currentFrom || !currentTo) return '直行';

  const aspect = getImageAspect(imageSize);
  const ax = currentFrom.x - previousFrom.x;
  const ay = (currentFrom.y - previousFrom.y) * aspect;
  const bx = currentTo.x - currentFrom.x;
  const by = (currentTo.y - currentFrom.y) * aspect;
  const lenA = Math.sqrt(ax * ax + ay * ay);
  const lenB = Math.sqrt(bx * bx + by * by);

  if (lenA <= 0 || lenB <= 0) return '直行';

  const dot = ax * bx + ay * by;
  const cos = clamp(dot / (lenA * lenB), -1, 1);
  const degrees = Math.acos(cos) * 180 / Math.PI;

  if (degrees < 25) return '继续直行';
  if (degrees > 145) return '掉头后直行';

  const cross = ax * by - ay * bx;
  return cross > 0 ? '右转后直行' : '左转后直行';
}

function buildPathMetrics(points, imageSize) {
  const normalizedPoints = normalizePoints(points);
  if (normalizedPoints.length < 2) return null;

  const segments = [];
  let total = 0;

  for (let i = 0; i < normalizedPoints.length - 1; i += 1) {
    const from = normalizedPoints[i];
    const to = normalizedPoints[i + 1];
    const length = getSegmentLength(from, to, imageSize);

    if (length <= 0) continue;

    segments.push({
      from,
      to,
      length,
      angle: getSegmentAngle(from, to, imageSize),
      startDistance: total,
      endDistance: total + length
    });
    total += length;
  }

  if (!segments.length || total <= 0) return null;

  return {
    points: normalizedPoints,
    segments,
    total
  };
}

function getPointAtDistance(metrics, distance) {
  if (!metrics || !metrics.segments || !metrics.segments.length) {
    return { x: 0, y: 0, angle: 0 };
  }

  const targetDistance = clamp(distance, 0, metrics.total);

  for (let i = 0; i < metrics.segments.length; i += 1) {
    const segment = metrics.segments[i];
    if (targetDistance <= segment.endDistance) {
      const ratio = segment.length
        ? (targetDistance - segment.startDistance) / segment.length
        : 0;

      return {
        x: segment.from.x + (segment.to.x - segment.from.x) * ratio,
        y: segment.from.y + (segment.to.y - segment.from.y) * ratio,
        angle: segment.angle
      };
    }
  }

  const last = metrics.segments[metrics.segments.length - 1];
  return {
    x: last.to.x,
    y: last.to.y,
    angle: last.angle
  };
}

function getPointAtRatio(metrics, ratio) {
  if (!metrics) return { x: 0, y: 0, angle: 0 };
  return getPointAtDistance(metrics, metrics.total * clamp(ratio, 0, 1));
}

function getDurationMs(totalDistance, speedPercentPerSecond, minMs, maxMs) {
  const speed = Math.max(0.1, toNumber(speedPercentPerSecond, 9));
  const rawDuration = totalDistance / speed * 1000;
  return clamp(rawDuration, minMs || 5000, maxMs || 22000);
}

function percentDistanceToMeters(percentDistance, metersPerPercent) {
  const scale = Math.max(0.1, toNumber(metersPerPercent, 1.2));
  return percentDistance * scale;
}

function formatDistance(meters) {
  const safeMeters = Math.max(0, Math.round(toNumber(meters, 0)));
  return safeMeters + 'm';
}

function formatTime(ms) {
  const safeMs = Math.max(0, toNumber(ms, 0));
  const seconds = Math.ceil(safeMs / 1000);
  if (seconds < 60) return seconds + '秒';
  return Math.floor(seconds / 60) + '分' + (seconds % 60) + '秒';
}

function getSafeStepIndex(value, lastIndex) {
  const numeric = Number(value);
  const integer = Number.isFinite(numeric) ? Math.trunc(numeric) : 0;
  return Math.max(0, Math.min(integer, lastIndex));
}

function roundSpokenDistance(meters, roundingMeters) {
  const increment = Math.max(0.1, toNumber(roundingMeters, 1));
  const safeMeters = Math.max(0, toNumber(meters, 0));
  return Number((Math.round(safeMeters / increment) * increment).toFixed(3));
}

function formatSpokenMovement(turnText, meters, roundingMeters) {
  const roundedMeters = roundSpokenDistance(meters, roundingMeters);
  if (roundedMeters <= 0) return turnText + '，前方即到';
  return turnText + '约' + roundedMeters + '米';
}

function buildTransferApproachInstruction(transferInstruction, transferFloor) {
  const exactInstruction = String(
    transferInstruction
      || (transferFloor ? '已到达电梯，请乘坐电梯前往' + transferFloor : '已到达电梯')
  ).trim();
  const withoutEnding = exactInstruction.replace(/[。！？!?]+$/, '');

  if (withoutEnding.startsWith('已到达')) {
    const remaining = withoutEnding.slice(3);
    const separatorIndex = remaining.indexOf('，');
    if (separatorIndex >= 0) {
      return '到达' + remaining.slice(0, separatorIndex) + '后，'
        + remaining.slice(separatorIndex + 1);
    }
    return '到达' + remaining + '后，请按提示换乘';
  }

  if (withoutEnding.startsWith('到达')) {
    const remaining = withoutEnding.slice(2);
    const separatorIndex = remaining.indexOf('，');
    if (separatorIndex >= 0) {
      return '到达' + remaining.slice(0, separatorIndex) + '后，'
        + remaining.slice(separatorIndex + 1);
    }
    return '到达' + remaining + '后，请按提示换乘';
  }

  return '到达电梯后，请按提示换乘';
}

function buildStepInstruction(points, targetPointIndex, options) {
  const config = options || {};
  const normalizedPoints = normalizePoints(points);
  const safeIndex = getSafeStepIndex(targetPointIndex, normalizedPoints.length - 1);
  const arrivalName = config.arrivalName || '目的地';
  const hasNextLeg = Boolean(config.hasNextLeg);
  const transferInstruction = config.transferInstruction
    || (config.transferFloor ? '到达电梯，请乘坐电梯前往' + config.transferFloor : '到达电梯');
  const metersPerPercent = config.distanceMetersPerPercent || 1.2;
  const imageSize = config.imageSize;

  if (normalizedPoints.length < 2) {
    return {
      text: '当前路线缺少有效轨迹点',
      point: normalizedPoints[0] || { x: 0, y: 0 },
      progress: 0,
      isArrival: false
    };
  }

  if (safeIndex <= 0) {
    return {
      text: '准备出发，请沿路线直行',
      point: normalizedPoints[0],
      progress: 0,
      isArrival: false
    };
  }

  if (safeIndex >= normalizedPoints.length - 1) {
    const previousFrom = normalizedPoints[safeIndex - 2];
    const currentFrom = normalizedPoints[safeIndex - 1];
    const currentTo = normalizedPoints[safeIndex];
    const turnText = getTurnDirection(previousFrom, currentFrom, currentTo, imageSize);
    const meters = percentDistanceToMeters(
      getSegmentLength(currentFrom, currentTo, imageSize),
      metersPerPercent
    );
    const arrivalText = hasNextLeg
      ? buildTransferApproachInstruction(transferInstruction, config.transferFloor)
      : '到达' + arrivalName;

    return {
      text: formatSpokenMovement(turnText, meters, config.distanceRoundingMeters)
        + '，' + arrivalText,
      point: normalizedPoints[normalizedPoints.length - 1],
      progress: 100,
      isArrival: true
    };
  }

  const previousFrom = normalizedPoints[safeIndex - 2];
  const currentFrom = normalizedPoints[safeIndex - 1];
  const currentTo = normalizedPoints[safeIndex];
  const turnText = getTurnDirection(previousFrom, currentFrom, currentTo, imageSize);
  const meters = percentDistanceToMeters(
    getSegmentLength(currentFrom, currentTo, imageSize),
    metersPerPercent
  );

  return {
    text: formatSpokenMovement(turnText, meters, config.distanceRoundingMeters),
    point: currentTo,
    progress: Math.round(safeIndex / (normalizedPoints.length - 1) * 100),
    isArrival: false
  };
}

function findDistinctPointBefore(points, startIndex) {
  const current = points[startIndex];
  for (let index = startIndex - 1; index >= 0; index -= 1) {
    const candidate = points[index];
    if (candidate.x !== current.x || candidate.y !== current.y) return candidate;
  }
  return null;
}

function findDistinctPointAfter(points, startIndex) {
  const current = points[startIndex];
  for (let index = startIndex + 1; index < points.length; index += 1) {
    const candidate = points[index];
    if (candidate.x !== current.x || candidate.y !== current.y) return candidate;
  }
  return null;
}

function getPolylineLength(points, fromIndex, toIndex, imageSize) {
  let total = 0;
  for (let index = fromIndex; index < toIndex; index += 1) {
    total += getSegmentLength(points[index], points[index + 1], imageSize);
  }
  return total;
}

function buildSpokenStepPath(points, semanticPointIndexes, options) {
  const config = options || {};
  const normalizedPoints = normalizePoints(points);
  const semanticPath = buildSemanticStepPath(points, semanticPointIndexes);
  const candidateIndexes = semanticPath.rawPointIndexes;

  if (candidateIndexes.length < 2) return semanticPath;

  const metersPerPercent = config.distanceMetersPerPercent || 1.2;
  const minimumMeters = Math.max(0, toNumber(config.minimumSpokenStepMeters, 0.5));
  const roundingMeters = Math.max(0.1, toNumber(config.distanceRoundingMeters, 1));
  const canSpeakSpan = (fromIndex, toIndex) => {
    const meters = percentDistanceToMeters(
      getPolylineLength(normalizedPoints, fromIndex, toIndex, config.imageSize),
      metersPerPercent
    );
    return meters >= minimumMeters && roundSpokenDistance(meters, roundingMeters) > 0;
  };

  const keptIndexes = [candidateIndexes[0]];
  const finalIndex = candidateIndexes[candidateIndexes.length - 1];

  for (let index = 1; index < candidateIndexes.length - 1; index += 1) {
    const candidateIndex = candidateIndexes[index];
    if (canSpeakSpan(keptIndexes[keptIndexes.length - 1], candidateIndex)) {
      keptIndexes.push(candidateIndex);
    }
  }

  while (
    keptIndexes.length > 1
    && !canSpeakSpan(keptIndexes[keptIndexes.length - 1], finalIndex)
  ) {
    keptIndexes.pop();
  }
  if (keptIndexes[keptIndexes.length - 1] !== finalIndex) keptIndexes.push(finalIndex);

  return {
    points: keptIndexes.map(index => normalizedPoints[index]),
    rawPointIndexes: keptIndexes
  };
}

function getInstructionSourceIndexes(points, sourceSemanticPointIndexes, fromIndex, toIndex) {
  const lastIndex = points.length - 1;
  if (
    !Array.isArray(sourceSemanticPointIndexes)
    || !sourceSemanticPointIndexes.length
    || sourceSemanticPointIndexes.some(index => (
      !Number.isInteger(index) || index < 0 || index > lastIndex
    ))
  ) {
    return [fromIndex, toIndex];
  }

  const indexes = Array.from(new Set(
    sourceSemanticPointIndexes.filter(index => index >= fromIndex && index <= toIndex)
      .concat([fromIndex, toIndex])
  )).sort((left, right) => left - right);
  return indexes.length >= 2 ? indexes : [fromIndex, toIndex];
}

function getRawDepartureTurn(points, fromIndex, imageSize) {
  return getTurnDirection(
    findDistinctPointBefore(points, fromIndex),
    points[fromIndex],
    findDistinctPointAfter(points, fromIndex),
    imageSize
  );
}

function buildCompositeMovementInstruction(points, sourceIndexes, options) {
  const config = options || {};
  const arrivalText = config.arrivalText || '';
  const parts = [];
  let arrivalConsumed = false;

  for (let index = 1; index < sourceIndexes.length; index += 1) {
    const fromIndex = sourceIndexes[index - 1];
    const toIndex = sourceIndexes[index];
    const turnText = getRawDepartureTurn(points, fromIndex, config.imageSize);
    const meters = percentDistanceToMeters(
      getPolylineLength(points, fromIndex, toIndex, config.imageSize),
      config.distanceMetersPerPercent || 1.2
    );
    const roundedMeters = roundSpokenDistance(meters, config.distanceRoundingMeters);
    const isLastPart = index >= sourceIndexes.length - 1;

    if (roundedMeters > 0) {
      parts.push(formatSpokenMovement(turnText, meters, config.distanceRoundingMeters));
      continue;
    }

    if (isLastPart && arrivalText) {
      parts.push(turnText + '，前方即' + arrivalText);
      arrivalConsumed = true;
    } else if (isLastPart) {
      parts.push(turnText + '，前方即到');
    } else {
      parts.push(turnText + '，前方即到转向点');
    }
  }

  let text = parts.join('；');
  if (arrivalText && !arrivalConsumed) text += '，' + arrivalText;
  return text;
}

function buildSemanticStepInstruction(points, rawPointIndexes, targetSemanticIndex, options) {
  const config = options || {};
  const normalizedPoints = normalizePoints(points);
  const stepPath = buildSemanticStepPath(points, rawPointIndexes);
  const indexes = stepPath.rawPointIndexes;
  const safeSemanticIndex = getSafeStepIndex(targetSemanticIndex, indexes.length - 1);

  if (normalizedPoints.length < 2 || indexes.length < 2) {
    return buildStepInstruction(normalizedPoints, 0, config);
  }

  if (safeSemanticIndex <= 0) {
    return buildStepInstruction(normalizedPoints, 0, config);
  }

  const fromIndex = indexes[safeSemanticIndex - 1];
  const toIndex = indexes[safeSemanticIndex];
  const sourceIndexes = getInstructionSourceIndexes(
    normalizedPoints,
    config.sourceSemanticPointIndexes,
    fromIndex,
    toIndex
  );
  const isArrival = safeSemanticIndex >= indexes.length - 1;
  const point = normalizedPoints[toIndex];
  let arrivalText = '';

  if (isArrival) {
    const arrivalName = config.arrivalName || '目的地';
    const transferInstruction = config.transferInstruction
      || (config.transferFloor ? '到达电梯，请乘坐电梯前往' + config.transferFloor : '到达电梯');
    arrivalText = config.hasNextLeg
      ? buildTransferApproachInstruction(transferInstruction, config.transferFloor)
      : '到达' + arrivalName;
  }

  return {
    text: buildCompositeMovementInstruction(normalizedPoints, sourceIndexes, {
      arrivalText,
      distanceMetersPerPercent: config.distanceMetersPerPercent,
      distanceRoundingMeters: config.distanceRoundingMeters,
      imageSize: config.imageSize
    }),
    point,
    progress: isArrival
      ? 100
      : Math.round(safeSemanticIndex / (indexes.length - 1) * 100),
    isArrival,
    sourceRawPointIndexes: sourceIndexes
  };
}

function roundMarker(marker) {
  return {
    x: Number(marker.x.toFixed(3)),
    y: Number(marker.y.toFixed(3)),
    angle: Number(marker.angle.toFixed(1))
  };
}

function buildLineSegments(points, imageSize) {
  const normalizedPoints = normalizePoints(points);
  if (normalizedPoints.length < 2) return [];

  const aspect = getImageAspect(imageSize);
  const segments = [];

  for (let i = 0; i < normalizedPoints.length - 1; i += 1) {
    const from = normalizedPoints[i];
    const to = normalizedPoints[i + 1];
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const length = Math.sqrt(dx * dx + (dy * aspect) * (dy * aspect));

    if (length <= 0) continue;

    segments.push({
      left: Number(from.x.toFixed(3)),
      top: Number(from.y.toFixed(3)),
      width: Number(length.toFixed(3)),
      angle: Number((Math.atan2(dy * aspect, dx) * 180 / Math.PI).toFixed(1))
    });
  }

  return segments;
}

module.exports = {
  clamp,
  normalizePoint,
  normalizePoints,
  buildSemanticStepPath,
  buildSpokenStepPath,
  getImageAspect,
  getSegmentAngle,
  getSegmentLength,
  getTurnDirection,
  buildPathMetrics,
  getPointAtDistance,
  getPointAtRatio,
  getDurationMs,
  percentDistanceToMeters,
  formatDistance,
  formatTime,
  roundSpokenDistance,
  buildStepInstruction,
  buildSemanticStepInstruction,
  roundMarker,
  buildLineSegments
};
