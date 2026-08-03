const destinationPolicy = require('./destinationPolicy.js');
const maintenanceSameFloorPaths = require('./sameFloorPaths.js');
const floorNavPaths = require('./floorNavPaths.js');
const elevatorGroups = require('./elevatorGroups.js');
const elevatorShafts = require('./elevatorShafts.js');
const elevatorPlanner = require('../utils/elevatorPlanner.js');

const FLOOR_MAP_IMAGES = Object.freeze({
  '1楼': '/assets/floor-maps/1F.jpg',
  '2楼': '/assets/floor-maps/2F.jpg',
  '3楼': '/assets/floor-maps/3F.jpg',
  '4楼': '/assets/floor-maps/4F.jpg',
  '5楼': '/assets/floor-maps/5F.jpg',
  '6楼': '/assets/floor-maps/6F.jpg',
  '7楼': '/assets/floor-maps/7F.jpg',
  '8楼': '/assets/floor-maps/8F.jpg',
  '9楼': '/assets/floor-maps/9F.jpg',
  '10楼': '/assets/floor-maps/10F.jpg',
  '11楼': '/assets/floor-maps/11F.jpg',
  '12楼': '/assets/floor-maps/12F.jpg',
  '13楼': '/assets/floor-maps/13F.jpg'
});

const FLOOR_MAP_IMAGE_SIZE = [5587, 7163];

const NURSE_STATION_DESTINATIONS = {
  '外一科病房': '外一科病房护士站',
  '外二科病房': '外二科病房护士站',
  '骨一科病房': '骨一科病房护士站',
  '骨二科病房': '骨二科病房护士站',
  '内一科病房': '内一科病房护士站',
  '内二科病房': '内二科病房护士站'
};

function createDepartmentRoute(destination) {
  const image = FLOOR_MAP_IMAGES[destination.floor];
  if (!image) throw new Error('未配置楼层地图：' + destination.floor);
  return {
    name: destination.name,
    floor: destination.floor,
    fromElevator: {
      image,
      floor: destination.floor,
      instruction: destination.fromElevator.instruction
    },
    toDestination: {
      image,
      floor: destination.floor,
      instruction: destination.toDestination.instruction
    }
  };
}

const departmentRoutes = destinationPolicy.publicDestinations.map(createDepartmentRoute);
const publicDestinationNames = departmentRoutes.reduce((names, route) => {
  names[route.name] = true;
  return names;
}, Object.create(null));
const sameFloorPaths = Object.keys(maintenanceSameFloorPaths).reduce((paths, key) => {
  const names = key.split('|||');
  if (names.length === 2 && publicDestinationNames[names[0]] && publicDestinationNames[names[1]]) {
    paths[key] = maintenanceSameFloorPaths[key];
  }
  return paths;
}, Object.create(null));

const routeMap = departmentRoutes.reduce((map, item) => {
  map[item.name] = item;
  return map;
}, {});

function getDepartmentNames() {
  return departmentRoutes.map(item => item.name);
}

function getDepartmentRoute(name) {
  const key = String(name || '').trim();
  return routeMap[key] || null;
}

const DEPARTMENT_INPUT_ALIASES = {
  '急诊': '急诊科',
  '急诊室': '急诊科',
  '及诊': '急诊科',
  '急珍': '急诊科',
  '急症': '急诊科',
  '救急': '急诊科',
  '挂号': '挂号缴费',
  '缴费': '挂号缴费',
  '交费': '挂号缴费',
  '收费': '挂号缴费',
  '付费': '挂号缴费',
  '付款': '挂号缴费',
  '交钱': '挂号缴费',
  '缴废': '挂号缴费',
  '叫费': '挂号缴费',
  '教费': '挂号缴费',
  '交飞': '挂号缴费',
  '医保': '挂号缴费',
  '结算': '挂号缴费',
  'ct': '放射科',
  'dr': '放射科',
  '外一科': '外一科病房',
  '外二科': '外二科病房',
  '骨一科': '骨一科病房',
  '骨二科': '骨二科病房',
  '内一科': '内一科病房',
  '内二科': '内二科病房',
  '内一病房': '内一科病房',
  '内二病房': '内二科病房',
  '骨一病房': '骨一科病房',
  '骨二病房': '骨二科病房',
  '外一病房': '外一科病房',
  '外二病房': '外二科病房'
};

const DEPARTMENT_GROUP_ALIASES = {
  '病房': ['儿科病房', '妇产科病房', '外一科病房', '外二科病房', '骨一科病房', '骨二科病房', '内一科病房', '内二科病房'],
  '住院': ['儿科病房', '妇产科病房', '外一科病房', '外二科病房', '骨一科病房', '骨二科病房', '内一科病房', '内二科病房'],
  '住院部': ['儿科病房', '妇产科病房', '外一科病房', '外二科病房', '骨一科病房', '骨二科病房', '内一科病房', '内二科病房'],
  '骨': ['骨一科病房', '骨二科病房'],
  '骨科': ['骨一科病房', '骨二科病房'],
  '骨科病房': ['骨一科病房', '骨二科病房'],
  '骨科住院': ['骨一科病房', '骨二科病房'],
  '骨科住院部': ['骨一科病房', '骨二科病房'],
  '骨科病区': ['骨一科病房', '骨二科病房'],
  '缴': ['挂号缴费'],
  '费': ['挂号缴费'],
  '交': ['挂号缴费'],
  '钱': ['挂号缴费'],
  '付': ['挂号缴费'],
  '款': ['挂号缴费'],
  '医': ['挂号缴费', '中医馆'],
  '挂': ['挂号缴费'],
  '号': ['挂号缴费'],
  '药': ['西药房', '中药房', '静脉用药调配中心'],
  '拿': ['西药房', '中药房'],
  '领': ['西药房', '中药房'],
  '买': ['西药房'],
  '取药': ['西药房', '中药房'],
  '儿': ['儿科门诊', '儿科病房'],
  '儿童': ['儿科门诊', '儿科病房'],
  '小孩': ['儿科门诊', '儿科病房'],
  '孩': ['儿科门诊', '儿科病房'],
  '宝': ['儿科门诊', '儿科病房'],
  '急': ['急诊科', '急诊科办公区'],
  '急救': ['急诊科'],
  '烧': ['急诊科', '儿科门诊', '内科门诊'],
  '痛': ['急诊科', '内科门诊', '外科门诊', '口腔科门诊'],
  '咳': ['内科门诊', '儿科门诊'],
  '晕': ['内科门诊', '急诊科'],
  '内': ['内科门诊', '内镜诊疗中心', '内一科病房', '内二科病房'],
  '内科': ['内科门诊', '内一科病房', '内二科病房'],
  '内科病房': ['内一科病房', '内二科病房'],
  '内科住院': ['内一科病房', '内二科病房'],
  '内科住院部': ['内一科病房', '内二科病房'],
  '内科病区': ['内一科病房', '内二科病房'],
  '内科护士站': ['内一科病房', '内二科病房'],
  '外': ['外科门诊', '外一科病房', '外二科病房'],
  '外科': ['外科门诊', '外一科病房', '外二科病房'],
  '外科病房': ['外一科病房', '外二科病房'],
  '外科住院': ['外一科病房', '外二科病房'],
  '外科住院部': ['外一科病房', '外二科病房'],
  '外科病区': ['外一科病房', '外二科病房'],
  '外科护士站': ['外一科病房', '外二科病房'],
  '妇': ['妇科门诊', '妇产科病房'],
  '产': ['产科门诊', '产房', '妇产科病房'],
  '孕': ['产科门诊'],
  '检': ['检验科'],
  '验': ['检验科'],
  '抽': ['检验科'],
  '采': ['检验科'],
  '化': ['检验科'],
  '超': ['超声医学科'],
  '彩超': ['超声医学科'],
  '放': ['放射科'],
  '片': ['放射科'],
  '光': ['放射科'],
  '拍片': ['放射科'],
  '输': ['输液室', '输血科'],
  '针': ['输液室'],
  '血': ['输血科', '血液透析科', '检验科'],
  '口': ['口腔科门诊'],
  '牙': ['口腔科门诊'],
  '眼': ['眼科门诊', '耳鼻喉科门诊'],
  '耳': ['耳鼻喉科门诊'],
  '鼻': ['耳鼻喉科门诊'],
  '喉': ['耳鼻喉科门诊'],
  '皮': ['皮肤科门诊'],
  '肤': ['皮肤科门诊'],
  '尿': ['泌尿科门诊'],
  '泌': ['泌尿科门诊'],
  '肛': ['肛肠科门诊'],
  '肠': ['肛肠科门诊'],
  '碎': ['碎石室'],
  '石': ['碎石室'],
  '胃': ['内镜诊疗中心'],
  '镜': ['内镜诊疗中心'],
  '刀': ['外科门诊'],
  '伤': ['伤口门诊', '外科门诊', '急诊科'],
  '消': ['消控室'],
  '计算机': ['计算机机房'],
  '电脑': ['计算机机房']
};

const GENERIC_PHARMACY_ALIASES = Object.freeze([
  '药房',
  '取药',
  '拿药',
  '要房',
  '领药',
  '买药',
  '开药'
]);
const GENERIC_PHARMACY_DESTINATIONS = Object.freeze(['中药房', '西药房']);

const SEARCH_CHAR_FOLD_MAP = {
  '及': '急',
  '级': '急',
  '疾': '急',
  '症': '诊',
  '珍': '诊',
  '真': '诊',
  '正': '诊',
  '交': '缴',
  '教': '缴',
  '叫': '缴',
  '废': '费',
  '飛': '费',
  '號': '号',
  '挂': '挂',
  '掛': '挂',
  '藥': '药',
  '要': '药',
  '股': '骨',
  '古': '骨',
  '可': '科',
  '颗': '科',
  '棵': '科',
  '课': '科',
  '房': '房',
  '坊': '房',
  '尔': '儿',
  '兒': '儿',
  '內': '内',
  '奈': '内',
  '歪': '外',
  '验': '眼',
  '咽': '眼',
  '妇': '妇',
  '付': '妇',
  '肤': '肤',
  '复': '肤',
  '牙': '牙',
  '雅': '牙',
  '肠': '肠',
  '场': '肠',
  '鏡': '镜',
  '径': '镜'
};

function normalizeDepartmentInput(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[，。！？、,.!?]/g, '');
}

function normalizeDepartmentSearchName(name) {
  return normalizeDepartmentInput(name)
    .replace(/病区/g, '病房')
    .replace(/科室/g, '')
    .replace(/门诊$/g, '')
    .replace(/病房$/g, '');
}

function foldDepartmentSearchText(value) {
  const normalized = normalizeDepartmentSearchName(value)
    .replace(/挂号交费/g, '挂号缴费')
    .replace(/交费/g, '缴费')
    .replace(/收费/g, '缴费')
    .replace(/付款/g, '缴费')
    .replace(/付费/g, '缴费')
    .replace(/取药/g, '药房')
    .replace(/拿药/g, '药房');
  return normalized.split('').map(char => SEARCH_CHAR_FOLD_MAP[char] || char).join('');
}

function stripIntentWords(value) {
  return normalizeDepartmentInput(value)
    .replace(/^(我是|我现在在|现在在|当前位置是|位置是)/, '')
    .replace(/^(我要去|我要到|我想去|想到|想去|带我去|帮我去|请带我去|导航到|导航去|去|到|找|查|问)/, '')
    .replace(/(在哪里|在哪儿|在哪|在几楼|几楼|怎么走|怎么去|路线|位置|地方|科室|窗口|处)$/g, '');
}

function getSearchQueries(value) {
  const normalized = normalizeDepartmentInput(value);
  const stripped = stripIntentWords(normalized);
  const folded = foldDepartmentSearchText(normalized);
  const strippedFolded = foldDepartmentSearchText(stripped);
  const compact = normalized.replace(/^(我要|我想|想|去|到|找|导航)/, '');
  const queries = [normalized, stripped, compact, folded, strippedFolded]
    .filter(Boolean);
  return queries.filter((item, index) => queries.indexOf(item) === index);
}

function hasWardIntent(value) {
  return /病房|病区|住院|住院部|护士站|护理站/.test(normalizeDepartmentInput(value));
}

function hasOutpatientIntent(value) {
  return /门诊|门珍|门正|看诊|就诊/.test(normalizeDepartmentInput(value));
}

function isWardDepartmentName(name) {
  return /病房/.test(String(name || '')) || isNurseStationDestination(name);
}

function isOutpatientDepartmentName(name) {
  return /门诊/.test(String(name || ''));
}

function filterMatchesByIntent(matches, value) {
  if (hasWardIntent(value)) {
    const wardMatches = matches.filter(isWardDepartmentName);
    if (wardMatches.length) return wardMatches;
  }
  if (hasOutpatientIntent(value)) {
    const outpatientMatches = matches.filter(isOutpatientDepartmentName);
    if (outpatientMatches.length) return outpatientMatches;
  }
  return matches;
}

function getIntentRank(name, value) {
  if (hasWardIntent(value)) return isWardDepartmentName(name) ? 0 : 8;
  if (hasOutpatientIntent(value)) return isOutpatientDepartmentName(name) ? 0 : 4;
  return 0;
}

function getDepartmentSearchTokens(name) {
  const arrivalName = getArrivalName(name);
  const tokens = [
    name,
    arrivalName,
    normalizeDepartmentSearchName(name),
    normalizeDepartmentSearchName(arrivalName),
    String(name || '').replace(/门诊|病房|科室/g, ''),
    String(arrivalName || '').replace(/门诊|病房|科室/g, '')
  ];
  return tokens
    .concat(tokens.map(foldDepartmentSearchText))
    .filter(Boolean)
    .filter((item, index, source) => source.indexOf(item) === index);
}

function levenshteinDistance(a, b) {
  const left = String(a || '');
  const right = String(b || '');
  if (left === right) return 0;
  if (!left) return right.length;
  if (!right) return left.length;

  const previous = [];
  for (let j = 0; j <= right.length; j += 1) previous[j] = j;

  for (let i = 1; i <= left.length; i += 1) {
    const current = [i];
    for (let j = 1; j <= right.length; j += 1) {
      const cost = left[i - 1] === right[j - 1] ? 0 : 1;
      current[j] = Math.min(
        current[j - 1] + 1,
        previous[j] + 1,
        previous[j - 1] + cost
      );
    }
    for (let j = 0; j <= right.length; j += 1) previous[j] = current[j];
  }
  return previous[right.length];
}

function getFuzzyMatchScore(name, queries) {
  const tokens = getDepartmentSearchTokens(name);
  let bestScore = Infinity;
  queries.forEach(query => {
    const foldedQuery = foldDepartmentSearchText(query);
    tokens.forEach(token => {
      const foldedToken = foldDepartmentSearchText(token);
      if (!foldedQuery || !foldedToken) return;
      if (foldedToken === foldedQuery) {
        bestScore = Math.min(bestScore, 0);
        return;
      }
      if (foldedToken.indexOf(foldedQuery) === 0) {
        bestScore = Math.min(bestScore, 1);
        return;
      }
      if (foldedToken.indexOf(foldedQuery) !== -1 || foldedQuery.indexOf(foldedToken) !== -1) {
        bestScore = Math.min(bestScore, 2);
        return;
      }
      if (foldedQuery.length < 2) return;
      const distance = levenshteinDistance(foldedQuery, foldedToken);
      const allowed = foldedQuery.length <= 3 ? 1 : Math.min(2, Math.ceil(foldedQuery.length * 0.34));
      if (distance <= allowed) {
        bestScore = Math.min(bestScore, 4 + distance);
      }
    });
  });
  return bestScore;
}

function getAliasDepartmentName(value) {
  const normalized = normalizeDepartmentInput(value);
  return DEPARTMENT_INPUT_ALIASES[normalized] || '';
}

function getGenericPharmacyMatches(value) {
  const normalized = normalizeDepartmentInput(value);
  if (!normalized || /中药|西药|静脉用药/.test(normalized)) return [];
  const isGeneric = getSearchQueries(normalized).some(query => (
    GENERIC_PHARMACY_ALIASES.indexOf(query) !== -1
  ));
  if (!isGeneric) return [];
  return GENERIC_PHARMACY_DESTINATIONS.filter(name => getDepartmentRoute(name));
}

function getLongestContainedAliasMatches(value, aliasMap) {
  const normalizedValue = normalizeDepartmentInput(value);
  const aliases = Object.keys(aliasMap || {})
    .filter(alias => alias && normalizedValue.indexOf(alias) !== -1)
    .sort((a, b) => b.length - a.length);
  if (!aliases.length) return [];

  const bestLength = aliases[0].length;
  const bestAliases = aliases.filter(alias => alias.length === bestLength);
  const matches = [];
  bestAliases.forEach(alias => {
    const target = aliasMap[alias];
    const targets = Array.isArray(target) ? target : [target];
    targets.forEach(name => {
      if (name && matches.indexOf(name) === -1) matches.push(name);
    });
  });
  return matches;
}

function sortDepartmentMatches(matches, value) {
  const normalizedValue = normalizeDepartmentInput(value);
  const queries = getSearchQueries(value);
  const uniqueMatches = [];
  matches.forEach(name => {
    if (uniqueMatches.indexOf(name) === -1) uniqueMatches.push(name);
  });
  const intentMatches = filterMatchesByIntent(uniqueMatches, value);
  return intentMatches.sort((a, b) => {
    const aIntentRank = getIntentRank(a, value);
    const bIntentRank = getIntentRank(b, value);
    if (aIntentRank !== bIntentRank) return aIntentRank - bIntentRank;

    const aName = normalizeDepartmentInput(a);
    const bName = normalizeDepartmentInput(b);
    const aSearch = normalizeDepartmentSearchName(a);
    const bSearch = normalizeDepartmentSearchName(b);
    const aScore = Math.min(
      aName === normalizedValue ? 0 : aSearch === normalizedValue ? 1 : aName.indexOf(normalizedValue) === 0 ? 2 : 3,
      getFuzzyMatchScore(a, queries)
    );
    const bScore = Math.min(
      bName === normalizedValue ? 0 : bSearch === normalizedValue ? 1 : bName.indexOf(normalizedValue) === 0 ? 2 : 3,
      getFuzzyMatchScore(b, queries)
    );
    if (aScore !== bScore) return aScore - bScore;
    return a.length - b.length;
  });
}

function getArrivalName(name) {
  const key = String(name || '').trim();
  return NURSE_STATION_DESTINATIONS[key] || key;
}

function isNurseStationDestination(name) {
  const key = String(name || '').trim();
  return Boolean(NURSE_STATION_DESTINATIONS[key]);
}

function matchDepartments(keyword) {
  const value = normalizeDepartmentInput(keyword);
  if (!value) return [];
  const queries = getSearchQueries(value);

  if (getDepartmentRoute(value)) return [value];

  const pharmacyMatches = getGenericPharmacyMatches(value);
  if (pharmacyMatches.length) return pharmacyMatches;

  const aliasName = getAliasDepartmentName(value);
  if (aliasName && getDepartmentRoute(aliasName)) return [aliasName];

  const groupMatches = DEPARTMENT_GROUP_ALIASES[value] || [];
  if (groupMatches.length) return filterMatchesByIntent(groupMatches.filter(name => getDepartmentRoute(name)), value);

  const containedExactMatches = getDepartmentNames().filter(name => value.indexOf(name) !== -1);
  if (containedExactMatches.length) {
    return sortDepartmentMatches(filterMatchesByIntent(containedExactMatches, value), value);
  }

  const containedInputAliasMatches = getLongestContainedAliasMatches(value, DEPARTMENT_INPUT_ALIASES)
    .filter(name => getDepartmentRoute(name));
  if (containedInputAliasMatches.length) {
    return sortDepartmentMatches(filterMatchesByIntent(containedInputAliasMatches, value), value);
  }

  const rawValue = String(keyword || '').trim();
  const rawMatches = getDepartmentNames().filter(name => {
    return rawValue && String(name).indexOf(rawValue) !== -1;
  });
  if (rawMatches.length) {
    return sortDepartmentMatches(filterMatchesByIntent(rawMatches, value), value);
  }

  const containedGroupMatches = getLongestContainedAliasMatches(value, DEPARTMENT_GROUP_ALIASES)
    .filter(name => getDepartmentRoute(name));
  if (containedGroupMatches.length) {
    return sortDepartmentMatches(filterMatchesByIntent(containedGroupMatches, value), value);
  }

  const matches = getDepartmentNames().filter(name => getFuzzyMatchScore(name, queries) < Infinity);
  return sortDepartmentMatches(filterMatchesByIntent(matches, value), value);
}

function resolveDepartmentName(name) {
  const value = normalizeDepartmentInput(name);
  if (!value) {
    return {
      ok: false,
      status: 'empty',
      name: '',
      matches: [],
      message: '请填写当前位置和目的地'
    };
  }

  if (getDepartmentRoute(value)) {
    return {
      ok: true,
      status: 'exact',
      name: value,
      matches: [value],
      message: ''
    };
  }

  const aliasName = getAliasDepartmentName(value);
  if (aliasName && getDepartmentRoute(aliasName)) {
    return {
      ok: true,
      status: 'alias',
      name: aliasName,
      matches: [aliasName],
      message: ''
    };
  }

  const matches = matchDepartments(value);
  if (matches.length === 1) {
    return {
      ok: true,
      status: 'uniqueMatch',
      name: matches[0],
      matches,
      message: ''
    };
  }

  if (matches.length > 1) {
    return {
      ok: false,
      status: 'ambiguous',
      name: '',
      matches,
      message: '“' + value + '”匹配到多个科室：' + matches.join('、') + '。请选择具体科室。'
    };
  }

  return {
    ok: false,
    status: 'notFound',
    name: '',
    matches: [],
    message: '未找到“' + value + '”对应的科室，请检查名称或从候选科室中选择。'
  };
}

function getResolvedDepartmentRoute(name) {
  const resolved = resolveDepartmentName(name);
  return resolved.ok ? getDepartmentRoute(resolved.name) : null;
}

function getElevatorGroupsForFloor(floor) {
  return elevatorGroups[floor] || [];
}

function getTransferInstruction(floor, elevatorDisplayName) {
  const targetFloor = String(floor || '').trim() || '目的地楼层';
  const elevatorName = String(elevatorDisplayName || '').trim() || '电梯';
  return '已到达' + elevatorName + '，请乘坐' + elevatorName + '前往' + targetFloor;
}

function nameElevatorInText(text, elevatorDisplayName) {
  if (!elevatorDisplayName) return text;
  return String(text || '').replace(/电梯/g, elevatorDisplayName);
}

function hasDistinctFinitePoints(points) {
  if (!Array.isArray(points) || points.length < 2) return false;
  if (!points.every(point => (
    Array.isArray(point)
    && point.length >= 2
    && Number.isFinite(point[0])
    && Number.isFinite(point[1])
  ))) return false;
  const first = points[0];
  return points.some(point => point[0] !== first[0] || point[1] !== first[1]);
}

function isCoLocatedPath(path, distanceMetersPerPercent) {
  if (!path || typeof path !== 'object') return false;
  if (path.coLocated === true) return true;
  const scale = distanceMetersPerPercent === undefined ? 1.2 : distanceMetersPerPercent;
  if (!Number.isFinite(scale) || scale <= 0) return false;
  if (
    typeof path.routeLength !== 'number'
    || !Number.isFinite(path.routeLength)
    || path.routeLength < 0
  ) return false;
  return Math.round(path.routeLength * scale) === 0;
}

function buildLeg(
  kind,
  departmentRoute,
  floorPathConfig,
  selectedElevatorShaftId,
  selectedElevatorDisplayName
) {
  const isFromLocation = kind === 'fromLocation';
  const routeInfo = isFromLocation
    ? departmentRoute.fromElevator
    : departmentRoute.toDestination;
  const arrivalName = getArrivalName(departmentRoute.name);
  const isNurseStationTarget = !isFromLocation && isNurseStationDestination(departmentRoute.name);
  const floor = routeInfo.floor || departmentRoute.floor || '';
  const hasRoutePath = Boolean(floorPathConfig && hasDistinctFinitePoints(floorPathConfig.points));
  const points = hasRoutePath ? floorPathConfig.points : [];
  const image = (floorPathConfig && floorPathConfig.image) || FLOOR_MAP_IMAGES[floor] || routeInfo.image;
  const elevatorName = selectedElevatorDisplayName || '电梯';
  const baseInstruction = isNurseStationTarget
    ? '请从电梯出发，沿路线前往病区护士站'
    : routeInfo.instruction;

  const leg = {
    kind,
    departmentName: departmentRoute.name,
    image,
    elevatorGroups: getElevatorGroupsForFloor(floor),
    floor: routeInfo.floor || departmentRoute.floor || '楼层待补充',
    title: isFromLocation
      ? departmentRoute.name + ' → ' + elevatorName
      : elevatorName + ' → ' + arrivalName,
    instruction: hasRoutePath
      ? nameElevatorInText(baseInstruction, selectedElevatorDisplayName)
      : '当前路线缺少已确认的精确轨迹点',
    points,
    imageSize: floorPathConfig && floorPathConfig.imageSize
      ? floorPathConfig.imageSize
      : FLOOR_MAP_IMAGE_SIZE,
    hasRoutePath,
    routePath: floorPathConfig,
    arrivalName,
    isNurseStationTarget
  };
  if (selectedElevatorShaftId) {
    leg.selectedElevatorShaftId = selectedElevatorShaftId;
  }
  if (selectedElevatorDisplayName) {
    leg.selectedElevatorDisplayName = selectedElevatorDisplayName;
  }
  return leg;
}

function getSameFloorPathKey(locationName, destinationName) {
  return String(locationName || '').trim() + '|||' + String(destinationName || '').trim();
}

function buildSameFloorLeg(locationRoute, destinationRoute) {
  const key = getSameFloorPathKey(locationRoute.name, destinationRoute.name);
  const directPath = sameFloorPaths[key] || null;
  const arrivalName = getArrivalName(destinationRoute.name);
  const isNurseStationTarget = isNurseStationDestination(destinationRoute.name);
  const hasRoutePath = Boolean(directPath && hasDistinctFinitePoints(directPath.points));
  const points = hasRoutePath ? directPath.points : [];
  const image = FLOOR_MAP_IMAGES[locationRoute.floor] || destinationRoute.toDestination.image;

  return {
    kind: 'sameFloor',
    departmentName: destinationRoute.name,
    fromDepartmentName: locationRoute.name,
    image,
    elevatorGroups: getElevatorGroupsForFloor(locationRoute.floor),
    floor: locationRoute.floor,
    title: locationRoute.name + ' → ' + arrivalName,
    instruction: hasRoutePath
      ? (isNurseStationTarget ? '同楼层直达，请沿路线前往本层护士站' : '同楼层直达，请沿路线前往目的地')
      : '同楼层直达路线缺少已确认的精确轨迹点',
    points,
    imageSize: directPath && directPath.imageSize
      ? directPath.imageSize
      : FLOOR_MAP_IMAGE_SIZE,
    hasRoutePath,
    routePath: directPath,
    arrivalName,
    isNurseStationTarget
  };
}

function createNavigationPlan(locationName, destinationName) {
  const locationResolved = resolveDepartmentName(locationName);
  const destinationResolved = resolveDepartmentName(destinationName);

  if (!locationResolved.ok || !destinationResolved.ok) {
    return {
      ok: false,
      message: !locationResolved.ok ? locationResolved.message : destinationResolved.message,
      locationResolved,
      destinationResolved
    };
  }

  const location = getDepartmentRoute(locationResolved.name);
  const destination = getDepartmentRoute(destinationResolved.name);

  if (location.name === destination.name) {
    return {
      ok: false,
      status: 'sameDepartment',
      message: '当前位置和目的地相同，已在' + getArrivalName(destination.name),
      location,
      destination,
      locationResolved,
      destinationResolved
    };
  }

  if (location.floor === destination.floor && location.name !== destination.name) {
    const directPath = sameFloorPaths[getSameFloorPathKey(location.name, destination.name)] || null;
    if (isCoLocatedPath(directPath)) {
      return {
        ok: true,
        status: 'coLocated',
        message: '当前位置与目的地位于同一区域，请根据现场标识确认',
        legs: []
      };
    }
    return {
      ok: true,
      status: 'route',
      message: '',
      mode: 'sameFloor',
      location,
      destination,
      legs: [
        buildSameFloorLeg(location, destination)
      ]
    };
  }

  const selection = elevatorPlanner.selectNearestElevatorShaft({
    fromRoute: location,
    toRoute: destination,
    shafts: elevatorShafts,
    floorNavPaths
  });
  if (!selection.ok) {
    return {
      ok: false,
      status: 'noCommonElevator',
      message: '当前楼层与目标楼层没有已确认可直达的同一电梯，请咨询导医台或现场工作人员。',
      legs: []
    };
  }

  const selectedElevatorShaftId = selection.selectedElevatorShaftId;
  const selectedElevatorDisplayName = selection.shaft.displayName;
  const fromLeg = buildLeg(
    'fromLocation',
    location,
    selection.toElevatorPath,
    selectedElevatorShaftId,
    selectedElevatorDisplayName
  );
  const destinationLeg = buildLeg(
    'toDestination',
    destination,
    selection.fromElevatorPath,
    selectedElevatorShaftId,
    selectedElevatorDisplayName
  );
  fromLeg.transferFloor = destination.floor;
  fromLeg.transferInstruction = getTransferInstruction(
    destination.floor,
    selectedElevatorDisplayName
  );

  return {
    ok: true,
    status: 'route',
    message: '',
    mode: 'crossFloor',
    selectedElevatorShaftId,
    selectedElevatorDisplayName,
    location,
    destination,
    legs: [
      fromLeg,
      destinationLeg
    ]
  };
}

function getDepartmentLeg(name, kind) {
  const departmentRoute = getResolvedDepartmentRoute(name);
  if (!departmentRoute) return null;
  return buildLeg(kind || 'toDestination', departmentRoute, null, null);
}

module.exports = {
  departmentRoutes,
  FLOOR_MAP_IMAGES,
  FLOOR_MAP_IMAGE_SIZE,
  NURSE_STATION_DESTINATIONS,
  getDepartmentNames,
  getDepartmentRoute,
  resolveDepartmentName,
  getResolvedDepartmentRoute,
  getDepartmentLeg,
  getArrivalName,
  getTransferInstruction,
  isNurseStationDestination,
  matchDepartments,
  getSameFloorPathKey,
  isCoLocatedPath,
  createNavigationPlan
};
