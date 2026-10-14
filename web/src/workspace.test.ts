import { describe, expect, it } from "vitest";
import { createStep, histogram, needsTimeOrder } from "./feature-utils";

describe("Feature Builder contracts", () => {
  it("preserves zero and validates required values", () => {
    expect(createStep("fill_null", "pressure", "0")).toEqual({
      op: "fill_null", column: "pressure", value: 0,
    });
    expect(() => createStep("fill_null", "pressure", " ")).toThrow();
    expect(createStep("fill_null", "label", "unknown").value).toBe("unknown");
    expect(createStep("fill_null", "code", "001", "String").value).toBe("001");
  });
  it("detects time dependencies", () => {
    expect(needsTimeOrder([createStep("lag", "temperature", "")])).toBe(true);
    expect(needsTimeOrder([createStep("filter_not_null", "temperature", "")])).toBe(false);
    expect(() => createStep("unsupported", "temperature", "")).toThrow();
  });
});
describe("Sampled EDA charts", () => {
  it("does not reinterpret null as zero", () => {
    const d = histogram([{ x: 1 }, { x: null }, { x: 3 }, { x: 3 }], "x", 2);
    expect(d?.counts).toEqual([1, 2]);
  });
  it("handles empty and constant values", () => {
    expect(histogram([], "x")).toBeNull();
    expect(histogram([{ x: "text" }], "x")).toBeNull();
    expect(histogram([{ x: 2 }, { x: 2 }], "x")).toEqual({
      labels: ["2.00"], counts: [2],
    });
  });
});
