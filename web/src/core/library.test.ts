import { afterEach, describe, expect, it } from "vitest";
import { libraryQuery, paths } from "./api";
import { libraryChoice, resetLibraryChoice } from "./library";

afterEach(() => resetLibraryChoice());

describe("library parameter", () => {
  it("defaults to main and leaves the URL unchanged", () => {
    expect(paths.sessions(1, {})).toBe("/api/v1/drivers/1/sessions");
    expect(paths.sessions(1, { library: "main" })).toBe("/api/v1/drivers/1/sessions");
    expect(paths.setups(1, {})).toBe("/api/v1/drivers/1/setups");
    expect(libraryQuery(undefined)).toBeUndefined();
    expect(libraryQuery(null)).toBeUndefined();
  });

  it("sends library=lab explicitly, beside the other filters", () => {
    expect(paths.sessions(1, { library: "lab" })).toBe("/api/v1/drivers/1/sessions?library=lab");
    expect(paths.sessions(1, { car: ["a", "b"], library: "lab", limit: 10 })).toBe("/api/v1/drivers/1/sessions?car=a&car=b&limit=10&library=lab");
    expect(paths.setups(1, { raced: true, library: "lab" })).toBe("/api/v1/drivers/1/setups?raced=true&library=lab");
  });

  it("keeps the visit's choice in memory only, main at the start", () => {
    expect(libraryChoice()).toBe("main");
  });
});
