import { describe, expect, it } from "vitest";
import { ceiling, custom, customOptional, fixed, lapTime, percent0, signed, whole } from "./text";

/** The cases of `crates/coach-live/src/format.rs`, checked against .NET 10 there. */
describe("overlay text rules", () => {
  it("fixed rounds exact ties to even like .NET F{n}", () => {
    expect(fixed(0.125, 2)).toBe("0.12");
    expect(fixed(0.375, 2)).toBe("0.38");
    expect(fixed(0.625, 2)).toBe("0.62");
    expect(fixed(2.5, 0)).toBe("2");
    expect(fixed(3.5, 0)).toBe("4");
    expect(fixed(0.5, 0)).toBe("0");
    expect(fixed(1.005, 2)).toBe("1.00");
    expect(fixed(12.345, 2)).toBe("12.35");
    expect(fixed(-0.5, 0)).toBe("-0");
    expect(fixed(-0.0004, 2)).toBe("-0.00");
    expect(fixed(139.80851825, 3)).toBe("139.809");
    expect(fixed(1e21, 1)).toBe("1000000000000000000000.0");
  });

  it("custom rounds half away from zero after fifteen digits", () => {
    expect(custom(0.125, 2)).toBe("0.13");
    expect(custom(1.005, 2)).toBe("1.01");
    expect(custom(0.285, 2)).toBe("0.29");
    expect(custom(0.995, 2)).toBe("1.00");
    expect(custom(2.5, 0)).toBe("3");
    expect(custom(0.25, 1)).toBe("0.3");
    expect(custom(0, 2)).toBe("0.00");
    expect(custom(9.996, 2)).toBe("10.00");
    expect(custom(0.00125, 2)).toBe("0.00");
    expect(custom(123456789.125, 2)).toBe("123456789.13");
    expect(custom(0.9950000047683716, 2)).toBe("1.00");
    expect(custom(-0.0004, 2)).toBe("0.00");
  });

  it("customOptional drops trailing zeros", () => {
    expect(customOptional(12, 2)).toBe("12");
    expect(customOptional(16.5, 2)).toBe("16.5");
    expect(customOptional(12.345, 2)).toBe("12.35");
    expect(customOptional(0.995, 2)).toBe("1");
  });

  it("signed uses the zero section for values that round to zero", () => {
    expect(signed(0.125, 3)).toBe("+0.125");
    expect(signed(-0.125, 3)).toBe("-0.125");
    expect(signed(0.4, 3)).toBe("+0.400");
    expect(signed(-0.0004, 3)).toBe("0.000");
    expect(signed(0.0004, 3)).toBe("0.000");
    expect(signed(0, 3)).toBe("0.000");
  });

  it("percent0 matches en-US P0", () => {
    expect(percent0(0.5)).toBe("50%");
    expect(percent0(0.125)).toBe("12%");
    expect(percent0(0.375)).toBe("38%");
    expect(percent0(0.625)).toBe("62%");
    expect(percent0(12.345)).toBe("1,235%");
    expect(percent0(1234.567)).toBe("123,457%");
    expect(percent0(0.995)).toBe("99%");
    expect(percent0(0.145)).toBe("14%");
    expect(percent0(-0.5)).toBe("-50%");
  });

  it("lap times truncate ticks and milliseconds and drop hours", () => {
    expect(lapTime(92.4189)).toBe("1:32.418");
    expect(lapTime(92.418)).toBe("1:32.418");
    expect(lapTime(59.9996)).toBe("59.999");
    expect(lapTime(61)).toBe("1:01.000");
    expect(lapTime(3725.5)).toBe("2:05.500");
    expect(lapTime(7325.5)).toBe("2:05.500");
    expect(lapTime(0.4)).toBe("0.400");
    expect(lapTime(30.8)).toBe("30.800");
    expect(lapTime(92.41799926757812)).toBe("1:32.417");
    expect(lapTime(3599.9999)).toBe("59:59.999");
  });

  it("whole and ceiling", () => {
    expect(ceiling(12.0001)).toBe("13");
    expect(ceiling(0.2)).toBe("1");
    expect(whole(25)).toBe("25");
    expect(whole(2.5)).toBe("3");
    expect(whole(-2.5)).toBe("-3");
    expect(whole(Number.NaN)).toBe("");
  });
});
