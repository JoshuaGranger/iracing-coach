(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.iracingCoachReplayPhaseClock = api;
})(typeof globalThis !== "undefined" ? globalThis : window, function () {
  "use strict";

  const owners = new Map();
  const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
  const finite = value => typeof value === "number" && Number.isFinite(value);

  function phaseAt(initialFraction, speed, durationSeconds, elapsedMilliseconds, revision, frame) {
    if (!finite(initialFraction) || !finite(speed) || !finite(durationSeconds) || durationSeconds <= 0 || !finite(elapsedMilliseconds)) {
      throw new TypeError("Replay phase clock inputs must be finite and duration must be positive.");
    }
    const fraction = clamp(initialFraction + Math.max(0, elapsedMilliseconds) / 1000 * speed / durationSeconds, 0, 1);
    return Object.freeze({
      revision,
      frame,
      elapsedMilliseconds: Math.max(0, elapsedMilliseconds),
      fraction,
      completed: fraction >= 1
    });
  }

  function stop(ownerId, revision) {
    const state = owners.get(ownerId);
    if (!state || (revision !== undefined && state.revision !== revision)) return false;
    state.cancelled = true;
    if (state.animationFrame) cancelAnimationFrame(state.animationFrame);
    owners.delete(ownerId);
    return true;
  }

  function start(ownerId, callback, revision, initialFraction, speed, durationSeconds) {
    if (!ownerId || !callback || typeof callback.invokeMethodAsync !== "function") {
      throw new TypeError("Replay phase clock needs an owner and a .NET callback.");
    }
    stop(ownerId);
    const state = {
      ownerId,
      callback,
      revision,
      initialFraction,
      speed,
      durationSeconds,
      startedAt: null,
      frame: 0,
      animationFrame: 0,
      cancelled: false
    };
    owners.set(ownerId, state);

    const tick = timestamp => {
      if (state.cancelled || owners.get(ownerId) !== state) return;
      if (state.startedAt === null) state.startedAt = timestamp;
      const phase = phaseAt(
        state.initialFraction,
        state.speed,
        state.durationSeconds,
        timestamp - state.startedAt,
        state.revision,
        state.frame++);
      state.callback.invokeMethodAsync(
        "PublishReplayPhase",
        phase.revision,
        phase.frame,
        phase.elapsedMilliseconds,
        phase.fraction,
        phase.completed).catch(() => stop(ownerId, state.revision));
      if (phase.completed) {
        owners.delete(ownerId);
        return;
      }
      state.animationFrame = requestAnimationFrame(tick);
    };
    state.animationFrame = requestAnimationFrame(tick);
  }

  function simulate(initialFraction, speed, durationSeconds, refreshRate, elapsedSeconds) {
    if (!finite(refreshRate) || refreshRate <= 0 || !finite(elapsedSeconds) || elapsedSeconds < 0) {
      throw new TypeError("Refresh rate must be positive and elapsed time must be non-negative.");
    }
    const frameDuration = 1000 / refreshRate;
    const frames = Math.round(elapsedSeconds * refreshRate);
    return phaseAt(initialFraction, speed, durationSeconds, frames * frameDuration, 1, frames);
  }

  return Object.freeze({ start, stop, phaseAt, simulate });
});
