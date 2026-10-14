import { describe, expect, it } from "vitest";

describe("Data Workspace contracts", () => {
  it("keeps file and Iceberg versions distinct", () => {
    const file = { kind: "file", version: "a".repeat(64) };
    const iceberg = { kind: "iceberg", version: "12345" };
    expect(file.version).not.toEqual(iceberg.version);
  });
});
