import { describe, expect, it } from "vitest";
import { draftForDataset, type ExplorationDraft } from "./ExplorationDraft";

describe("Exploration draft provenance", () => {
  const candidate: ExplorationDraft = {
    datasetId: "dataset-a", version: "sha-a",
    request: { column: "score", metric: "mean", group_by: "department" },
    source: { title: "그룹별 수치 비교", reason: "통계·자료형 기반", profileScope: "sample" },
  };

  it("preserves the actual API request and the originating profile scope", () => {
    expect(draftForDataset(candidate, "dataset-a", "sha-a")).toEqual(candidate);
    expect(candidate.source?.profileScope).toBe("sample");
  });
  it("never reuses a candidate on another dataset or changed version", () => {
    expect(draftForDataset(candidate, "dataset-b", "sha-a")).toBeNull();
    expect(draftForDataset(candidate, "dataset-a", "sha-b")).toBeNull();
    expect(draftForDataset(null, "dataset-a", "sha-a")).toBeNull();
  });
});
