import { describe, expect, it } from "vitest";
import { mergeById } from "./collections";

describe("page merge identity", () => {
  it("prefers refreshed Job or Dataset records and keeps loaded history", () => {
    const newest = [{ id: "recent", status: "Succeeded" }, { id: "added", status: "Queued" }];
    const older = [{ id: "recent", status: "Running" }, { id: "past", status: "Failed" }];
    expect(mergeById(newest, older)).toEqual([
      newest[0], newest[1], older[1],
    ]);
    expect(older[0].status).toBe("Running");
  });

  it("loads older pages without replacing the latest state", () => {
    const current = [{ id: "a", attempt_count: 2 }];
    const loaded = [{ id: "a", attempt_count: 1 }, { id: "b", attempt_count: 1 }];
    expect(mergeById(current, loaded)).toEqual([current[0], loaded[1]]);
  });

  it("does not mutate existing arrays", () => {
    const previous = Object.freeze([{ id: "a" }]);
    const incoming = Object.freeze([{ id: "b" }]);
    expect(mergeById(incoming, previous)).toEqual([{ id: "b" }, { id: "a" }]);
  });
});
