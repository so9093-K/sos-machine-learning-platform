import type { ExplorationRequest } from "./types";

/** 탐색 조건의 유일한 UI 초안. 서버 ExplorationSpec의 필드·연산은 재정의하지 않는다. */
export type ExplorationDraft = {
  datasetId: string;
  version: string;
  request: ExplorationRequest;
  source?: {
    title: string;
    reason: string;
    profileScope: "sample" | "full";
  };
};

export function draftForDataset(
  draft: ExplorationDraft | null, datasetId: string, version: string,
): ExplorationDraft | null {
  return draft?.datasetId === datasetId && draft.version === version ? draft : null;
}
