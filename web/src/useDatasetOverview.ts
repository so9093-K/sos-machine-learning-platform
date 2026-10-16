import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import type { DatasetOverview } from "./types";

type OverviewState = {
  datasetId: string;
  revision: number;
  status: "loading" | "ready" | "error";
  data: DatasetOverview | null;
  error: string;
};

/** Dataset 판본별 Overview 조회의 정본. 화면마다 독립적으로 요청하지 않는다. */
export function useDatasetOverview(datasetId: string, revision: number) {
  const [retryCount, setRetryCount] = useState(0);
  const [state, setState] = useState<OverviewState | null>(null);
  useEffect(() => {
    if (!datasetId) return;
    let active = true;
    setState({ datasetId, revision, status: "loading", data: null, error: "" });
    request<DatasetOverview>(
      `/api/datasets/${encodeURIComponent(datasetId)}/overview`,
    ).then(data => {
      if (active) setState({ datasetId, revision, status: "ready", data, error: "" });
    }).catch(error => {
      if (active) setState({
        datasetId, revision, status: "error", data: null, error: formatError(error),
      });
    });
    return () => { active = false; };
  }, [datasetId, revision, retryCount]);
  const current = state?.datasetId === datasetId && state.revision === revision
    ? state : null;
  return {
    overview: current?.data ?? null,
    overviewStatus: current?.status ?? "loading",
    overviewError: current?.error ?? "",
    retryOverview: () => setRetryCount(n => n + 1),
  };
}
