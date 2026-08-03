const routes = require('../../data/routes.js');
const navigationPolicy = require('../../data/navigationPolicy.js');
const routeMath = require('../../utils/routeMath.js');
const navAgent = require('../../utils/navAgent.js');
const { SimNavEngine } = require('../../utils/simNavEngine.js');

const WELCOME_PROMPT = '我是医院导航系统，请问你要去哪里？';

Page({
  data: {
    inputVal1: '',
    imageUrl1: '',
    showImage1: false,
    showSuggestions1: false,
    suggestions1: [],

    inputVal2: '',
    imageUrl2: '',
    showImage2: false,
    showSuggestions2: false,
    suggestions2: [],
    currentPreviewLeg: null,
    previewLegIndex: 0,
    previewLegCount: 0,
    previewMessage: '',

    departments: routes.getDepartmentNames(),

    currentLeg: null,
    navigationLegCount: 0,
    currentImageIndex: 0,
    showNavigationPopup: false,

    navStepTitle: '',
    navInstruction: '',
    navFloor: '',
    navDistanceText: '--',
    navEtaText: '--',
    navProgress: 0,
    navLineSegments: [],

    markerVisible: false,
    markerX: 0,
    markerY: 0,
    markerAngle: 0,

    isSimulating: false,
    isPaused: false,
    stepMode: false,
    stepAnimating: false,
    isAudioPlaying: false,
    simSpeedPercentPerSecond: 9,
    loopSpeedPercentPerSecond: 12,
    stepWalkMetersPerSecond: 1.1,
    distanceMetersPerPercent: 1.2,
    missingRoutePath: false,

    showAgentPanel: false,
    navMode: 'step',
    agentInput: '',
    agentMessages: [
      {
        role: 'assistant',
        text: WELCOME_PROMPT + '可以说“我要去缴费”“孩子发烧”“牙疼去哪里”。'
      }
    ],
    recordState: 'idle',
    recordPermissionDenied: false,
    voiceMode: '',
    voicePartialText: '',
    voiceDrainActive: false,
    voiceRecognitionTainted: false,
    voiceTip: '语音输入目的地',
    speechStatusText: ''
  },

  simNavEngine: null,
  currentPlan: null,
  previewPlanLegs: [],
  speechAudioContext: null,
  voiceManager: null,
  wechatSIPlugin: null,
  voiceSessionSeq: 0,
  currentVoiceSession: null,
  voiceManagerTainted: false,
  stepNavLegIndex: 0,
  stepNavPointIndex: 0,
  stepAnimationTimer: null,
  lastSpokenStepKey: '',
  speechQueue: [],
  speechQueueIndex: 0,
  speechQueueSource: '',
  speechPlaybackToken: 0,
  activeSpeechPlayback: null,
  speechSynthesisTimer: null,
  speechPlaybackTimer: null,
  welcomePromptSpoken: false,
  welcomePromptTimer: null,
  currentNavigationPrompt: null,
  pendingTransferLegIndex: null,
  pageIsActive: false,
  resourcesDestroyed: false,

  onLoad: function() {
    this.pageIsActive = true;
    this.resourcesDestroyed = false;
    this.speechAudioContext = wx.createInnerAudioContext();
    this.createSimNavEngine();
    this.welcomePromptTimer = setTimeout(() => {
      this.welcomePromptTimer = null;
      if (!this.pageIsActive || this.resourcesDestroyed) return;
      this.playWelcomePrompt();
    }, 500);
  },

  onShow: function() {
    if (!this.resourcesDestroyed) this.pageIsActive = true;
  },

  onHide: function() {
    this.cleanupPageResources('hide');
  },

  onUnload: function() {
    if (this.resourcesDestroyed) return;
    this.cleanupPageResources('unload');
    this.destroySimNavEngine();
    if (this.speechAudioContext) {
      this.speechAudioContext.destroy();
      this.speechAudioContext = null;
    }
    this.voiceManager = null;
    this.wechatSIPlugin = null;
    this.resourcesDestroyed = true;
  },

  cleanupPageResources: function(reason) {
    this.pageIsActive = false;
    if (this.welcomePromptTimer) {
      clearTimeout(this.welcomePromptTimer);
      this.welcomePromptTimer = null;
    }
    if (reason === 'unload') {
      this.abandonVoiceRecognitionForUnload();
    } else {
      this.stopVoiceRecognition({ discardResult: true, reason: reason || 'cleanup' });
    }
    this.clearStepAnimation();
    this.stopAudio();
    if (this.simNavEngine) this.simNavEngine.stop();
  },

  createSimNavEngine: function() {
    if (this.simNavEngine) return this.simNavEngine;

    this.simNavEngine = new SimNavEngine({
      speedPercentPerSecond: this.data.simSpeedPercentPerSecond,
      distanceMetersPerPercent: this.data.distanceMetersPerPercent,
      callbacks: {
        onLegChange: this.handleNavLegChange.bind(this),
        onFrame: this.handleNavFrame.bind(this),
        onStats: this.handleNavStats.bind(this),
        onLegComplete: this.handleNavLegComplete.bind(this),
        onFinish: this.handleNavFinish.bind(this),
        onStateChange: this.handleNavStateChange.bind(this),
        onError: this.handleNavError.bind(this)
      }
    });

    return this.simNavEngine;
  },

  destroySimNavEngine: function() {
    if (this.simNavEngine) {
      this.simNavEngine.destroy();
      this.simNavEngine = null;
    }
  },

  onInput1: function(e) {
    const value = e.detail.value.trim();
    this.setData({ inputVal1: value }, () => {
      this.filterSuggestions(value, 1);
      this.updateRoutePreview();
    });
  },

  onInput2: function(e) {
    const value = e.detail.value.trim();
    this.setData({ inputVal2: value }, () => {
      this.filterSuggestions(value, 2);
      this.updateRoutePreview();
    });
  },

  onFocus1: function() {
    this.filterSuggestions(this.data.inputVal1.trim(), 1);
  },

  onFocus2: function() {
    this.filterSuggestions(this.data.inputVal2.trim(), 2);
  },

  filterSuggestions: function(value, index) {
    const suggestions = value
      ? routes.matchDepartments(value)
      : this.data.departments.slice(0, 12);

    this.setData({
      ['showSuggestions' + index]: suggestions.length > 0,
      ['suggestions' + index]: suggestions
    });
  },

  selectSuggestion1: function(e) {
    const selectedSuggestion = e.currentTarget.dataset.name;
    this.setData({
      inputVal1: selectedSuggestion,
      showSuggestions1: false
    }, () => this.updateRoutePreview());
    this.onSearch1();
  },

  selectSuggestion2: function(e) {
    const selectedSuggestion = e.currentTarget.dataset.name;
    this.setData({
      inputVal2: selectedSuggestion,
      showSuggestions2: false
    }, () => this.updateRoutePreview());
    this.onSearch2();
  },

  setDestinationValue: function(destinationName) {
    this.setData({
      inputVal2: destinationName,
      showSuggestions2: false,
      suggestions2: []
    }, () => {
      this.onSearch2();
      this.updateRoutePreview();
    });
  },

  onSearch1: function() {
    const inputVal = this.data.inputVal1.trim();
    const route = routes.getResolvedDepartmentRoute
      ? routes.getResolvedDepartmentRoute(inputVal)
      : routes.getDepartmentRoute(inputVal);
    const imageUrl = route ? route.fromElevator.image : '';

    this.setData({
      imageUrl1: imageUrl,
      showImage1: Boolean(imageUrl)
    }, () => this.updateRoutePreview());

    if (!imageUrl && inputVal) {
      wx.showToast({ title: '未找到当前位置图片', icon: 'none' });
    }
  },

  onSearch2: function() {
    const inputVal = this.data.inputVal2.trim();
    const route = routes.getResolvedDepartmentRoute
      ? routes.getResolvedDepartmentRoute(inputVal)
      : routes.getDepartmentRoute(inputVal);
    const imageUrl = route ? route.toDestination.image : '';

    this.setData({
      imageUrl2: imageUrl,
      showImage2: Boolean(imageUrl)
    }, () => this.updateRoutePreview());

    if (!imageUrl && inputVal) {
      wx.showToast({ title: '未找到目的地图片', icon: 'none' });
    }
  },

  openAgentAssistant: function() {
    this.setData({ showAgentPanel: true });
  },

  closeAgentAssistant: function() {
    this.stopVoiceRecognition({ discardResult: true, reason: 'close-agent-panel' });
    this.setData({ showAgentPanel: false });
  },

  setNavigationMode: function(e) {
    const mode = e && e.currentTarget && e.currentTarget.dataset.mode === 'loop' ? 'loop' : 'step';
    if (mode === this.data.navMode) return;

    this.stopVoiceRecognition({ discardResult: true, reason: 'switch-mode' });
    this.setData({ navMode: mode }, () => {
      if (!this.data.showNavigationPopup || !this.currentPlan) return;

      if (mode === 'loop') {
        this.startLoopNavigation(0);
        return;
      }

      this.clearStepAnimation();
      this.stopAudio();
      if (this.simNavEngine) this.simNavEngine.stop();
      this.startStepLeg(this.data.currentImageIndex || 0);
    });
  },

  onAgentInput: function(e) {
    this.setData({ agentInput: e.detail.value });
  },

  appendAgentMessage: function(role, text) {
    const messages = this.data.agentMessages.concat([{ role, text }]);
    this.setData({ agentMessages: messages });
  },

  sendAgentMessage: function() {
    const message = this.data.agentInput.trim();
    if (!message) return;

    this.setData({ showAgentPanel: true });
    this.appendAgentMessage('user', message);
    const result = navAgent.handleMessage(message, routes);
    this.appendAgentMessage('assistant', result.reply);
    this.speakAssistantReply(result.reply);
    this.setData({ agentInput: '' });

    if (result.destinationName) {
      this.setDestinationValue(result.destinationName);
    } else if (result.matches && result.matches.length) {
      this.setData({
        suggestions2: result.matches,
        showSuggestions2: true
      });
    }
  },

  getWechatSIPlugin: function() {
    if (this.wechatSIPlugin) return this.wechatSIPlugin;

    try {
      if (typeof requirePlugin !== 'function') {
        throw new Error('WechatSI plugin loader is unavailable');
      }
      const plugin = requirePlugin('WechatSI');
      if (!plugin) throw new Error('WechatSI plugin is unavailable');
      this.wechatSIPlugin = plugin;
      return plugin;
    } catch (error) {
      this.wechatSIPlugin = null;
      this.setData({ voiceTip: '语音服务暂不可用，请查看屏幕文字，可继续手动导航' });
      return null;
    }
  },

  ownsVoiceSession: function(session) {
    return Boolean(
      session
      && this.currentVoiceSession
      && this.currentVoiceSession === session
      && this.currentVoiceSession.id === session.id
      && this.voiceSessionSeq === session.id
      && !session.abandoned
    );
  },

  isCurrentVoiceSession: function(session) {
    return Boolean(
      this.ownsVoiceSession(session)
      && session.finalPolicy === 'apply'
      && !this.voiceManagerTainted
      && this.pageIsActive
      && !this.resourcesDestroyed
    );
  },

  clearVoiceSessionTerminalTimer: function(session) {
    if (!session || !session.terminalTimer) return;
    clearTimeout(session.terminalTimer);
    session.terminalTimer = null;
  },

  detachVoiceManagerCallbacks: function(session) {
    const manager = session && session.manager;
    const handlers = session && session.handlers;
    if (!manager || !handlers) return;
    for (const name of ['onStart', 'onStop', 'onError', 'onRecognize']) {
      if (manager[name] === handlers[name]) manager[name] = null;
    }
  },

  releaseVoiceSession: function(session, options) {
    if (!this.ownsVoiceSession(session)) return false;
    const settings = options || {};
    this.clearVoiceSessionTerminalTimer(session);
    session.terminalHandled = true;
    this.currentVoiceSession = null;
    this.voiceSessionSeq += 1;
    this.detachVoiceManagerCallbacks(session);
    this.setData({
      recordState: 'idle',
      voiceMode: '',
      voicePartialText: '',
      voiceDrainActive: false,
      voiceRecognitionTainted: this.voiceManagerTainted,
      voiceTip: settings.voiceTip || '语音输入目的地'
    });
    return true;
  },

  taintVoiceRecognition: function(session) {
    if (!this.ownsVoiceSession(session)) return;
    session.finalPolicy = 'discard';
    this.voiceManagerTainted = true;
    this.releaseVoiceSession(session, {
      voiceTip: '语音识别暂不可用，请继续手动输入目的地'
    });
  },

  armVoiceSessionTerminalTimer: function(session) {
    this.clearVoiceSessionTerminalTimer(session);
    // WechatSI exposes onStop/onError as the single terminal boundary for one
    // manager start; a missing boundary makes that manager unsafe to reuse.
    session.terminalTimer = setTimeout(() => {
      session.terminalTimer = null;
      if (!this.ownsVoiceSession(session) || !session.awaitingTerminal) return;
      this.taintVoiceRecognition(session);
    }, 5000);
  },

  bindVoiceManagerSession: function(manager, session) {
    const handlers = {};
    session.manager = manager;
    session.handlers = handlers;

    handlers.onStart = () => {
      if (!this.isCurrentVoiceSession(session) || this.data.recordState !== 'starting') return;
      const isAgentVoice = session.mode === 'agent';
      this.setData({
        recordState: 'recording',
        voiceMode: session.mode,
        voiceTip: isAgentVoice ? '正在听，请说导诊问题' : '正在听，请说目的地'
      });
    };
    handlers.onRecognize = (result) => {
      if (!this.isCurrentVoiceSession(session) || session.awaitingTerminal || this.data.recordState !== 'recording') return;
      this.setData({
        voicePartialText: String(result && result.result ? result.result : '').trim()
      });
    };
    handlers.onStop = (res) => {
      if (!this.ownsVoiceSession(session)) return;
      if (!this.isCurrentVoiceSession(session)) {
        this.releaseVoiceSession(session, {
          voiceTip: session.releaseTip || '录音已停止，请再次点击语音'
        });
        return;
      }
      const text = String(res && res.result ? res.result : '').trim();
      const mode = session.mode;
      if (!this.releaseVoiceSession(session, { voiceTip: '语音输入目的地' })) return;
      if (mode === 'agent') {
        this.applyAgentVoiceText(text);
        return;
      }
      this.applyVoiceText(text);
    };
    handlers.onError = () => {
      if (!this.ownsVoiceSession(session)) return;
      if (!this.isCurrentVoiceSession(session)) {
        this.releaseVoiceSession(session, {
          voiceTip: session.releaseTip || '录音已停止，请再次点击语音'
        });
        return;
      }
      if (!this.releaseVoiceSession(session, { voiceTip: '语音识别失败，请重试' })) return;
      wx.showToast({ title: '语音识别失败，请重试', icon: 'none' });
    };

    manager.onStart = handlers.onStart;
    manager.onStop = handlers.onStop;
    manager.onError = handlers.onError;
    manager.onRecognize = handlers.onRecognize;
  },

  startRecorderForSession: function(session) {
    if (!this.isCurrentVoiceSession(session) || this.voiceManagerTainted) return;

    const plugin = this.getWechatSIPlugin();
    if (!plugin || typeof plugin.getRecordRecognitionManager !== 'function') {
      if (!this.isCurrentVoiceSession(session)) return;
      this.currentVoiceSession = null;
      this.voiceSessionSeq += 1;
      this.setData({
        recordState: 'idle',
        voiceMode: '',
        voiceTip: '语音识别暂不可用，请继续手动输入目的地'
      });
      return;
    }

    try {
      const manager = this.voiceManager || plugin.getRecordRecognitionManager();
      if (!manager || typeof manager.start !== 'function') {
        throw new Error('WechatSI recorder is unavailable');
      }
      this.voiceManager = manager;
      this.bindVoiceManagerSession(manager, session);
      session.managerStarted = true;
      manager.start({
        duration: 30000,
        lang: 'zh_CN'
      });
    } catch (error) {
      session.managerStarted = false;
      if (!this.isCurrentVoiceSession(session)) return;
      this.releaseVoiceSession(session, {
        voiceTip: '语音识别启动失败，请重试或手动输入'
      });
      wx.showToast({ title: '语音识别启动失败，请重试', icon: 'none' });
    }
  },

  requestRecordPermission: function(session) {
    if (!this.isCurrentVoiceSession(session)) return;
    if (!wx.authorize) {
      this.startRecorderForSession(session);
      return;
    }

    try {
      wx.authorize({
        scope: 'scope.record',
        success: () => {
          if (!this.isCurrentVoiceSession(session)) return;
          this.setData({ recordPermissionDenied: false });
          this.startRecorderForSession(session);
        },
        fail: () => {
          if (!this.isCurrentVoiceSession(session)) return;
          this.currentVoiceSession = null;
          this.voiceSessionSeq += 1;
          this.setData({
            recordState: 'idle',
            recordPermissionDenied: true,
            voiceMode: '',
            voiceTip: '请开启麦克风权限后再次点击语音'
          });
          wx.showToast({ title: '请开启麦克风权限', icon: 'none' });
        }
      });
    } catch (error) {
      if (!this.isCurrentVoiceSession(session)) return;
      this.currentVoiceSession = null;
      this.voiceSessionSeq += 1;
      this.setData({
        recordState: 'idle',
        recordPermissionDenied: true,
        voiceMode: '',
        voiceTip: '请开启麦克风权限后再次点击语音'
      });
    }
  },

  startVoiceRecognition: function(mode) {
    const voiceMode = mode === 'agent' ? 'agent' : 'destination';
    const activeSession = this.currentVoiceSession;

    if (
      this.voiceManagerTainted
      || this.data.voiceRecognitionTainted
      || this.data.voiceDrainActive
      || this.data.recordState === 'starting'
      || this.data.recordState === 'stopping'
    ) return;

    if (activeSession) {
      if (activeSession.mode === voiceMode && this.data.recordState === 'recording') {
        this.stopVoiceRecognition({ discardResult: false, reason: 'user-stop' });
        return;
      }
      return;
    }

    if (!this.pageIsActive || this.resourcesDestroyed || this.data.recordState !== 'idle') return;
    const session = {
      id: this.voiceSessionSeq + 1,
      mode: voiceMode,
      finalPolicy: 'apply',
      managerStarted: false,
      stopRequested: false,
      awaitingTerminal: false,
      terminalHandled: false,
      terminalTimer: null,
      manager: null,
      handlers: null,
      abandoned: false
    };
    this.voiceSessionSeq = session.id;
    this.currentVoiceSession = session;
    this.setData({
      recordState: 'starting',
      recordPermissionDenied: false,
      voiceMode,
      voicePartialText: '',
      voiceDrainActive: false,
      voiceTip: voiceMode === 'agent' ? '正在准备导诊语音…' : '正在准备目的地语音…'
    });
    this.requestRecordPermission(session);
  },

  stopVoiceRecognition: function(options) {
    const settings = options || {};
    const session = this.currentVoiceSession;
    if (!session) {
      if (this.data.recordState !== 'idle' || this.data.voiceMode) {
        this.setData({ recordState: 'idle', voiceMode: '' });
      }
      return;
    }

    const discardResult = Boolean(settings.discardResult);
    if (discardResult) {
      session.finalPolicy = 'discard';
      session.releaseTip = settings.reason === 'switch-mode'
        ? '录音模式已切换，请再次点击语音'
        : '录音已停止，请再次点击语音';
    }

    if (!session.managerStarted) {
      session.finalPolicy = 'discard';
      this.releaseVoiceSession(session, {
        voiceTip: session.releaseTip || '语音输入目的地'
      });
      return;
    }

    this.beginVoiceSessionDrain(session);
  },

  beginVoiceSessionDrain: function(session) {
    if (!this.ownsVoiceSession(session)) return;
    if (session.awaitingTerminal) return;
    session.awaitingTerminal = true;
    this.setData({
      recordState: 'stopping',
      voicePartialText: '',
      voiceDrainActive: true,
      voiceTip: session.finalPolicy === 'discard'
        ? (session.releaseTip || '录音已停止，请再次点击语音')
        : '正在停止录音…'
    });

    this.armVoiceSessionTerminalTimer(session);
    if (session.stopRequested) return;
    session.stopRequested = true;
    const manager = session.manager || this.voiceManager;
    if (!manager || typeof manager.stop !== 'function') {
      this.taintVoiceRecognition(session);
      return;
    }
    try {
      manager.stop();
    } catch (error) {
      this.taintVoiceRecognition(session);
    }
  },

  abandonVoiceRecognitionForUnload: function() {
    const session = this.currentVoiceSession;
    if (!session) return;
    session.finalPolicy = 'discard';
    session.releaseTip = '语音输入目的地';

    if (session.managerStarted && !session.stopRequested) {
      session.stopRequested = true;
      const manager = session.manager || this.voiceManager;
      if (manager && typeof manager.stop === 'function') {
        try {
          manager.stop();
        } catch (error) {
          // Unload abandons the page instance even if the native stop fails.
        }
      }
    }

    if (!this.ownsVoiceSession(session)) return;
    this.clearVoiceSessionTerminalTimer(session);
    this.currentVoiceSession = null;
    this.voiceSessionSeq += 1;
    session.abandoned = true;
    this.detachVoiceManagerCallbacks(session);
    this.setData({
      recordState: 'idle',
      voiceMode: '',
      voicePartialText: '',
      voiceDrainActive: false
    });
  },

  openRecordPermissionSettings: function() {
    if (!wx.openSetting) return;
    wx.openSetting({
      success: (result) => {
        if (!this.pageIsActive || this.resourcesDestroyed) return;
        const granted = Boolean(result && result.authSetting && result.authSetting['scope.record']);
        this.setData({
          recordPermissionDenied: !granted,
          voiceTip: granted
            ? '麦克风权限已开启，请再次点击语音'
            : '请开启麦克风权限后再次点击语音'
        });
      },
      fail: () => {
        if (!this.pageIsActive || this.resourcesDestroyed) return;
        this.setData({
          recordPermissionDenied: true,
          voiceTip: '未能打开设置，请在系统设置中开启麦克风权限'
        });
      }
    });
  },

  toggleVoiceInput: function() {
    this.startVoiceRecognition('destination');
  },

  toggleAgentVoiceInput: function() {
    this.startVoiceRecognition('agent');
  },

  splitSpeechText: function(text, maxChars = 50) {
    const normalized = String(text == null ? '' : text).trim();
    if (!normalized) return [];

    const limit = Math.max(1, Math.floor(Number(maxChars) || 50));
    const chunks = [];
    const preferredBreak = /[。！？；，、,.!?;]/;
    let buffer = '';

    for (const character of normalized) {
      if (buffer && buffer.length + character.length > limit) {
        chunks.push(buffer);
        buffer = '';
      }
      buffer += character;
      if (buffer.length >= 20 && preferredBreak.test(character)) {
        chunks.push(buffer);
        buffer = '';
      }
    }
    if (buffer) chunks.push(buffer);
    return chunks.filter(chunk => chunk.length > 0 && chunk.length <= limit);
  },

  speakText: function(text, { key = '', source = 'general', force = false } = {}) {
    const chunks = this.splitSpeechText(text, 50);
    if (!chunks.length) return { ok: false, reason: 'empty' };
    if (key && key === this.lastSpokenStepKey && !force) {
      return { ok: false, reason: 'duplicate' };
    }

    this.stopSpeechPlayback();
    const token = this.speechPlaybackToken;
    this.speechQueue = chunks;
    this.speechQueueIndex = 0;
    this.speechQueueSource = source;
    if (key) this.lastSpokenStepKey = key;
    this.setData({ speechStatusText: '正在准备语音播报' });
    this.synthesizeCurrentSpeechChunk(token, 0);
    return { ok: true, token };
  },

  isCurrentSpeechChunk: function(token, chunkIndex) {
    return Boolean(
      token === this.speechPlaybackToken
      && chunkIndex === this.speechQueueIndex
      && typeof this.speechQueue[chunkIndex] === 'string'
      && this.speechQueue[chunkIndex].length > 0
      && this.pageIsActive
      && !this.resourcesDestroyed
    );
  },

  clearSpeechSynthesisTimer: function() {
    if (this.speechSynthesisTimer !== null) {
      clearTimeout(this.speechSynthesisTimer);
      this.speechSynthesisTimer = null;
    }
  },

  clearSpeechPlaybackTimer: function() {
    if (this.speechPlaybackTimer !== null) {
      clearTimeout(this.speechPlaybackTimer);
      this.speechPlaybackTimer = null;
    }
  },

  clearActiveSpeechPlayback: function() {
    const active = this.activeSpeechPlayback;
    const context = this.speechAudioContext;
    if (active && context) {
      if (typeof context.offEnded === 'function') context.offEnded(active.endedHandler);
      if (typeof context.offError === 'function') context.offError(active.errorHandler);
    }
    this.activeSpeechPlayback = null;
  },

  finishSpeechQueue: function(options = {}) {
    this.clearSpeechSynthesisTimer();
    this.clearSpeechPlaybackTimer();
    this.clearActiveSpeechPlayback();
    if (options.stopPlayback && this.speechAudioContext) {
      try {
        this.speechAudioContext.stop();
      } catch (error) {
        // Cleanup is still complete when the native player is already gone.
      }
    }
    this.speechQueue = [];
    this.speechQueueIndex = 0;
    this.speechQueueSource = '';
    const update = { speechStatusText: options.message || '' };
    if (this.data.isAudioPlaying) update.isAudioPlaying = false;
    if (options.message) {
      update.voiceTip = options.message;
    }
    this.setData(update);
  },

  synthesizeCurrentSpeechChunk: function(token, chunkIndex) {
    if (!this.isCurrentSpeechChunk(token, chunkIndex)) return;
    const plugin = this.getWechatSIPlugin();
    if (!plugin || typeof plugin.textToSpeech !== 'function') {
      this.finishSpeechQueue({
        stopPlayback: true,
        message: '语音服务暂不可用，请查看屏幕文字'
      });
      return;
    }

    const chunk = this.speechQueue[chunkIndex];
    this.clearSpeechSynthesisTimer();
    this.speechSynthesisTimer = setTimeout(() => {
      if (!this.isCurrentSpeechChunk(token, chunkIndex)) return;
      this.finishSpeechQueue({
        stopPlayback: true,
        message: '语音合成超时，语音服务暂不可用，请查看屏幕文字'
      });
    }, 15000);

    try {
      plugin.textToSpeech({
        lang: 'zh_CN',
        content: chunk,
        success: (result) => {
          if (!this.isCurrentSpeechChunk(token, chunkIndex)) return;
          this.clearSpeechSynthesisTimer();
          if (
            !result
            || result.retcode !== 0
            || typeof result.filename !== 'string'
            || !result.filename.trim()
          ) {
            this.finishSpeechQueue({
              stopPlayback: true,
              message: '语音服务暂不可用，请查看屏幕文字'
            });
            return;
          }
          this.playSpeechChunk(result.filename.trim(), token, chunkIndex);
        },
        fail: () => {
          if (!this.isCurrentSpeechChunk(token, chunkIndex)) return;
          this.clearSpeechSynthesisTimer();
          this.finishSpeechQueue({
            stopPlayback: true,
            message: '网络语音暂不可用，请查看屏幕文字'
          });
        }
      });
    } catch (error) {
      if (!this.isCurrentSpeechChunk(token, chunkIndex)) return;
      this.finishSpeechQueue({
        stopPlayback: true,
        message: '语音服务暂不可用，请查看屏幕文字'
      });
    }
  },

  playSpeechChunk: function(filename, token, chunkIndex) {
    if (!this.isCurrentSpeechChunk(token, chunkIndex)) return;
    const context = this.speechAudioContext;
    if (!context) {
      this.finishSpeechQueue({
        stopPlayback: true,
        message: '语音服务暂不可用，请查看屏幕文字'
      });
      return;
    }

    const endedHandler = () => this.handleSpeechAudioEnded(token, chunkIndex);
    const errorHandler = () => this.handleSpeechAudioError(token, chunkIndex);
    this.activeSpeechPlayback = { token, chunkIndex, endedHandler, errorHandler };

    try {
      context.onEnded(endedHandler);
      context.onError(errorHandler);
      context.src = filename;
      context.play();
    } catch (error) {
      this.finishSpeechQueue({
        stopPlayback: true,
        message: '语音服务暂不可用，请查看屏幕文字'
      });
      return;
    }

    this.clearSpeechPlaybackTimer();
    this.speechPlaybackTimer = setTimeout(() => {
      const active = this.activeSpeechPlayback;
      if (!active || active.token !== token || active.chunkIndex !== chunkIndex) return;
      this.finishSpeechQueue({
        stopPlayback: true,
        message: '语音播放超时，语音服务暂不可用，请查看屏幕文字'
      });
    }, 60000);
    this.setData({ isAudioPlaying: true, speechStatusText: '正在语音播报' });
  },

  handleSpeechAudioEnded: function(token, chunkIndex) {
    const active = this.activeSpeechPlayback;
    if (!active || active.token !== token || active.chunkIndex !== chunkIndex) return;
    this.clearSpeechPlaybackTimer();
    this.clearActiveSpeechPlayback();
    this.speechQueueIndex = chunkIndex + 1;
    if (this.speechQueueIndex >= this.speechQueue.length) {
      this.finishSpeechQueue();
      return;
    }
    if (this.data.isAudioPlaying) this.setData({ isAudioPlaying: false });
    this.synthesizeCurrentSpeechChunk(token, this.speechQueueIndex);
  },

  handleSpeechAudioError: function(token, chunkIndex) {
    const active = this.activeSpeechPlayback;
    if (!active || active.token !== token || active.chunkIndex !== chunkIndex) return;
    this.finishSpeechQueue({
      stopPlayback: true,
      message: '语音播放失败，请查看屏幕文字'
    });
  },

  playWelcomePrompt: function() {
    if (this.welcomePromptSpoken) return { ok: false, reason: 'duplicate' };
    this.welcomePromptSpoken = true;
    return this.speakText(WELCOME_PROMPT, {
      key: 'welcome',
      source: 'welcome'
    });
  },

  speakAssistantReply: function(replyText) {
    return this.speakText(replyText, { source: 'assistant' });
  },

  applyVoiceText: function(text) {
    if (!text) {
      wx.showToast({ title: '没有识别到内容', icon: 'none' });
      return;
    }

    const destinationName = navAgent.matchDepartment(text, routes);
    if (!destinationName) {
      this.setData({ inputVal2: text, showAgentPanel: true });
      this.filterSuggestions(text, 2);
      wx.showToast({ title: '请从候选科室中确认', icon: 'none' });
      return;
    }

    this.setData({ showAgentPanel: true });
    this.appendAgentMessage('user', '语音：' + text);
    const reply = routes.getArrivalName(destinationName) + '已填入目的地。';
    this.appendAgentMessage('assistant', reply);
    this.speakAssistantReply(reply);
    this.setDestinationValue(destinationName);
  },

  applyAgentVoiceText: function(text) {
    if (!text) {
      wx.showToast({ title: '没有识别到导诊问题', icon: 'none' });
      return;
    }

    this.setData({ showAgentPanel: true });
    this.appendAgentMessage('user', '语音：' + text);
    const result = navAgent.handleMessage(text, routes);
    this.appendAgentMessage('assistant', result.reply);
    this.speakAssistantReply(result.reply);

    if (result.destinationName) {
      this.setDestinationValue(result.destinationName);
    } else if (result.matches && result.matches.length) {
      this.setData({
        suggestions2: result.matches,
        showSuggestions2: true
      });
    }
  },

  updateRoutePreview: function() {
    const location = this.data.inputVal1.trim();
    const destination = this.data.inputVal2.trim();

    if (!location || !destination) {
      this.clearRoutePreview();
      this.setData({ previewMessage: '' });
      return;
    }

    const plan = routes.createNavigationPlan(location, destination);
    if (!plan.ok) {
      this.clearRoutePreview();
      this.setData({ previewMessage: plan.message || '未找到完整路线，请检查科室名称' });
      return;
    }
    if (plan.status === 'coLocated') {
      this.clearRoutePreview();
      this.setData({ previewMessage: plan.message || '当前位置与目的地位于同一区域，请根据现场标识确认' });
      return;
    }

    this.previewPlanLegs = plan.legs.map(leg => ({
      title: leg.title,
      image: leg.image,
      floor: leg.floor,
      lineSegments: routeMath.buildLineSegments(leg.points, leg.imageSize)
    }));
    this.setData({ previewMessage: '' });
    this.setPreviewLeg(0);
  },

  setPreviewLeg: function(index) {
    const legs = Array.isArray(this.previewPlanLegs) ? this.previewPlanLegs : [];
    if (!legs.length) {
      this.setData({ currentPreviewLeg: null, previewLegIndex: 0, previewLegCount: 0 });
      return;
    }
    const safeIndex = Math.max(0, Math.min(Number(index) || 0, legs.length - 1));
    this.setData({
      currentPreviewLeg: legs[safeIndex],
      previewLegIndex: safeIndex,
      previewLegCount: legs.length
    });
  },

  showPreviousPreviewLeg: function() {
    this.setPreviewLeg(this.data.previewLegIndex - 1);
  },

  showNextPreviewLeg: function() {
    this.setPreviewLeg(this.data.previewLegIndex + 1);
  },

  clearRoutePreview: function() {
    this.previewPlanLegs = [];
    this.setData({ currentPreviewLeg: null, previewLegIndex: 0, previewLegCount: 0 });
  },

  startNavigation: function() {
    const location = this.data.inputVal1.trim();
    const destination = this.data.inputVal2.trim();

    if (!location || !destination) {
      wx.showToast({ title: '请先填写当前位置和目的地', icon: 'none' });
      return;
    }

    const plan = routes.createNavigationPlan(location, destination);
    if (!plan.ok) {
      wx.showToast({ title: plan.message, icon: 'none' });
      return;
    }
    if (plan.status === 'coLocated') {
      const message = plan.message || '当前位置与目的地位于同一区域，请根据现场标识确认';
      if (this.data.showNavigationPopup) this.navigationClose();
      this.currentPlan = null;
      this.clearRoutePreview();
      this.setData({
        previewMessage: message,
        currentLeg: null,
        navigationLegCount: 0,
        showNavigationPopup: false
      });
      wx.showToast({ title: message, icon: 'none' });
      return;
    }

    this.currentPlan = plan;
    this.stopAudio();

    this.setData({
      currentLeg: plan.legs[0],
      navigationLegCount: plan.legs.length,
      currentImageIndex: 0,
      showNavigationPopup: true,
      navStepTitle: '',
      navInstruction: '',
      navFloor: '',
      navDistanceText: '--',
      navEtaText: '--',
      navProgress: 0,
      navLineSegments: [],
      markerVisible: false,
      markerX: 0,
      markerY: 0,
      markerAngle: 0,
      isSimulating: false,
      isPaused: false,
      stepMode: false,
      stepAnimating: false,
      missingRoutePath: false
    }, () => {
      this.stepNavLegIndex = 0;
      this.stepNavPointIndex = 0;
      this.clearStepAnimation();
      if (this.data.navMode === 'loop') {
        this.startLoopNavigation(0);
      } else {
        this.startStepLeg(0);
      }
    });
  },

  startLoopNavigation: function(startIndex) {
    if (!this.currentPlan || !this.currentPlan.legs || !this.currentPlan.legs.length) return;
    const engine = this.createSimNavEngine();
    const safeIndex = Math.max(0, Math.min(Number(startIndex) || 0, this.currentPlan.legs.length - 1));

    this.clearStepAnimation();
    this.stopAudio();
    this.stepNavLegIndex = safeIndex;
    this.stepNavPointIndex = 0;
    this.setData({
      stepMode: false,
      stepAnimating: false,
      isPaused: false
    });

    engine.start(this.currentPlan.legs, {
      startIndex: safeIndex,
      loop: true,
      speedPercentPerSecond: this.data.loopSpeedPercentPerSecond,
      distanceMetersPerPercent: this.data.distanceMetersPerPercent
    });
  },

  handleNavLegChange: function(payload) {
    const leg = payload.leg || {};
    const legIndex = Math.max(0, Number(payload.legIndex) || 0);
    const entryInstruction = leg.instruction || leg.title || '开始本段导航';
    const entryKey = 'leg-' + legIndex + ':entry';
    const continuesTransferSpeech = this.pendingTransferLegIndex === legIndex;

    this.setData({
      currentLeg: leg,
      currentImageIndex: legIndex,
      navStepTitle: leg.title || '',
      navInstruction: leg.instruction || '',
      navFloor: leg.floor || '楼层待补充',
      navDistanceText: payload.remainingDistanceText,
      navEtaText: payload.etaText,
      navProgress: payload.progress,
      navLineSegments: routeMath.buildLineSegments(leg.points, leg.imageSize),
      markerVisible: true,
      isSimulating: true,
      isPaused: false,
      stepMode: false,
      missingRoutePath: !leg.hasRoutePath
    });

    if (continuesTransferSpeech) {
      this.pendingTransferLegIndex = null;
      this.currentNavigationPrompt = { text: entryInstruction, key: entryKey };
      return;
    }
    this.speakNavigationPrompt(entryInstruction, entryKey);
  },

  handleNavFrame: function(marker) {
    this.setData({
      markerX: marker.x,
      markerY: marker.y,
      markerAngle: marker.angle
    });
  },

  handleNavStats: function(stats) {
    this.setData({
      navProgress: stats.progress,
      navDistanceText: stats.remainingDistanceText,
      navEtaText: stats.etaText
    });
  },

  handleNavLegComplete: function(payload) {
    if (!payload.hasNext) return;

    const leg = payload.leg || {};
    const nextLeg = this.currentPlan && this.currentPlan.legs
      ? this.currentPlan.legs[(payload.legIndex || 0) + 1]
      : null;
    const transferInstruction = leg.transferInstruction
      || ('已到达电梯，请乘坐电梯前往' + ((nextLeg && nextLeg.floor) || leg.transferFloor || '目的地楼层'));
    const navInstruction = payload.isLoopRestart
      ? '已完成一轮，正在重新从起点开始'
      : transferInstruction;
    const nextLegIndex = (Math.max(0, Number(payload.legIndex) || 0)) + 1;
    const nextLegInstruction = nextLeg && nextLeg.instruction ? nextLeg.instruction : '';
    const spokenTransferInstruction = nextLegInstruction
      ? transferInstruction.replace(/[。！？!?]+$/, '') + '。' + nextLegInstruction
      : transferInstruction;

    this.setData({
      isSimulating: false,
      isPaused: false,
      navProgress: 100,
      navDistanceText: '0m',
      navEtaText: '0秒',
      navInstruction: payload.isLoopRestart ? navInstruction : spokenTransferInstruction
    });

    if (payload.isLoopRestart) {
      this.pendingTransferLegIndex = null;
      this.stopSpeechPlayback();
    } else {
      this.pendingTransferLegIndex = nextLegIndex;
      this.speakNavigationPrompt(
        spokenTransferInstruction,
        'leg-' + (Math.max(0, Number(payload.legIndex) || 0)) + ':transfer-' + nextLegIndex
      );
    }
  },

  handleNavFinish: function() {
    this.pendingTransferLegIndex = null;
    this.setData({
      isSimulating: false,
      isPaused: false,
      navProgress: 100,
      navDistanceText: '0m',
      navEtaText: '0秒',
      navInstruction: '已到达目的地'
    });
    return this.speakNavigationPrompt('已到达目的地', 'arrival');
  },

  handleNavStateChange: function(state) {
    if (!this.data.showNavigationPopup && state.status !== 'idle') return;
    this.setData({
      isSimulating: state.isRunning,
      isPaused: state.isPaused
    });
  },

  handleNavError: function(error) {
    wx.showToast({
      title: error.message || '模拟导航启动失败',
      icon: 'none'
    });
  },

  pauseNavigation: function() {
    const prompt = this.currentNavigationPrompt || {
      text: this.data.navInstruction
        || (this.simNavEngine && this.simNavEngine.currentLeg && this.simNavEngine.currentLeg.instruction)
        || '',
      key: this.lastSpokenStepKey || ('leg-' + (this.data.currentImageIndex || 0) + ':entry')
    };
    if (!prompt.text) {
      return { ok: false, reason: 'empty' };
    }
    this.currentNavigationPrompt = prompt;
    if (!this.data.stepMode && (!this.simNavEngine || !this.simNavEngine.pause())) {
      return { ok: false, reason: 'not-paused' };
    }
    this.stopSpeechPlayback();
    return { ok: true };
  },

  resumeNavigation: function() {
    const prompt = this.currentNavigationPrompt;
    if (!prompt || !prompt.text) return { ok: false, reason: 'empty' };
    if (!this.data.stepMode && (!this.simNavEngine || !this.simNavEngine.resume())) {
      return { ok: false, reason: 'not-resumed' };
    }
    return this.speakNavigationPrompt(prompt.text, prompt.key, { force: true });
  },

  replayNavigation: function() {
    const prompt = this.currentNavigationPrompt;
    if (!prompt || !prompt.text) return { ok: false, reason: 'empty' };
    return this.speakNavigationPrompt(prompt.text, prompt.key, { force: true });
  },

  replayCurrentStep: function() {
    const prompt = this.currentNavigationPrompt;
    if (!prompt || !prompt.text) return { ok: false, reason: 'empty' };
    return this.speakNavigationPrompt(prompt.text, prompt.key, { force: true });
  },

  navigationNext: function() {
    this.stepNavigation();
  },

  getStepMarkerAngle: function(points, pointIndex, imageSize) {
    const normalizedPoints = routeMath.normalizePoints(points || []);
    const safeIndex = Math.max(0, Math.min(pointIndex, normalizedPoints.length - 1));
    const from = normalizedPoints[Math.max(0, safeIndex - 1)];
    const to = normalizedPoints[safeIndex] || from;
    if (!from || !to) return 0;
    return routeMath.getSegmentAngle(from, to, imageSize);
  },

  getSemanticStepPath: function(leg) {
    const routePath = leg && leg.routePath;
    return routeMath.buildSpokenStepPath(
      (leg && leg.points) || [],
      routePath && routePath.semanticPointIndexes,
      {
        imageSize: leg && leg.imageSize,
        distanceMetersPerPercent: this.data.distanceMetersPerPercent,
        minimumSpokenStepMeters: navigationPolicy.minimumSpokenStepMeters,
        distanceRoundingMeters: navigationPolicy.distanceRoundingMeters
      }
    );
  },

  applyStepState: function(legIndex, pointIndex, prompt) {
    const leg = this.currentPlan.legs[legIndex] || {};
    const normalizedPoints = routeMath.normalizePoints(leg.points || []);
    const safeIndex = Math.max(0, Math.min(pointIndex, normalizedPoints.length - 1));
    const point = normalizedPoints[safeIndex] || prompt.point || { x: 0, y: 0 };
    const progress = this.getStepProgress(leg.points, safeIndex);
    const remainingDistance = this.getRemainingDistanceFromPoint(
      leg.points,
      safeIndex,
      leg.imageSize
    );

    this.setData({
      currentLeg: leg,
      currentImageIndex: legIndex,
      navStepTitle: leg.title || '',
      navInstruction: prompt.text,
      navFloor: leg.floor || '楼层待补充',
      navDistanceText: routeMath.formatDistance(routeMath.percentDistanceToMeters(remainingDistance, this.data.distanceMetersPerPercent)),
      navEtaText: '--',
      navProgress: progress,
      navLineSegments: routeMath.buildLineSegments(leg.points, leg.imageSize),
      markerVisible: true,
      markerX: Number(point.x.toFixed(3)),
      markerY: Number(point.y.toFixed(3)),
      markerAngle: this.getStepMarkerAngle(leg.points, safeIndex, leg.imageSize),
      isSimulating: false,
      isPaused: true,
      stepMode: true,
      stepAnimating: false,
      missingRoutePath: !leg.hasRoutePath
    });
  },

  speakNavigationPrompt: function(text, stepKey, options = {}) {
    if (!text) return { ok: false, reason: 'empty' };
    const key = stepKey || text;
    this.currentNavigationPrompt = { text, key };
    this.setData({ voiceTip: '语音播报：' + text });
    return this.speakText(text, {
      key,
      source: 'navigation',
      force: options.force === true
    });
  },

  getStepProgress: function(points, pointIndex) {
    const normalizedPoints = routeMath.normalizePoints(points || []);
    if (normalizedPoints.length < 2) return 0;
    const safeIndex = Math.max(0, Math.min(pointIndex, normalizedPoints.length - 1));
    return Math.round(safeIndex / (normalizedPoints.length - 1) * 100);
  },

  getRemainingDistanceFromPoint: function(points, pointIndex, imageSize) {
    const normalizedPoints = routeMath.normalizePoints(points || []);
    let remaining = 0;
    for (let index = Math.max(0, pointIndex); index < normalizedPoints.length - 1; index += 1) {
      remaining += routeMath.getSegmentLength(
        normalizedPoints[index],
        normalizedPoints[index + 1],
        imageSize
      );
    }
    return remaining;
  },

  getStepSegmentDuration: function(segmentLength) {
    const meters = routeMath.percentDistanceToMeters(segmentLength, this.data.distanceMetersPerPercent);
    const speed = Math.max(0.45, Number(this.data.stepWalkMetersPerSecond) || 1.1);
    const duration = meters / speed * 1000;
    return Math.max(900, Math.min(12000, duration));
  },

  clearStepAnimation: function() {
    if (this.stepAnimationTimer) {
      clearInterval(this.stepAnimationTimer);
      this.stepAnimationTimer = null;
    }
  },

  startStepLeg: function(legIndex) {
    if (!this.currentPlan || !this.currentPlan.legs.length) return;
    this.clearStepAnimation();
    if (this.simNavEngine) this.simNavEngine.stop();

    const safeLegIndex = Math.max(0, Math.min(legIndex, this.currentPlan.legs.length - 1));
    const leg = this.currentPlan.legs[safeLegIndex];
    const stepPath = this.getSemanticStepPath(leg);
    const prompt = routeMath.buildSemanticStepInstruction(
      leg.points,
      stepPath.rawPointIndexes,
      0,
      {
      arrivalName: leg.arrivalName,
      hasNextLeg: safeLegIndex < this.currentPlan.legs.length - 1,
      transferFloor: leg.transferFloor || ((this.currentPlan.legs[safeLegIndex + 1] || {}).floor),
      transferInstruction: leg.transferInstruction,
      sourceSemanticPointIndexes: leg.routePath && leg.routePath.semanticPointIndexes,
      distanceMetersPerPercent: this.data.distanceMetersPerPercent,
      distanceRoundingMeters: navigationPolicy.distanceRoundingMeters,
      imageSize: leg.imageSize
      }
    );

    this.stepNavLegIndex = safeLegIndex;
    this.stepNavPointIndex = 0;
    this.applyStepState(safeLegIndex, 0, prompt);
    this.speakNavigationPrompt(prompt.text, 'leg-' + safeLegIndex + ':step-0');
  },

  completeStepSegment: function(legIndex, toIndex, prompt) {
    const leg = this.currentPlan.legs[legIndex] || {};
    this.stepNavLegIndex = legIndex;
    this.stepNavPointIndex = toIndex;
    this.applyStepState(legIndex, toIndex, prompt);

    if (
      !prompt.isArrival
      || legIndex >= this.currentPlan.legs.length - 1
    ) return;

    const nextLeg = this.currentPlan.legs[legIndex + 1] || {};
    const transferInstruction = leg.transferInstruction
      || ('已到达电梯，请乘坐电梯前往'
        + (leg.transferFloor || nextLeg.floor || '目的地楼层'));
    this.setData({ navInstruction: transferInstruction });
    this.speakNavigationPrompt(
      transferInstruction,
      'leg-' + legIndex + ':transfer-arrival'
    );
  },

  animateStepSegment: function(legIndex, fromIndex, toIndex, prompt) {
    const leg = this.currentPlan.legs[legIndex] || {};
    const metrics = routeMath.buildPathMetrics(
      (leg.points || []).slice(fromIndex, toIndex + 1),
      leg.imageSize
    );
    if (!metrics) {
      this.completeStepSegment(legIndex, toIndex, prompt);
      return;
    }

    this.clearStepAnimation();
    this.stopAudio();
    if (this.simNavEngine) this.simNavEngine.pause();

    const duration = this.getStepSegmentDuration(metrics.total);
    const startedAt = Date.now();

    this.setData({
      currentLeg: leg,
      currentImageIndex: legIndex,
      navStepTitle: leg.title || '',
      navInstruction: prompt.text,
      navFloor: leg.floor || '楼层待补充',
      navEtaText: routeMath.formatTime(duration),
      navLineSegments: routeMath.buildLineSegments(leg.points, leg.imageSize),
      markerVisible: true,
      isSimulating: false,
      isPaused: true,
      stepMode: true,
      stepAnimating: true,
      missingRoutePath: !leg.hasRoutePath
    });
    this.speakNavigationPrompt(prompt.text, 'leg-' + legIndex + ':step-' + toIndex);

    this.stepAnimationTimer = setInterval(() => {
      const ratio = Math.max(0, Math.min(1, (Date.now() - startedAt) / duration));
      const marker = routeMath.getPointAtRatio(metrics, ratio);
      this.setData({
        markerX: Number(marker.x.toFixed(3)),
        markerY: Number(marker.y.toFixed(3)),
        markerAngle: Number(marker.angle.toFixed(1))
      });

      if (ratio < 1) return;

      this.clearStepAnimation();
      this.completeStepSegment(legIndex, toIndex, prompt);
    }, 50);
  },

  stepNavigation: function() {
    if (!this.currentPlan || !this.currentPlan.legs || !this.currentPlan.legs.length) {
      this.startNavigation();
      return;
    }

    if (this.data.stepAnimating) return;

    let legIndex = this.stepNavLegIndex || 0;
    let pointIndex = this.stepNavPointIndex || 0;

    if (!this.data.stepMode) {
      this.startStepLeg(this.data.currentImageIndex || 0);
      return;
    }

    const legs = this.currentPlan.legs;
    let leg = legs[legIndex];
    if (!leg) return;

    const stepPath = this.getSemanticStepPath(leg);
    if (stepPath.points.length < 2) {
      wx.showToast({ title: '当前路线缺少轨迹点', icon: 'none' });
      return;
    }

    const semanticPointIndex = stepPath.rawPointIndexes.indexOf(pointIndex);
    if (semanticPointIndex < 0) return;

    if (this.data.stepMode && semanticPointIndex >= stepPath.points.length - 1) {
      if (legIndex < legs.length - 1) {
        this.startStepLeg(legIndex + 1);
        return;
      } else {
        this.stepNavLegIndex = legIndex;
        this.stepNavPointIndex = pointIndex;
        this.handleNavFinish();
        return;
      }
    }

    const nextSemanticPointIndex = semanticPointIndex + 1;
    const nextPointIndex = stepPath.rawPointIndexes[nextSemanticPointIndex];
    const prompt = routeMath.buildSemanticStepInstruction(
      leg.points,
      stepPath.rawPointIndexes,
      nextSemanticPointIndex,
      {
      arrivalName: leg.arrivalName,
      hasNextLeg: legIndex < legs.length - 1,
      transferFloor: leg.transferFloor || ((legs[legIndex + 1] || {}).floor),
      transferInstruction: leg.transferInstruction,
      sourceSemanticPointIndexes: leg.routePath && leg.routePath.semanticPointIndexes,
      distanceMetersPerPercent: this.data.distanceMetersPerPercent,
      distanceRoundingMeters: navigationPolicy.distanceRoundingMeters,
      imageSize: leg.imageSize
      }
    );

    this.stepNavLegIndex = legIndex;
    this.stepNavPointIndex = pointIndex;
    this.animateStepSegment(legIndex, pointIndex, nextPointIndex, prompt);
  },

  navigationClose: function() {
    if (this.simNavEngine) {
      this.simNavEngine.stop();
    }
    this.clearStepAnimation();
    this.stopAudio();
    this.setData({
      showNavigationPopup: false,
      markerVisible: false,
      navLineSegments: [],
      navProgress: 0,
      isSimulating: false,
      isPaused: false,
      stepMode: false,
      stepAnimating: false
    });
    this.stepNavLegIndex = 0;
    this.stepNavPointIndex = 0;
  },

  stopSpeechPlayback: function() {
    this.speechPlaybackToken += 1;
    this.finishSpeechQueue({ stopPlayback: true });
  },

  stopAudio: function() {
    this.stopSpeechPlayback();
    this.lastSpokenStepKey = '';
    if (this.data.isAudioPlaying) {
      this.setData({ isAudioPlaying: false });
    }
  },

  previewImage: function() {
    const src = this.data.currentPreviewLeg && this.data.currentPreviewLeg.image;
    if (!src) return;

    wx.previewImage({
      current: src,
      urls: [src]
    });
  },

  previewNavigationImage: function() {
    const current = this.data.currentLeg && this.data.currentLeg.image;
    if (!current) return;

    wx.previewImage({
      current,
      urls: [current]
    });
  },

  stopPropagation: function() {}
});
