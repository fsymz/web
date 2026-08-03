const routeMath = require('./routeMath.js');

function noop() {}

function createCallbacks(callbacks) {
  const source = callbacks || {};
  return {
    onLegChange: source.onLegChange || noop,
    onFrame: source.onFrame || noop,
    onStats: source.onStats || noop,
    onLegComplete: source.onLegComplete || noop,
    onFinish: source.onFinish || noop,
    onStateChange: source.onStateChange || noop,
    onError: source.onError || noop
  };
}

class SimNavEngine {
  constructor(options) {
    const config = options || {};
    this.callbacks = createCallbacks(config.callbacks);
    this.frameIntervalMs = config.frameIntervalMs || 50;
    this.statsIntervalMs = config.statsIntervalMs || 500;
    this.autoSwitchDelayMs = config.autoSwitchDelayMs || 900;
    this.speedPercentPerSecond = config.speedPercentPerSecond || 9;
    this.distanceMetersPerPercent = config.distanceMetersPerPercent || 1.2;
    this.minDurationMs = config.minDurationMs || 5000;
    this.maxDurationMs = config.maxDurationMs || 22000;
    this.loop = !!config.loop;

    this.legs = [];
    this.currentLegIndex = 0;
    this.currentLeg = null;
    this.metrics = null;
    this.durationMs = 0;
    this.timer = null;
    this.switchTimer = null;
    this.startedAt = 0;
    this.elapsedBeforePause = 0;
    this.lastStatsAt = 0;
    this.status = 'idle';
    this.transitionToken = 0;
  }

  configure(options) {
    const config = options || {};
    if (config.callbacks) this.callbacks = createCallbacks(config.callbacks);
    if (config.speedPercentPerSecond) this.speedPercentPerSecond = config.speedPercentPerSecond;
    if (config.distanceMetersPerPercent) this.distanceMetersPerPercent = config.distanceMetersPerPercent;
    if (config.frameIntervalMs) this.frameIntervalMs = config.frameIntervalMs;
    if (config.statsIntervalMs) this.statsIntervalMs = config.statsIntervalMs;
    if (Object.prototype.hasOwnProperty.call(config, 'loop')) {
      this.loop = !!config.loop;
    }
  }

  start(legs, options) {
    this.stop({ keepStatus: true });
    this.configure(options);
    this.legs = Array.isArray(legs) ? legs.slice() : [];

    if (!this.legs.length) {
      this.status = 'idle';
      this.callbacks.onError({ message: '缺少导航路段' });
      return false;
    }

    const startIndex = options && Number.isFinite(Number(options.startIndex))
      ? Number(options.startIndex)
      : 0;

    return this.startLeg(startIndex);
  }

  startLeg(index) {
    this.transitionToken += 1;
    const transitionToken = this.transitionToken;
    this.clearTimers();
    const safeIndex = Math.max(0, Math.min(index, this.legs.length - 1));
    const leg = this.legs[safeIndex];
    const metrics = routeMath.buildPathMetrics(leg && leg.points, leg && leg.imageSize);

    if (!metrics) {
      this.status = 'idle';
      this.callbacks.onError({
        message: '当前路线缺少有效轨迹点',
        leg,
        legIndex: safeIndex
      });
      return false;
    }

    this.currentLegIndex = safeIndex;
    this.currentLeg = leg;
    this.metrics = metrics;
    this.durationMs = routeMath.getDurationMs(
      metrics.total,
      this.speedPercentPerSecond,
      this.minDurationMs,
      this.maxDurationMs
    );
    this.startedAt = Date.now();
    this.elapsedBeforePause = 0;
    this.lastStatsAt = 0;
    this.status = 'running';

    const ownsStart = () => (
      transitionToken === this.transitionToken
      && this.status === 'running'
      && this.currentLegIndex === safeIndex
      && this.currentLeg === leg
      && this.metrics === metrics
    );

    this.callbacks.onLegChange(this.buildLegPayload(0));
    if (!ownsStart()) return true;

    this.emitFrame(0);
    if (!ownsStart()) return true;

    this.emitStats(0, true);
    if (!ownsStart()) return true;

    this.callbacks.onStateChange(this.getState());
    if (!ownsStart()) return true;

    this.timer = setInterval(() => {
      this.tick();
    }, this.frameIntervalMs);

    return true;
  }

  tick() {
    if (this.status !== 'running' || !this.metrics) return;

    const elapsed = this.getElapsedMs();
    const ratio = Math.min(1, elapsed / this.durationMs);

    if (ratio >= 1) {
      this.finishCurrentLeg();
      return;
    }

    this.emitFrame(ratio);

    const now = Date.now();
    if (now - this.lastStatsAt >= this.statsIntervalMs) {
      this.emitStats(ratio, true);
      this.lastStatsAt = now;
    }
  }

  pause() {
    if (this.status !== 'running') return false;

    this.elapsedBeforePause = this.getElapsedMs();
    this.clearTimer();
    this.status = 'paused';
    this.callbacks.onStateChange(this.getState());
    return true;
  }

  resume() {
    if (this.status !== 'paused') return false;

    this.startedAt = Date.now();
    this.status = 'running';
    this.callbacks.onStateChange(this.getState());

    this.timer = setInterval(() => {
      this.tick();
    }, this.frameIntervalMs);

    return true;
  }

  replayCurrentLeg() {
    if (!this.legs.length) return false;
    return this.startLeg(this.currentLegIndex);
  }

  restart() {
    if (!this.legs.length) return false;
    return this.startLeg(0);
  }

  next() {
    if (!this.legs.length) return false;
    const nextIndex = this.currentLegIndex + 1;
    if (nextIndex >= this.legs.length) return false;
    return this.startLeg(nextIndex);
  }

  stop(options) {
    this.transitionToken += 1;
    this.clearTimers();
    this.metrics = null;
    this.currentLeg = null;
    this.elapsedBeforePause = 0;
    if (!(options && options.keepStatus)) {
      this.status = 'idle';
      this.callbacks.onStateChange(this.getState());
    }
  }

  destroy() {
    this.stop();
    this.legs = [];
  }

  finishCurrentLeg() {
    this.clearTimer();
    const transitionToken = this.transitionToken;
    const completedLegIndex = this.currentLegIndex;
    const payload = this.buildLegPayload(1);
    const hasNext = completedLegIndex < this.legs.length - 1;
    const willLoop = !hasNext && this.loop;
    const nextIndex = hasNext ? completedLegIndex + 1 : 0;

    this.status = 'switching';
    this.emitFrame(1);
    this.emitStats(1, true);

    this.callbacks.onLegComplete(Object.assign({}, payload, {
      hasNext: hasNext || willLoop,
      isLoopRestart: willLoop
    }));

    if (transitionToken !== this.transitionToken || this.status !== 'switching') return;

    this.callbacks.onStateChange(this.getState());

    if (transitionToken !== this.transitionToken || this.status !== 'switching') return;

    if (hasNext || willLoop) {
      this.switchTimer = setTimeout(() => {
        if (transitionToken !== this.transitionToken || this.status !== 'switching') return;
        this.startLeg(nextIndex);
      }, this.autoSwitchDelayMs);
      return;
    }

    this.status = 'finished';
    this.callbacks.onFinish(payload);
    this.callbacks.onStateChange(this.getState());
  }

  emitFrame(ratio) {
    const marker = routeMath.roundMarker(routeMath.getPointAtRatio(this.metrics, ratio));
    this.callbacks.onFrame(Object.assign({}, marker, {
      ratio,
      legIndex: this.currentLegIndex
    }));
  }

  emitStats(ratio) {
    this.callbacks.onStats(this.buildStats(ratio));
  }

  buildLegPayload(ratio) {
    return Object.assign({
      leg: this.currentLeg,
      legIndex: this.currentLegIndex,
      totalLegs: this.legs.length
    }, this.buildStats(ratio));
  }

  buildStats(ratio) {
    const safeRatio = routeMath.clamp(ratio, 0, 1);
    const totalPercentDistance = this.metrics ? this.metrics.total : 0;
    const remainingPercentDistance = totalPercentDistance * (1 - safeRatio);
    const remainingMs = this.durationMs * (1 - safeRatio);
    const totalMeters = routeMath.percentDistanceToMeters(
      totalPercentDistance,
      this.distanceMetersPerPercent
    );
    const remainingMeters = routeMath.percentDistanceToMeters(
      remainingPercentDistance,
      this.distanceMetersPerPercent
    );

    return {
      ratio: safeRatio,
      progress: Math.round(safeRatio * 100),
      totalDistanceText: routeMath.formatDistance(totalMeters),
      remainingDistanceText: routeMath.formatDistance(remainingMeters),
      etaText: routeMath.formatTime(remainingMs)
    };
  }

  getElapsedMs() {
    if (this.status === 'paused') return this.elapsedBeforePause;
    return this.elapsedBeforePause + (Date.now() - this.startedAt);
  }

  getState() {
    return {
      status: this.status,
      isRunning: this.status === 'running',
      isPaused: this.status === 'paused',
      isFinished: this.status === 'finished',
      currentLegIndex: this.currentLegIndex
    };
  }

  clearTimer() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  clearSwitchTimer() {
    if (this.switchTimer) {
      clearTimeout(this.switchTimer);
      this.switchTimer = null;
    }
  }

  clearTimers() {
    this.clearTimer();
    this.clearSwitchTimer();
  }
}

module.exports = {
  SimNavEngine
};
