function normalizeText(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[，。！？、,.!?]/g, '');
}

function normalizeDepartmentName(value) {
  return normalizeText(value)
    .replace(/科室/g, '')
    .replace(/病区/g, '病房')
    .replace(/挂号交费/g, '挂号缴费')
    .replace(/挂号处/g, '挂号缴费')
    .replace(/缴费处/g, '挂号缴费')
    .replace(/收费处/g, '挂号缴费')
    .replace(/付款处/g, '挂号缴费');
}

const DEPARTMENT_ALIASES = {
  挂号缴费: ['挂号缴费', '挂号交费', '缴费', '交费', '缴废', '叫费', '教费', '收费', '付款', '付费', '挂号', '交钱', '付钱', '交款', '门诊缴费', '门诊交费', '收费台', '收费窗口', '缴费窗口', '付款窗口', '办卡', '结算', '医保缴费', '医保', '报销'],
  中药房: ['中药', '中药房', '取中药', '中草药'],
  西药房: ['西药', '西药房'],
  急诊科: ['急诊', '急诊科', '急症', '急珍', '及诊', '救急', '发烧', '高烧', '胸痛', '腹痛', '摔伤', '流血', '很急', '抢救', '头破了', '晕倒', '昏倒', '呼吸困难'],
  输液室: ['输液', '输液室', '打针', '吊针', '挂水', '注射'],
  放射科: ['放射', '拍片', '放射科', 'DR', 'CT', 'X光', '胸片', '骨片'],
  检验科: ['检验', '化验', '抽血', '验血', '验尿', '检验科', '采血', '查血', '查尿', '验单'],
  超声医学科: ['超声', 'B超', '彩超', '超声医学', '做超声', '做彩超'],
  妇科门诊: ['妇科', '妇科门诊', '月经', '白带', '妇科病'],
  产科门诊: ['产科', '产科门诊', '孕检', '产检', '怀孕', '孕妇'],
  儿科门诊: ['儿科', '儿科门诊', '儿童', '小孩', '孩子', '宝宝', '婴儿', '小朋友', '孩子发烧', '小孩发烧', '宝宝发烧', '儿童发烧', '孩子咳嗽', '小孩咳嗽'],
  内科门诊: ['内科', '内科门诊', '咳嗽', '头晕', '胃痛', '胃疼', '感冒', '高血压', '糖尿病', '肚子疼'],
  外科门诊: ['外科', '外科门诊', '伤口', '刀口', '换药', '外伤'],
  口腔科门诊: ['口腔', '牙科', '口腔科', '牙疼', '牙痛', '牙齿痛', '拔牙', '补牙', '洗牙'],
  眼科门诊: ['眼科', '眼睛', '眼痛', '看眼睛', '视力'],
  耳鼻喉科门诊: ['耳鼻喉', '耳鼻咽喉', '耳朵', '鼻子', '喉咙', '咽喉', '鼻炎'],
  皮肤科门诊: ['皮肤', '皮肤科', '皮疹', '过敏', '湿疹', '痘痘'],
  骨一科病房: ['骨一', '骨一科', '骨一科病房', '骨一病房', '骨科病房', '骨科住院', '骨科病区'],
  骨二科病房: ['骨二', '骨二科', '骨二科病房', '骨二病房', '骨二可', '骨科病房', '骨科住院', '骨科病区'],
  外一科病房: ['外一', '外一科', '外一科病房', '外一病房', '外科病房', '外科住院', '外科病区'],
  外二科病房: ['外二', '外二科', '外二科病房', '外二病房', '外科病房', '外科住院', '外科病区'],
  内一科病房: ['内一', '内一科', '内一科病房', '内一病房', '内科病房', '内科住院', '内科病区', '内科护士站'],
  内二科病房: ['内二', '内二科', '内二科病房', '内二病房', '内科病房', '内科住院', '内科病区', '内科护士站']
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

function unique(items) {
  const seen = {};
  return items.filter(item => {
    if (seen[item]) return false;
    seen[item] = true;
    return true;
  });
}

function getDepartmentNames(routes) {
  return routes && routes.getDepartmentNames ? routes.getDepartmentNames() : [];
}

function stripIntentWords(value) {
  return normalizeDepartmentName(value)
    .replace(/^(我想要|我想|我要|请问|请帮我|帮我|带我|麻烦|想)/, '')
    .replace(/^(去|到|前往|导航到|导航|找|问|查|咨询)/, '')
    .replace(/(在几楼|在哪儿|在哪里|在哪|几楼|怎么走|怎么去|路线|位置|地方)$/, '')
    .replace(/在$/, '');
}

function getQueryVariants(text) {
  const normalizedText = normalizeDepartmentName(text);
  return unique([
    normalizedText,
    stripIntentWords(normalizedText),
    normalizedText.replace(/^(我要去|我要到|想去|想到|带我去|导航到|去|到|找)/, ''),
    normalizedText.replace(/(的地方|地方|窗口|处|科室)$/g, '')
  ]).filter(Boolean);
}

function getGenericPharmacyMatches(text, routes) {
  const normalized = normalizeDepartmentName(text);
  if (!normalized || /中药|西药|静脉用药/.test(normalized)) return [];
  const isGeneric = getQueryVariants(text).some(query => (
    GENERIC_PHARMACY_ALIASES.indexOf(query) !== -1
  ));
  if (!isGeneric) return [];

  const availableNames = getDepartmentNames(routes);
  return GENERIC_PHARMACY_DESTINATIONS.filter(name => availableNames.indexOf(name) !== -1);
}

function hasWardIntent(value) {
  return /病房|病区|住院|住院部|护士站|护理站/.test(normalizeDepartmentName(value));
}

function hasOutpatientIntent(value) {
  return /门诊|门珍|门正|看诊|就诊/.test(normalizeDepartmentName(value));
}

function isWardDepartmentName(name, routes) {
  return /病房/.test(String(name || '')) || Boolean(routes && routes.isNurseStationDestination && routes.isNurseStationDestination(name));
}

function isOutpatientDepartmentName(name) {
  return /门诊/.test(String(name || ''));
}

function filterMatchesByIntent(matches, text, routes) {
  if (hasWardIntent(text)) {
    const wardMatches = matches.filter(name => isWardDepartmentName(name, routes));
    if (wardMatches.length) return wardMatches;
  }
  if (hasOutpatientIntent(text)) {
    const outpatientMatches = matches.filter(isOutpatientDepartmentName);
    if (outpatientMatches.length) return outpatientMatches;
  }
  return matches;
}

function resolveIntentScopedDepartments(queryVariants, routes) {
  const intentText = queryVariants.join('');
  if ((!hasWardIntent(intentText) && !hasOutpatientIntent(intentText)) || !routes || !routes.matchDepartments) {
    return null;
  }

  let collectedMatches = [];
  for (let i = 0; i < queryVariants.length; i += 1) {
    const queryMatches = filterMatchesByIntent(routes.matchDepartments(queryVariants[i]) || [], intentText, routes);
    if (queryMatches.length === 1) {
      return { name: queryMatches[0], ambiguous: false, matches: queryMatches, message: '' };
    }
    collectedMatches = collectedMatches.concat(queryMatches);
  }

  const filtered = unique(collectedMatches);

  if (filtered.length > 1) {
    const intentLabel = hasWardIntent(intentText) ? '病房' : '门诊';
    return {
      name: '',
      ambiguous: true,
      matches: filtered,
      message: '你要去的是' + intentLabel + '，请再选择具体科室：' + filtered.join('、') + '。'
    };
  }

  return null;
}

function getContextualAliasBonus(target, query) {
  if (target === '儿科门诊' && /(孩子|小孩|儿童|宝宝|婴儿|小朋友)/.test(query) && /(发烧|高烧|咳嗽|感冒|肚子疼|腹痛)/.test(query)) {
    return 42;
  }
  if (target === '急诊科' && /(很急|急救|抢救|胸痛|呼吸困难|晕倒|昏倒|流血|摔伤|头破了)/.test(query)) {
    return 42;
  }
  if (target === '挂号缴费' && /(缴|交|费|钱|付款|医保|结算|报销)/.test(query)) {
    return 18;
  }
  if (target === '口腔科门诊' && /(牙|口腔|拔牙|补牙|洗牙)/.test(query)) {
    return 18;
  }
  if (target === '检验科' && /(抽血|验血|验尿|化验|检验|采血)/.test(query)) {
    return 18;
  }
  if (target === '放射科' && /(拍片|胸片|骨片|X光|DR|CT)/i.test(query)) {
    return 18;
  }
  return 0;
}

function getAliasMatchScore(target, alias, query) {
  if (!alias || !query) return 0;
  if (query === alias) return 120 + alias.length * 2;
  if (query.indexOf(alias) !== -1) return 80 + alias.length * 2 + getContextualAliasBonus(target, query);
  if (alias.indexOf(query) !== -1) return query.length === 1 ? 0 : 56 + query.length;
  return 0;
}

function matchAlias(queryVariants, routes) {
  const names = getDepartmentNames(routes);
  const validNameMap = names.reduce((map, name) => {
    map[name] = true;
    return map;
  }, {});

  const aliasTargets = Object.keys(DEPARTMENT_ALIASES);
  let bestTarget = null;
  let bestScore = 0;
  let bestAliasLength = 0;

  for (let i = 0; i < aliasTargets.length; i += 1) {
    const target = aliasTargets[i];
    if (!validNameMap[target]) continue;

    const aliases = DEPARTMENT_ALIASES[target].map(normalizeDepartmentName);
    for (let q = 0; q < queryVariants.length; q += 1) {
      const query = queryVariants[q];
      for (let a = 0; a < aliases.length; a += 1) {
        const alias = aliases[a];
        const score = getAliasMatchScore(target, alias, query);
        if (
          score > bestScore ||
          (score === bestScore && alias.length > bestAliasLength)
        ) {
          bestTarget = target;
          bestScore = score;
          bestAliasLength = alias.length;
        }
      }
    }
  }

  return bestScore > 0 ? bestTarget : null;
}

function resolveDepartmentByRoutes(queryVariants, routes) {
  if (!routes || !routes.resolveDepartmentName) return null;

  let ambiguousResult = null;
  for (let i = 0; i < queryVariants.length; i += 1) {
    const resolved = routes.resolveDepartmentName(queryVariants[i]);
    if (resolved && resolved.ok) {
      return {
        name: resolved.name,
        ambiguous: false,
        matches: resolved.matches || [],
        message: ''
      };
    }
    if (resolved && resolved.status === 'ambiguous') {
      ambiguousResult = {
        name: '',
        ambiguous: true,
        matches: resolved.matches || [],
        message: resolved.message || ''
      };
    }
  }

  return ambiguousResult;
}

function isDepartmentLikeQuery(text) {
  return /科|门诊|病房|病区|住院|护士站|内科|外科|骨科|急诊|口腔|眼科|耳鼻喉|妇科|产科|儿科/.test(normalizeDepartmentName(text));
}

function matchDepartmentDetailed(text, routes) {
  const normalizedText = normalizeDepartmentName(text);
  if (!normalizedText) return { name: '', ambiguous: false, matches: [], message: '' };
  const queryVariants = getQueryVariants(text);

  const intentScopedMatch = resolveIntentScopedDepartments(queryVariants, routes);
  if (intentScopedMatch) return intentScopedMatch;

  const routeResolved = resolveDepartmentByRoutes(queryVariants, routes);
  if (routeResolved && (!routeResolved.ambiguous || isDepartmentLikeQuery(normalizedText))) {
    return routeResolved;
  }

  const aliasMatch = matchAlias(queryVariants, routes);
  if (aliasMatch) {
    return { name: aliasMatch, ambiguous: false, matches: [aliasMatch], message: '' };
  }

  if (routeResolved) return routeResolved;

  const names = getDepartmentNames(routes).slice().sort((a, b) => b.length - a.length);
  for (let i = 0; i < names.length; i += 1) {
    const name = names[i];
    const normalizedName = normalizeDepartmentName(name);
    const matched = queryVariants.some(query => (
      query.indexOf(normalizedName) !== -1 || normalizedName.indexOf(query) !== -1
    ));
    if (matched) {
      return { name, ambiguous: false, matches: [name], message: '' };
    }
  }

  const fuzzyMatches = names.filter(name => {
    const normalizedName = normalizeDepartmentName(name)
      .replace(/门诊$/g, '')
      .replace(/病房$/g, '')
      .replace(/中心$/g, '')
      .replace(/科$/g, '');
    return normalizedName && queryVariants.some(query => query.indexOf(normalizedName) !== -1);
  });

  if (fuzzyMatches.length > 1) {
    return {
      name: '',
      ambiguous: true,
      matches: fuzzyMatches,
      message: '匹配到多个科室：' + fuzzyMatches.join('、') + '。请选择具体科室。'
    };
  }

  if (routes && routes.matchDepartments) {
    const routeMatches = unique(queryVariants.reduce((items, query) => {
      return items.concat(routes.matchDepartments(query));
    }, [])).slice(0, 6);
    if (routeMatches.length === 1) {
      return { name: routeMatches[0], ambiguous: false, matches: routeMatches, message: '' };
    }
    if (routeMatches.length > 1) {
      return {
        name: '',
        ambiguous: true,
        matches: routeMatches,
        message: '你可能要去：' + routeMatches.join('、') + '。请选择一个具体目的地。'
      };
    }
  }

  return {
    name: fuzzyMatches[0] || '',
    ambiguous: false,
    matches: fuzzyMatches[0] ? [fuzzyMatches[0]] : [],
    message: ''
  };
}

function matchDepartment(text, routes) {
  const matched = matchDepartmentDetailed(text, routes);
  return matched.name || null;
}

function matchFloor(text) {
  const normalized = normalizeText(text);
  const zhNumbers = {
    一: 1,
    二: 2,
    三: 3,
    四: 4,
    五: 5,
    六: 6,
    七: 7,
    八: 8,
    九: 9,
    十: 10
  };
  const digitMatch = normalized.match(/(\d{1,2})楼/);
  if (digitMatch) return digitMatch[1] + '楼';

  const zhMatch = normalized.match(/([一二三四五六七八九十]{1,3})楼/);
  if (!zhMatch) return '';

  const value = zhMatch[1];
  if (value === '十') return '10楼';
  if (value.indexOf('十') === 0) return (10 + (zhNumbers[value[1]] || 0)) + '楼';
  if (value.indexOf('十') === value.length - 1) return ((zhNumbers[value[0]] || 1) * 10) + '楼';
  return (zhNumbers[value] || '') + '楼';
}

function getFloorDepartments(floor, routes) {
  return getDepartmentNames(routes).filter(name => {
    const route = routes.getDepartmentRoute(name);
    return route && route.floor === floor;
  });
}

function shouldSetDestination(text) {
  const normalized = normalizeText(text);
  return /去|到|导航|带我|我要|我想|想|需要|目的地|前往|缴费|交费|挂号|取药|拿药|领药|检查|抽血|拍片|彩超|超声|看病|看诊|发烧|高烧|胸痛|腹痛|很急|急救|抢救|牙疼|牙痛/.test(normalized);
}

function isInfoOnlyQuestion(text) {
  const normalized = normalizeText(text);
  return (
    /在几楼|几楼|在哪儿|在哪里|在哪|有哪些|有什么|都有/.test(normalized) ||
    /(?:是|主要|能|可以)?(?:做|干)(?:什么|哪些|哪类|啥|嘛)(?:的|检查|治疗|业务)?/.test(normalized) ||
    /(?:主要)?(?:负责|管|提供)(?:什么|哪些|哪类)/.test(normalized) ||
    /看(?:什么|哪些|哪类)(?:病|疾病)?/.test(normalized) ||
    /(?:功能|作用|职责|业务范围|诊疗范围)(?:是|有)(?:什么|哪些)/.test(normalized) ||
    /的(?:功能|作用|职责|业务范围|诊疗范围)$/.test(normalized) ||
    /(?:介绍|了解)(?:一下)?/.test(normalized) ||
    /是什么科/.test(normalized)
  );
}

function buildFloorReply(name, routes) {
  const route = routes.getDepartmentRoute(name);
  if (!route) return '';

  const arrivalName = routes.getArrivalName ? routes.getArrivalName(name) : name;
  const suffix = arrivalName !== name ? '，进入病区后导航到' + arrivalName : '';
  return name + '在' + route.floor + suffix + '。';
}

function handleMessage(message, routes) {
  const text = normalizeText(message);
  if (!text) {
    return {
      reply: '可以输入“内一科在几楼”或“我要去妇科门诊”。',
      action: 'none',
      destinationName: '',
      matches: []
    };
  }

  const pharmacyMatches = getGenericPharmacyMatches(text, routes);
  if (pharmacyMatches.length > 1) {
    return {
      reply: '请选择具体药房：' + pharmacyMatches.join('、') + '。',
      action: 'chooseDepartment',
      matches: pharmacyMatches,
      destinationName: '',
      departmentName: ''
    };
  }

  const floor = matchFloor(text);
  if (floor && /哪些|有什么|都有|科室/.test(text)) {
    const departments = getFloorDepartments(floor, routes);
    return {
      reply: departments.length
        ? floor + '有：' + departments.join('、') + '。'
        : '暂未配置' + floor + '的科室数据。',
      action: 'answerFloorList',
      floor,
      matches: departments,
      destinationName: ''
    };
  }

  const departmentMatch = matchDepartmentDetailed(text, routes);
  if (departmentMatch.ambiguous) {
    return {
      reply: departmentMatch.message || ('匹配到多个科室：' + departmentMatch.matches.join('、') + '。请选择具体科室。'),
      action: 'chooseDepartment',
      matches: departmentMatch.matches,
      destinationName: '',
      departmentName: ''
    };
  }

  const departmentName = departmentMatch.name;
  if (departmentName) {
    const reply = buildFloorReply(departmentName, routes);
    if (isInfoOnlyQuestion(text)) {
      return {
        reply,
        action: 'answerDepartment',
        destinationName: '',
        departmentName,
        matches: [departmentName]
      };
    }

    const shouldFillDestination = shouldSetDestination(text) || !isInfoOnlyQuestion(text);
    return {
      reply: shouldFillDestination
        ? reply + '已帮你填入目的地。'
        : reply,
      action: shouldFillDestination ? 'setDestination' : 'answerDepartment',
      destinationName: shouldFillDestination ? departmentName : '',
      departmentName,
      matches: [departmentName]
    };
  }

  return {
    reply: '我还没匹配到具体科室。可以说症状或事项，比如“孩子发烧”“我要缴费”“我想抽血”“牙疼去哪里”。',
    action: 'none',
    destinationName: '',
    matches: []
  };
}

module.exports = {
  normalizeText,
  normalizeDepartmentName,
  matchDepartment,
  matchDepartmentDetailed,
  matchFloor,
  isInfoOnlyQuestion,
  handleMessage
};
