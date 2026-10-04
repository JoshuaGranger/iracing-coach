import { describe, expect, it } from "vitest";
import { libraryQuery, paths } from "./api";
import { libraryChoice, libraryOfQuery } from "./library";
import { parseHash } from "./router";

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
});

describe("the page's library comes from the URL query only (#34)", () => {
  it("is lab only for library=lab in the hash query, main for everything else", () => {
    expect(libraryOfQuery(parseHash("#/d/1507810/sessions?library=lab").query)).toBe("lab");
    expect(libraryOfQuery(parseHash("#/d/1507810/setups?library=lab").query)).toBe("lab");
    expect(libraryOfQuery(parseHash("#/d/1507810/sessions").query)).toBe("main");
    expect(libraryOfQuery(parseHash("#/d/1507810/sessions?library=main").query)).toBe("main");
    expect(libraryOfQuery(parseHash("#/d/1507810/sessions?library=other").query)).toBe("main");
    expect(libraryOfQuery(parseHash("#/d/1507810/sessions?library=LAB").query)).toBe("main");
  });

  it("is main outside a browser, and nothing is stored", () => {
    expect(libraryChoice()).toBe("main");
  });
});
