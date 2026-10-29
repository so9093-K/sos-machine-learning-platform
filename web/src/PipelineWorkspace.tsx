import { useEffect, useRef, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { contextPath, type MLResourceKind } from "./PlatformNavigation";
import { PipelineRunDetails } from "./PipelineRunDetails";
import { formatRecordedAt, jobStatusLabel } from "./display-format";
import type { Dataset, PipelineCheck, PipelineDefinition, PipelineRun } from "./types";

function PipelineStart({ datasets, datasetId, onSelectDataset, hasMoreDatasets,
  onLoadMoreDatasets, onSubmitted }: {
  datasets: Dataset[]; datasetId: string; onSelectDataset: (id: string) => void;
  hasMoreDatasets: boolean; onLoadMoreDatasets: () => Promise<void>;
  onSubmitted: (run: PipelineRun) => void;
}) {
  const [check, setCheck] = useState<PipelineCheck | null>(null);
  const [definition, setDefinition] = useState<PipelineDefinition | null>(null);
  const [checkError, setCheckError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [retry, setRetry] = useState(0);
  const [accepted, setAccepted] = useState(false);
  const [moreLoading, setMoreLoading] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    let active = true;
    setCheck(null); setCheckError(""); setSubmitError(""); setDefinition(null);
    setAccepted(false); setLoading(true);
    void Promise.all([
      request<PipelineDefinition>("/api/pipelines/activity-anomaly-if"),
      datasetId ? request<PipelineCheck>(`/api/pipelines/activity-anomaly-if/check?dataset_id=${encodeURIComponent(datasetId)}`)
        : Promise.resolve(null),
    ]).then(([definition, checked]) => {
      if (active) { setDefinition(definition); setCheck(checked); }
    }).catch(cause => { if (active) setCheckError(formatError(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [datasetId, retry]);
  async function start() {
    if (submitting || accepted || !check?.ready || check.dataset_id !== datasetId) return;
    setSubmitting(true); setSubmitError("");
    try {
      const run = await submitJob<PipelineRun>("/api/pipelines/activity-anomaly-if/runs", { dataset_id: datasetId });
      if (!mounted.current) return;
      setAccepted(true);
      onSubmitted(run);
    } catch (cause) { if (mounted.current) setSubmitError(formatError(cause)); }
    finally { if (mounted.current) setSubmitting(false); }
  }
  return <div className="pipeline-start">
    {definition && <p><strong>{definition.name} · v{definition.version}</strong> · {definition.description}</p>}
    <label>입력 Dataset<select value={datasetId} disabled={submitting || accepted}
      onChange={event => onSelectDataset(event.target.value)}>
      <option value="">Dataset 선택</option>
      {datasetId && !datasets.some(item => item.id === datasetId) && <option value={datasetId}>URL로 선택한 Dataset</option>}
      {datasets.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>
    {hasMoreDatasets && <button className="secondary" disabled={moreLoading || submitting} onClick={async () => {
      setMoreLoading(true);
      try { await onLoadMoreDatasets(); } catch (cause) { setCheckError(formatError(cause)); }
      finally { setMoreLoading(false); }
    }}>Dataset 더 보기</button>}
    {loading && <p role="status">등록 정의·입력 구조 확인 중…</p>}
    {checkError && <div role="alert"><p>입력 확인 실패 · {checkError}</p>
      <button className="secondary" disabled={loading} onClick={() => setRetry(value => value + 1)}>입력 다시 확인</button></div>}
    {check?.dataset_id === datasetId && <div role="status">
      <p>{check.ready ? "입력 구조 검사 통과" : "입력 구조 불일치"}</p>
      {check.issues.map(issue => <p key={issue}>{issue}</p>)}
      {check.ready && <p className="hint">전체 값·시간 분할 검증은 실행의 첫 단계에서 수행됩니다.</p>}
    </div>}
    <p>실행 시 새 모델을 학습하고 보정·판정·Test 평가를 계산합니다. 계산 자원이 사용되며 모델 제공은 별도 절차입니다.</p>
    {submitError && <p role="alert">실행 접수 확인 실패 · {submitError} · 이력에서 접수 여부를 확인할 수 있습니다.</p>}
    <button disabled={!check?.ready || check.dataset_id !== datasetId || loading || Boolean(checkError) || submitting || accepted}
      onClick={() => void start()}>{submitting ? "실행 접수 중…" : accepted ? "실행 접수 완료" : "새 모델 학습·파이프라인 실행"}</button>
    {definition && <details><summary>등록된 입력 조건·분석 한계</summary>
      <p>필수 시간 컬럼 {definition.required_time_column} · 숫자형 Feature {definition.required_features.length}개</p>
      <ul>{definition.limitations.map(item => <li key={item}>{item}</li>)}</ul>
    </details>}
  </div>;
}

/** 최근 이력·결과 조사를 기본으로 하고 권한 있는 Actor만 고정 정의를 새로 실행한다. */
export function PipelineWorkspace({ runId, datasetId, datasets, canTrain, canRun, canCancel, canOpenMLflow,
  hasMoreDatasets, onLoadMoreDatasets, onSelectDataset, onSelectRun, onOpenResource, onOpenDataset }: {
  runId: string; datasetId: string; datasets: Dataset[];
  canTrain: boolean; canRun: boolean; canCancel: boolean; canOpenMLflow: boolean;
  hasMoreDatasets: boolean; onLoadMoreDatasets: () => Promise<void>;
  onSelectDataset: (id: string) => void; onSelectRun: (id: string) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  onOpenDataset: (id: string) => void;
}) {
  const [history, setHistory] = useState<PipelineRun[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [page, setPage] = useState(0);
  const [startContext, setStartContext] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    setLoading(true);
    void request<PipelineRun[]>("/api/pipeline-runs").then(items => {
      if (active) { setHistory(items); setHistoryError(""); setPage(0); }
    }).catch(cause => { if (active) setHistoryError(formatError(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [retry]);
  return <div className="pipeline-workspace">
    <section className="panel">
      <details key={runId || "history"} open={!runId} className="pipeline-history-disclosure">
      <summary>최근 실행 이력</summary>
      <div className="panel-head"><div><h2>등록 파이프라인·실행 이력</h2>
        <p>활동 이상 탐지 · 입력 분할부터 모델·판정·평가까지 한 실행의 결과를 조사합니다.</p></div>
        <button className="secondary" disabled={loading} onClick={() => setRetry(value => value + 1)}>이력 다시 조회</button>
      </div>
      {loading && <p role="status">최근 실행 이력 조회 중…</p>}
      {historyError && <p role="alert">이력 조회 실패 · {historyError} · 기존 표시는 마지막 조회 기록입니다.</p>}
      {!loading && !historyError && history.length === 0 && <p>아직 조회 가능한 실행이 없습니다.</p>}
      <ul className="pipeline-history">
        {history.slice(page * 5, page * 5 + 5).map(item => <li key={item.id}>
          <a href={contextPath("pipelines", { pipelineRunId: item.id, datasetId })}
            aria-current={runId === item.id ? "page" : undefined} onClick={event => {
              if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
              event.preventDefault(); onSelectRun(item.id);
            }}>
            <strong>{datasets.find(dataset => dataset.id === item.dataset_id)?.name ?? "활동 이상 탐지 실행"}</strong>
            <span>{formatRecordedAt(item.created_at)} · {jobStatusLabel(item.status)}{item.cancel_requested ? " · 취소 요청 중" : ""}</span>
          </a>
        </li>)}
      </ul>
      {history.length > 5 && <div className="pipeline-actions">
        <button className="secondary" disabled={page === 0} onClick={() => setPage(value => value - 1)}>이전</button>
        <span>{page + 1} / {Math.ceil(history.length / 5)}</span>
        <button className="secondary" disabled={(page + 1) * 5 >= history.length} onClick={() => setPage(value => value + 1)}>다음</button>
      </div>}
      <p className="hint">서버가 반환한 최근 실행 최대 30건 · 이력 상태는 마지막 조회 기준</p>
      </details>
      {canTrain ? <details key={runId || "new"} className="pipeline-new-run"
        onToggle={event => { if (event.currentTarget.open) setStartContext(runId || "new"); }}>
        <summary>새 파이프라인 실행</summary>
        {startContext === (runId || "new") && <PipelineStart datasets={datasets} datasetId={datasetId} onSelectDataset={onSelectDataset}
          hasMoreDatasets={hasMoreDatasets} onLoadMoreDatasets={onLoadMoreDatasets}
          onSubmitted={item => { onSelectRun(item.id); setRetry(value => value + 1); }} />}
      </details> : <p className="hint">결과 조회 · 새 실행에는 모델 학습 권한이 필요합니다.</p>}
    </section>
    {runId ? <PipelineRunDetails key={runId} runId={runId} canRun={canRun} canCancel={canCancel} canOpenMLflow={canOpenMLflow}
      onOpenResource={onOpenResource} onOpenDataset={onOpenDataset} />
      : <section className="panel"><h2>실행 결과 탐색</h2><p>이력에서 실행을 선택하면 데이터 분할·모델·점수·판정·평가 근거를 확인할 수 있습니다.</p></section>}
  </div>;
}
