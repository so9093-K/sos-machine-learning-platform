import { describe, expect, it } from "vitest";
import { chartValue, explorationCategory, explorationValue } from "./exploration-format";

describe("Discovery numeric and category display contract", () => {
  it("preserves exact DECIMAL and UInt64 strings without coercion", () => {
    expect(explorationValue("9007199254740993.125000")).toBe("9007199254740993.125000");
    expect(explorationValue("18446744073709551615")).toBe("18446744073709551615");
    expect(chartValue("9007199254740993.125000")).toBeNull();
    expect(chartValue(12)).toBe(12);
  });
  it("distinguishes real NULL group from whole dataset", () => {
    expect(explorationCategory(null, "category")).toBe("(NULL 그룹)");
    expect(explorationCategory(null, null)).toBe("전체");
    expect(explorationCategory("base64:/w==", "blob")).toBe("base64:/w==");
    expect(explorationValue(null)).toBe("NULL");
  });
});
