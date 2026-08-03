const { loadPage } = require('./load-page.js');

function createAudioContextFake(name, options = {}) {
  const endedHandlers = [];
  const errorHandlers = [];
  const endedRegistrations = [];
  const errorRegistrations = [];
  let src = '';

  const audio = {
    name,
    playCalls: [],
    pauseCalls: 0,
    stopCalls: 0,
    destroyCalls: 0,
    onEnded(handler) {
      endedHandlers.push(handler);
      endedRegistrations.push(handler);
    },
    offEnded(handler) {
      if (!handler) {
        endedHandlers.splice(0);
        return;
      }
      const index = endedHandlers.indexOf(handler);
      if (index >= 0) endedHandlers.splice(index, 1);
    },
    onError(handler) {
      errorHandlers.push(handler);
      errorRegistrations.push(handler);
    },
    offError(handler) {
      if (!handler) {
        errorHandlers.splice(0);
        return;
      }
      const index = errorHandlers.indexOf(handler);
      if (index >= 0) errorHandlers.splice(index, 1);
    },
    play() {
      if (options.audioPlayThrows) throw options.audioPlayThrows;
      this.playCalls.push(src);
    },
    pause() {
      this.pauseCalls += 1;
    },
    stop() {
      this.stopCalls += 1;
    },
    destroy() {
      this.destroyCalls += 1;
    },
    emitEnded(payload) {
      endedHandlers.slice().forEach(handler => handler(payload));
    },
    emitEndedFromRegistration(index, payload) {
      endedRegistrations[index]?.(payload);
    },
    emitError(payload) {
      errorHandlers.slice().forEach(handler => handler(payload || new Error('audio failed')));
    },
    emitErrorFromRegistration(index, payload) {
      errorRegistrations[index]?.(payload || new Error('audio failed'));
    }
  };

  Object.defineProperties(audio, {
    src: {
      enumerable: true,
      get() {
        return src;
      },
      set(value) {
        if (options.audioSrcThrows) throw options.audioSrcThrows;
        src = value;
      }
    },
    activeEndedHandlerCount: {
      enumerable: true,
      get() {
        return endedHandlers.length;
      }
    },
    activeErrorHandlerCount: {
      enumerable: true,
      get() {
        return errorHandlers.length;
      }
    }
  });

  return audio;
}

function createRecorderFake(options = {}) {
  const sessionCallbacks = [];
  const manager = {
    onStart: null,
    onStop: null,
    onError: null,
    onRecognize: null,
    startCalls: [],
    stopCalls: 0,
    start(options) {
      this.startCalls.push(options);
      sessionCallbacks.push({
        onStart: this.onStart,
        onStop: this.onStop,
        onError: this.onError,
        onRecognize: this.onRecognize
      });
    },
    stop() {
      this.stopCalls += 1;
      if (options.recorderStopThrows) throw options.recorderStopThrows;
    },
    emit(type, payload = {}) {
      this[type]?.(payload);
    },
    emitStart(payload = {}) {
      this.emit('onStart', payload);
    },
    emitStop(payload = {}) {
      this.emit('onStop', payload);
    },
    emitError(payload = {}) {
      this.emit('onError', payload);
    },
    emitRecognize(payload = {}) {
      this.emit('onRecognize', payload);
    },
    getSessionCallbacks(sessionIndex) {
      return sessionCallbacks[sessionIndex];
    }
  };
  return manager;
}

function installFakeTimers() {
  const originals = {
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
    setInterval: globalThis.setInterval,
    clearInterval: globalThis.clearInterval
  };
  const timeouts = new Map();
  const intervals = new Map();
  const clearedTimeouts = [];
  const clearedIntervals = [];
  let nextId = 1;

  globalThis.setTimeout = (callback, delay) => {
    const id = nextId++;
    timeouts.set(id, { callback, delay });
    return id;
  };
  globalThis.clearTimeout = id => {
    clearedTimeouts.push(id);
    timeouts.delete(id);
  };
  globalThis.setInterval = (callback, delay) => {
    const id = nextId++;
    intervals.set(id, { callback, delay });
    return id;
  };
  globalThis.clearInterval = id => {
    clearedIntervals.push(id);
    intervals.delete(id);
  };

  return {
    timeouts,
    intervals,
    clearedTimeouts,
    clearedIntervals,
    runTimeout(id) {
      const timer = timeouts.get(id);
      if (!timer) return false;
      timeouts.delete(id);
      timer.callback();
      return true;
    },
    restore() {
      globalThis.setTimeout = originals.setTimeout;
      globalThis.clearTimeout = originals.clearTimeout;
      globalThis.setInterval = originals.setInterval;
      globalThis.clearInterval = originals.clearInterval;
    }
  };
}

function cloneData(data) {
  return JSON.parse(JSON.stringify(data));
}

function createNavigationPageHarness(harnessOptions = {}) {
  const timers = installFakeTimers();
  const recorder = createRecorderFake(harnessOptions);
  const speechAudio = createAudioContextFake('speech', harnessOptions);
  const authorizeCalls = [];
  const openSettingCalls = [];
  const toastCalls = [];
  const previewImageCalls = [];
  const requirePluginCalls = [];
  const textToSpeechCalls = [];
  let getRecorderManagerCalls = 0;
  let audioContextCreateCalls = 0;

  const plugin = harnessOptions.plugin || {
    getRecordRecognitionManager() {
      getRecorderManagerCalls += 1;
      return recorder;
    },
    textToSpeech(options) {
      if (harnessOptions.textToSpeechThrows) {
        throw harnessOptions.textToSpeechThrows;
      }
      textToSpeechCalls.push(options);
    }
  };

  const requirePlugin = name => {
    requirePluginCalls.push(name);
    if (name !== 'WechatSI') throw new Error(`unexpected plugin: ${name}`);
    return harnessOptions.pluginUnavailable ? null : plugin;
  };

  const wx = {
    createInnerAudioContext() {
      audioContextCreateCalls += 1;
      return speechAudio;
    },
    authorize(request) {
      authorizeCalls.push(request);
    },
    openSetting(request) {
      openSettingCalls.push(request);
    },
    showToast(request) {
      toastCalls.push(request);
    },
    previewImage(request) {
      previewImageCalls.push(request);
    }
  };

  const loaded = loadPage({ wx, requirePlugin });
  const page = Object.assign({}, loaded.definition);
  page.data = cloneData(loaded.definition.data);
  page.speechAudioContext = null;
  page.voiceManager = null;
  page.wechatSIPlugin = null;
  page.currentVoiceSession = null;
  page.voiceSessionSeq = 0;
  page.voiceManagerTainted = false;
  page.stepAnimationTimer = null;
  page.welcomePromptTimer = null;
  page.speechQueue = [];
  page.speechQueueIndex = 0;
  page.speechQueueSource = '';
  page.speechPlaybackToken = 0;
  page.activeSpeechPlayback = null;
  page.speechSynthesisTimer = null;
  page.speechPlaybackTimer = null;
  page.welcomePromptSpoken = false;
  page.resourcesDestroyed = false;
  page.setData = function(update, callback) {
    Object.assign(this.data, update);
    if (callback) callback();
  };

  if (harnessOptions.autoLoad !== false) {
    page.onLoad();
  }

  let restored = false;
  return {
    page,
    recorder,
    speechAudio,
    plugin,
    textToSpeechCalls,
    requirePluginCalls,
    authorizeCalls,
    openSettingCalls,
    toastCalls,
    previewImageCalls,
    timers,
    get audioContextCreateCalls() {
      return audioContextCreateCalls;
    },
    get getRecorderManagerCalls() {
      return getRecorderManagerCalls;
    },
    restore() {
      if (restored) return;
      restored = true;
      loaded.restore();
      timers.restore();
    }
  };
}

module.exports = {
  createAudioContextFake,
  createNavigationPageHarness,
  createRecorderFake
};
