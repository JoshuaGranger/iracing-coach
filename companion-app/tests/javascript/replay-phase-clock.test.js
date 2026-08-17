"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const clock = require("../../src/iRacingCoach.UI/wwwroot/replay-phase-clock.js");

test("controlled clock is refresh-rate independent at 60/120/144/244 Hz", () => {
  const phases = [60, 120, 144, 244].map(rate => clock.simulate(0.1, 2, 120, rate, 30));
  for (const phase of phases) assert.ok(Math.abs(phase.fraction - 0.6) < 1e-12);
});

test("immutable phase includes one revision and monotonic frame identity", () => {
  const phase = clock.phaseAt(0.25, 1, 100, 5000, 17, 301);
  assert.equal(phase.revision, 17);
  assert.equal(phase.frame, 301);
  assert.equal(phase.fraction, 0.3);
  assert.equal(Object.isFrozen(phase), true);
  assert.throws(() => { phase.fraction = 0; }, TypeError);
});

test("phase clamps exactly at the end", () => {
  const phase = clock.phaseAt(0.9, 4, 10, 1000, 2, 9);
  assert.equal(phase.fraction, 1);
  assert.equal(phase.completed, true);
});

test("invalid and non-finite clock inputs fail closed", () => {
  assert.throws(() => clock.phaseAt(0, 1, 0, 1, 1, 1), TypeError);
  assert.throws(() => clock.phaseAt(0, Number.NaN, 1, 1, 1, 1), TypeError);
  assert.throws(() => clock.simulate(0, 1, 1, 0, 1), TypeError);
});
