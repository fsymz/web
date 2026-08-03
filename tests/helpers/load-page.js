const path = require('node:path');

const pagePath = path.resolve(
  __dirname,
  '..',
  '..',
  'miniprogram',
  'pages',
  'navigation',
  'navigation.js'
);

function rememberGlobal(name) {
  return {
    existed: Object.prototype.hasOwnProperty.call(globalThis, name),
    value: globalThis[name]
  };
}

function restoreGlobal(name, previous) {
  if (previous.existed) {
    globalThis[name] = previous.value;
    return;
  }

  delete globalThis[name];
}

function loadPage(options = {}) {
  const previous = {
    Page: rememberGlobal('Page'),
    wx: rememberGlobal('wx'),
    getApp: rememberGlobal('getApp'),
    requirePlugin: rememberGlobal('requirePlugin')
  };
  let definition;
  let restored = false;

  function restore() {
    if (restored) return;
    restored = true;
    restoreGlobal('Page', previous.Page);
    restoreGlobal('wx', previous.wx);
    restoreGlobal('getApp', previous.getApp);
    restoreGlobal('requirePlugin', previous.requirePlugin);
  }

  globalThis.Page = pageDefinition => {
    definition = pageDefinition;
  };
  globalThis.wx = options.wx || {};
  globalThis.getApp = () => ({});
  globalThis.requirePlugin = options.requirePlugin || (() => ({}));

  try {
    delete require.cache[require.resolve(pagePath)];
    require(pagePath);
  } catch (error) {
    restore();
    throw error;
  }

  if (!definition) {
    restore();
    throw new Error('Navigation page did not register a Page definition');
  }

  return { definition, restore };
}

module.exports = { loadPage };
