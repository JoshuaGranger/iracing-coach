import { describe, expect, it } from "vitest";
import { type OutboxEntry } from "../core/outbox";
import { pendingSummary } from "./outbox-bar";

const entry = (label: string, state: OutboxEntry["state"]): OutboxEntry => ({ id: label, method: "PUT", path: "/prefs", label, state, attempts: 0, createdAt: 1 });
describe("pending save description", () => {
  it("names the pending write rather than an unexplained change", () => {
    expect(pendingSummary([entry("Last viewed session", "pending")])).toBe("Last viewed session waiting to save");
    expect(pendingSummary([entry("New note", "sending")])).toBe("New note waiting to save");
  });
  it("keeps real conflicts and failures explicit", () => {
    expect(pendingSummary([entry("Edit note", "conflict"), entry("Trace layout", "failed")])).toBe("Edit note conflict · Trace layout not saved");
  });
});
