import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { contextPath } from "./PlatformNavigation";
import type { Dataset, PipelineRun } from "./types";

/** 새로운 학습의 시작 화면과 분리된 모델 실험실 분석 이력. */
export function PipelineHistoryPanel({ datasets, onOpenRun, onStart }: {
  datasets: Dataset[];
  onOpenRun: (runId: string, datasetId: string) => void;
  onStart: () => void;
}) {
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    // 기존 조회 API의 최근 30건 한도를 전체 결과 수로 오인하지 않는다.
    void request<PipelineRun[]>("/api/pipeline-runs")
      .then(history => { if (active) setRuns(history); })
      .catch(reason => { if (active) setError(formatError(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [retry]);
  const statusName = (status: PipelineRun["status"]) => ({
    Queued: "대기 중", Running: "진행 중", Succeeded: "완료",
    Failed: "실패", Cancelled: "취소됨",
  })[status] ?? status;
  const datasetName = (id: string) =>
    datasets.find(dataset => dataset.id === id)?.name ?? `목록 밖의 데이터 · ${id.slice(0, 12)}`;
  return <section className="panel" aria-labelledby="pipeline-activity-heading">
    <div className="panel-head"><div>
      <h3 id="pipeline-activity-heading">활동 이상 탐지 분석 이력</h3>
      <p>최근 조회 가능한 분석 최대 30건입니다. 전체 이력·전체 건수를 의미하지 않습니다.</p>
    </div><button type="button" className="secondary" disabled={loading}
      onClick={() => setRetry(value => value + 1)}>새로 조회</button></div>
    {loading && <p role="status">이전 분석을 불러오는 중입니다.</p>}
    {!loading && error && <div className="pipeline-readiness-blocked" role="alert">
      <p>분석 이력 조회에 실패했습니다: {error}</p>
      <button type="button" className="secondary" onClick={() => setRetry(value => value + 1)}>
        다시 조회
      </button>
    </div>}
    {!loading && !error && runs.length === 0 && <div className="pipeline-next-step">
      <p>조회된 분석 결과가 없습니다.</p>
      <button type="button" onClick={onStart}>활동 이상 탐지 학습 시작 →</button>
    </div>}
    {!loading && !error && runs.length > 0 && <div className="ml-resource-list">
      {runs.map(run => <a key={run.id}
        className="platform-list-link ghost"
        href={contextPath("pipelines", { datasetId: run.dataset_id, pipelineRunId: run.id })}
        onClick={event => {
          if (event.button !== 0 || event.metaKey || event.ctrlKey ||
              event.shiftKey || event.altKey) return;
          event.preventDefault();
          onOpenRun(run.id, run.dataset_id);
        }}>
        <strong>활동 이상 탐지 · {datasetName(run.dataset_id)}</strong>
        <span>{new Date(run.created_at).toLocaleString("ko-KR")} · {statusName(run.status)}</span>
      </a>)}
    </div>}
  </section>;
}
