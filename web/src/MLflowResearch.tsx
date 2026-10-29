import { useEffect, useMemo, useRef, useState } from "react";
import { formatError, request } from "./api";
import { ModelRegistryManager, type ModelGroup } from "./ModelRegistryManager";
import type {
  MLflowExperiment, MLflowExperimentPage, MLflowRun, MLflowRunPage, Dataset,
} from "./types";

import { RunComparison } from "./RunComparison";
import { ResearchRunDetails } from "./ResearchRunDetails";
import { isLocalOperatorHost, mlflowRunUrl, mlflowUrl } from "./operator-tools";
import type { MLResourceKind } from "./PlatformNavigation";
import { formatRecordedAt } from "./display-format";

const runLabels: Record<string, string> = {
  ml_train: "모델 학습", ml_score: "점수 계산", ml_policy: "판정 정책",
  ml_evaluate: "평가", ml_explain: "설명", ml_playground: "단일 입력 점수 계산",
};

function url(path: string, params: Record<string, string | null>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => { if (value) query.set(key, value); });
  return path + (query.size ? "?" + query.toString() : "");
}

/** 연구 묶음·Run·모델 버전의 정본은 MLflow다. 실행 권한은 SOS API에서 판단한다. */
export function MLflowResearch({ onTrain, onWorkflow, canManage, canTrain,
  datasets, registryKey, onSelectGroup, onOpenResource, canInspectOperations = false,
  experimentId = "", onSelectExperiment }: {
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  canInspectOperations?: boolean;
  onTrain: (group: string, experimentTitle: string) => void; onWorkflow: () => void;
  experimentId?: string;
  onSelectExperiment: (experiment: MLflowExperiment | null) => void;
  canManage: boolean; canTrain: boolean;
  datasets: Dataset[]; registryKey: string; onSelectGroup: (key: string) => void;
}) {
  const [localOperatorHost, setLocalOperatorHost] = useState(false);
  useEffect(() => { setLocalOperatorHost(isLocalOperatorHost()); }, []);
  const operatorLinks = canInspectOperations && localOperatorHost;
  const [experiments, setExperiments] = useState<MLflowExperiment[]>([]);
  const [runs, setRuns] = useState<MLflowRun[]>([]);
  const [experimentCursor, setExperimentCursor] = useState<string | null>(null);
  const [runCursor, setRunCursor] = useState<string | null>(null);
  const [experimentsLoading, setExperimentsLoading] = useState(true);
  const chosenExperiment = experiments.find(item => item.id === experimentId);
  // 선택한 ID를 확인하지 못한 상태에서 다른 연구 묶음으로 학습을 제출하지 않는다.
  const trainingExperimentTitle = experimentId ? chosenExperiment?.name : "기본 실험";
  const [selected, setSelected] = useState<string[]>([]);
  const [revision, setRevision] = useState(0);
  const [currentGroup, setCurrentGroup] = useState<ModelGroup | null>(null);
  const group = currentGroup?.registry_key === registryKey ? currentGroup : null;
  const groupTitle = group?.title ?? (registryKey === "default" ? "기본 활동 이상 탐지" : registryKey);
  const groupArchived = group?.archived ?? false;
  const [runKind, setRunKind] = useState("ml_train");
  const [runTerm, setRunTerm] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [experimentError, setExperimentError] = useState("");
  const [runError, setRunError] = useState("");
  const [runsLoading, setRunsLoading] = useState(true);
  // 연구 선택·새로고침 이후 도착한 이전 페이지가 현재 목록을 오염시키지 않는다.
  const activeSelection = useRef({ experimentId, revision, registryKey });
  activeSelection.current = { experimentId, revision, registryKey };

  useEffect(() => {
    let active = true;
    setExperimentError(""); setExperimentsLoading(true);
    void request<MLflowExperimentPage>("/api/ml/experiments").then(page => {
      if (!active) return;
      setExperiments(page.items); setExperimentCursor(page.next_page_token ?? null);
    }).catch(exc => { if (active) setExperimentError(formatError(exc)); })
      .finally(() => { if (active) setExperimentsLoading(false); });
    return () => { active = false; };
  }, [revision]);

  useEffect(() => {
    let active = true;
    setRunsLoading(true); setRunError("");
    setRuns([]); setRunCursor(null); setSelected([]);
    void request<MLflowRunPage>(url("/api/ml/experiments/runs", {
      experiment_id: experimentId, page_token: null,
    })).then(page => {
      if (!active) return;
      setRuns(page.items);
      setRunCursor(page.next_page_token ?? null);
    }).catch(exc => { if (active) setRunError(formatError(exc)); })
      .finally(() => { if (active) setRunsLoading(false); });
    return () => { active = false; };
  }, [revision, experimentId]);

  async function loadMore(kind: "experiments" | "runs") {
    if (loadingMore) return;
    const cursor = kind === "experiments" ? experimentCursor : runCursor;
    if (!cursor) return;
    const selection = { experimentId, revision, registryKey };
    const reportError = kind === "runs" ? setRunError : setExperimentError;
    setLoadingMore(true); reportError("");
    try {
      if (kind === "experiments") {
        const page = await request<MLflowExperimentPage>(url("/api/ml/experiments", { page_token: cursor }));
        if (activeSelection.current.revision !== selection.revision) return;
        setExperiments(old => [...old, ...page.items]);
        setExperimentCursor(page.next_page_token ?? null);
      } else {
        const page = await request<MLflowRunPage>(url("/api/ml/experiments/runs", {
          experiment_id: experimentId, page_token: cursor,
        }));
        if (
          activeSelection.current.revision !== selection.revision ||
          activeSelection.current.experimentId !== selection.experimentId
        ) return;
        setRuns(old => [...old, ...page.items]);
        setRunCursor(page.next_page_token ?? null);
      }
    } catch (exc) {
      if (activeSelection.current.revision === selection.revision &&
          (kind !== "runs" || activeSelection.current.experimentId === selection.experimentId)) {
        reportError(formatError(exc));
      }
    } finally {
      setLoadingMore(false);
    }
  }

  const datasetName = (id: string | null | undefined) => datasets.find(item => item.id === id)?.name ??
    (id ? "현재 목록 외 Dataset" : "Dataset 정보 없음");
  const filteredRuns = useMemo(() => runs.filter(item =>
    (runKind === "all" || item.job_kind === runKind) &&
    [item.id, item.job_id, runLabels[item.job_kind] ?? item.job_kind,
      datasets.find(d => d.id === item.params["sos.dataset_id"])?.name ?? ""]
      .some(value => value.toLowerCase().includes(runTerm.trim().toLowerCase()))),
    [runs, runKind, runTerm, datasets]);
  const compared = selected.map(id => runs.find(item => item.id === id))
    .filter((item): item is MLflowRun => item !== undefined);
  function toggleRun(id: string) {
    const next = runs.find(run => run.id === id);
    setSelected(current => current.includes(id) ? current.filter(value => value !== id)
      : current.length >= 2 || (compared.length > 0 && compared[0].job_kind !== next?.job_kind)
        ? current : [...current, id]);
  }

  return <div className="ml-resource-workspace">
    <section className="panel research-history" aria-label="연구 실행 비교">
      <div className="panel-head"><div><h2>연구 실행 비교</h2>
        <p>MLflow에 기록된 조건과 지표를 확인하고 SOS 실행 결과로 이어갑니다.</p></div>
        <div className="actions">
          {canTrain && <a href="#research-start">새 학습 준비 ↓</a>}
          {operatorLinks && <a href={mlflowUrl()} target="_blank" rel="noopener noreferrer">MLflow 실험 분석 ↗</a>}
          <button type="button" className="secondary" onClick={() => setRevision(x => x + 1)}>실험 재조회</button>
        </div>
      </div>
      <div className="research-history-body">
      <p>연구 묶음은 실험 목적을, 모델 그룹은 사용할 모델 버전의 묶음을 나타냅니다. 같은 종류의 실행을 최대 두 개 비교할 수 있습니다.</p>
      {experimentError && <p role="alert">실험 목록 조회 실패: {experimentError}</p>}
      {runError && <p role="alert">실행 이력 조회 실패: {runError}</p>}
      <div className="form-row">
        <label>연구 묶음
          <select value={experimentId} onChange={event => onSelectExperiment(
            experiments.find(item => item.id === event.target.value) ?? null)} aria-label="연구 Experiment 선택">
            <option value="">기본 실험</option>
            {experimentId && !chosenExperiment &&
              <option value={experimentId}>선택한 연구 · 목록 확인 필요</option>}
            {experiments.filter(item => item.name !== "기본 실험" || item.id === experimentId).map(item =>
              <option value={item.id} key={item.id}>{item.name}</option>)}
          </select>
        </label>
        <label>작업 종류
          <select value={runKind} onChange={event => { setRunKind(event.target.value); setSelected([]); }}>
            <option value="all">모든 작업</option>
            {Object.entries(runLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </label>
        <label>실행 검색
          <input value={runTerm} onChange={event => setRunTerm(event.target.value)} placeholder="Dataset·작업·Run ID" />
        </label>
      </div>
      {experimentCursor && <button type="button" className="secondary" disabled={loadingMore}
        onClick={() => void loadMore("experiments")}>실험 더 보기</button>}
      {runsLoading && <p role="status">실행 이력을 조회하고 있습니다.</p>}
      {!runsLoading && !runError && filteredRuns.length === 0 && <p className="muted">조회된 페이지에서 조건에 맞는 실행을 찾지 못했습니다.</p>}
      <p className="hint">현재 불러온 실행에서 검색합니다.{runCursor ? " 이전 실행은 더 보기로 조회할 수 있습니다." : ""}</p>
      <div className="research-run-list">
        {filteredRuns.map(item => <article className="research-run-row" key={item.id}>
          <label className="research-run-choice">
          <input type="checkbox" checked={selected.includes(item.id)}
            disabled={!selected.includes(item.id) && (selected.length >= 2 ||
              (compared.length > 0 && compared[0].job_kind !== item.job_kind))}
            onChange={() => toggleRun(item.id)}/>
          <span>{runLabels[item.job_kind] ?? item.job_kind} · {datasetName(item.params["sos.dataset_id"])}
            <small>{item.start_time ? formatRecordedAt(new Date(item.start_time).toISOString()) : ""}
              {" · MLflow 기록 "}{{ FINISHED: "완료", RUNNING: "진행 중", FAILED: "실패", KILLED: "종료" }[item.status ?? ""] ?? "상태 미확인"}
              {" · Run "}{item.id.slice(0, 10)}</small></span>
          </label>
          <ResearchRunDetails key={item.id + ":" + item.job_id} run={item} onOpenResource={onOpenResource} />
          {operatorLinks && <a href={mlflowRunUrl(item.id)} target="_blank" rel="noopener noreferrer">MLflow Run 운영 상세 ↗</a>}
        </article>)}
      </div>
      {runCursor && <button type="button" className="secondary" disabled={loadingMore}
        onClick={() => void loadMore("runs")}>이전 실행 더 보기</button>}
      {compared.length > 0 && <RunComparison runs={compared} datasets={datasets} />}
      </div>
    </section>
    <section id="research-start" tabIndex={-1} className="panel research-start" aria-label="새 연구 실행">
      <div className="panel-head"><div><h2>새 연구 실행</h2>
        <p>{groupTitle} · 데이터와 학습 조건을 선택해 새 모델 판본을 만듭니다.</p></div>
        {canTrain && <div className="actions">
          <button type="button" disabled={groupArchived || trainingExperimentTitle === undefined}
            onClick={() => { if (trainingExperimentTitle !== undefined) onTrain(registryKey, trainingExperimentTitle); }}>새 학습 →</button>
          <button type="button" className="secondary" onClick={onWorkflow}>활동 이상 탐지 Workflow →</button>
        </div>}
      </div>
      {trainingExperimentTitle !== undefined ? <p className="hint">학습 연구 묶음 · {trainingExperimentTitle}</p>
        : <p className="hint" role="status">{experimentsLoading ? "선택한 연구 묶음 확인 중…"
          : "선택한 연구를 현재 목록에서 확인하지 못했습니다. 실험 더 보기·재조회를 이용하거나 다른 연구 묶음을 선택하세요."}</p>}
      <details className="research-group-settings"><summary>학습 모델 그룹·등록 설정 · {groupTitle}</summary>
        <ModelRegistryManager activeKey={registryKey} onSelect={onSelectGroup}
          purpose="research" canManage={canManage} onGroupResolved={setCurrentGroup}
          onChanged={() => setRevision(x => x + 1)} />
      </details>
      {groupArchived && <p className="hint">보관된 모델 그룹입니다. 새 버전 학습 전 그룹 복원이 필요합니다.</p>}
      {!canTrain && <p className="hint">현재 계정은 실험 기록을 조회할 수 있습니다. 새 학습에는 Job 실행 권한이 필요합니다.</p>}
      <p className="hint">활동 이상 탐지 Workflow는 기본 모델 그룹을 사용하며 시간 분할 학습·기준 설정·Test 평가를 함께 실행합니다.</p>
    </section>
  </div>;
}
