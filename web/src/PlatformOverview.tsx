/** 홈은 백엔드 Job 분류 대신 지금 이용 가능한 제품 기능을 제공한다. */
import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { jobStatusLabel } from "./display-format";
import type { WorkspaceTab } from "./PlatformNavigation";
import type { PipelineRun } from "./types";

type DatasetLink = { id: string; name: string; kind: string };
type Props = {
  brandName: string;
  datasets: DatasetLink[];
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenDataset: (id: string) => void;
  onOpenPipelineRun: (runId: string, datasetId: string) => void;
};

export function PlatformOverview({ brandName, datasets, onNavigate, onOpenDataset, onOpenPipelineRun }: Props) {
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [runError, setRunError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setRunError("");
    void request<PipelineRun[]>("/api/pipeline-runs")
      .then(data => { if (active) setRuns(data); })
      .catch(error => { if (active) setRunError(formatError(error)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [retry]);
  // 조회 실패·재시도 중에는 이전 목록을 현재 결과처럼 보이지 않는다.
  const recent = loading || runError ? [] : [...runs].sort((a, b) =>
    b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id)).slice(0, 4);
  const datasetName = (id: string) => datasets.find(item => item.id === id)?.name ??
    "현재 Dataset 목록 밖의 데이터";
  return <div className="platform-home">
    <section className="platform-intro">
      <span className="eyebrow">{brandName} · EXPLORE · EXPERIMENT · UNDERSTAND · EVOLVE</span>
      <h2>무엇을 시작할까요?</h2>
      <p>현재 사용할 수 있는 데이터를 살펴보거나 준비된 활동 이상 탐지 분석을 이용할 수 있습니다.
        각 기능은 독립적으로 시작할 수 있습니다.</p>
    </section>
    <section aria-labelledby="available-work-heading">
      <h3 className="platform-section-title" id="available-work-heading">지금 이용할 수 있는 기능</h3>
      <div className="platform-destinations">
        <article className="panel platform-destination">
          <span className="eyebrow">DATA EXPLORER</span>
          <h4>데이터 탐색</h4>
          <p>Dataset을 등록하고 구조·분포·품질을 확인하며 제한된 EDA를 이용합니다.
            분석 Pipeline을 실행하지 않고도 사용할 수 있습니다.</p>
          <button type="button" onClick={() => onNavigate("understanding")}>
            데이터 탐색 시작 →
          </button>
        </article>
        <article className="panel platform-destination">
          <span className="eyebrow">모델 실험실</span>
          <h4>활동 이상 탐지</h4>
          <p>준비된 예제로 모델 학습을 시작하고, 실제 학습 결과와 이상 후보를 탐구합니다.</p>
          <p className="hint">모델을 이해한 뒤 다른 데이터에 적용할 수도 있습니다.</p>
          <button type="button" onClick={() => onNavigate("pipelines")}>
            모델 학습해 보기 →
          </button>
        </article>
      </div>
    </section>
    <section className="panel" aria-labelledby="resume-pipeline-heading">
      <div className="panel-head"><div>
        <h3 id="resume-pipeline-heading">이어서 이용할 분석</h3>
        <p>실제로 실행했던 Pipeline을 데이터와 결과 문맥으로 다시 엽니다.</p>
      </div>
        <button className="secondary" type="button" disabled={loading}
          onClick={() => setRetry(n => n + 1)}>다시 조회</button>
      </div>
      {loading && <p role="status">이전 분석을 확인하는 중입니다.</p>}
      {runError && <p role="alert">이전 분석을 조회하지 못했습니다: {runError}</p>}
      {!loading && !runError && recent.length === 0 &&
        <p className="muted">아직 실행한 분석이 없습니다. 위에서 활동 이상 탐지를 시작할 수 있습니다.</p>}
      {recent.length > 0 && <div className="platform-compact-list">
        {recent.map(run => <button type="button" key={run.id}
          className="platform-list-link ghost"
          onClick={() => onOpenPipelineRun(run.id, run.dataset_id)}>
          <span className="platform-resource-name">
            활동 이상 탐지 · {datasetName(run.dataset_id)}
          </span>
          <small>{new Date(run.created_at).toLocaleString("ko-KR")}</small>
          <span className={`status ${run.status.toLowerCase()}`}>{jobStatusLabel(run.status)}</span>
        </button>)}
      </div>}
    </section>
    <section className="panel" aria-labelledby="continue-data-heading">
      <div className="panel-head"><div>
        <h3 id="continue-data-heading">다시 살펴볼 데이터</h3>
        <p>이미 등록한 Dataset을 탐색하거나 분석에 활용할 수 있습니다.</p>
      </div><button className="secondary" type="button"
        onClick={() => onNavigate("preparation")}>독립 데이터 준비 →</button></div>
      {datasets.length === 0 ? <p className="muted">
        현재 조회한 Dataset이 없습니다. 데이터 탐색에서 파일을 등록하거나 예제를 선택하세요.</p> :
        <div className="platform-compact-list">
          {datasets.slice(0, 5).map(dataset => <button key={dataset.id} type="button"
            className="platform-list-link ghost" onClick={() => onOpenDataset(dataset.id)}>
            <span className="platform-resource-name">{dataset.name}</span>
            <small>{dataset.kind} · 탐색하기 →</small>
          </button>)}
        </div>}
    </section>
    <section className="platform-outlook">
      <h3>독립 ML 기능과 운영</h3>
      <p>모델·Job·불변 결과는 API에서 독립적으로 사용할 수 있습니다. 필요한 경우
        기존 상세 주소로 접근하거나 고급 실행·운영 화면에서 조회할 수 있습니다.</p>
      <div className="platform-outlook-actions">
        <button className="secondary" type="button" onClick={() => onNavigate("ml")}>독립 ML 실행·모델 →</button>
        <button className="secondary" type="button" onClick={() => onNavigate("jobs")}>작업 이력 →</button>
      </div>
    </section>
  </div>;
}
