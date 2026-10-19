import { describe, expect, it } from "vitest";
import { draftForDataset, type ExplorationDraft } from "./ExplorationDraft";

describe("Exploration draft provenance", () => {
  const candidate: ExplorationDraft = {
    datasetId: "dataset-a", version: "sha-a",
    request: { column: "score", metric: "mean", group_by: "department" },
    source: { title: "그룹별 수치 비교", reason: "통계·자료형 기반", profileScope: "sample" },
  };

  it("keeps a candidate only on its original dataset and version", () => {
    expect(draftForDataset(candidate, "dataset-a", "sha-a")).toBe(candidate);
    expect(draftForDataset(candidate, "dataset-b", "sha-a")).toBeNull();
    expect(draftForDataset(candidate, "dataset-a", "sha-b")).toBeNull();
    expect(draftForDataset(null, "dataset-a", "sha-a")).toBeNull();
  });
});
