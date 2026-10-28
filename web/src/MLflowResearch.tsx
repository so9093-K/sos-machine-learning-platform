import { useEffect, useMemo, useRef, useState } from "react";
import { formatError, request } from "./api";
import { ModelRegistryManager, ModelVersionManagement, type ModelGroup } from "./ModelRegistryManager";
import type {
  MLflowExperiment, MLflowExperimentPage, MLflowModelVersion,
  MLflowModelVersionPage, MLflowRun, MLflowRunPage, Dataset,
} from "./types";

import { RunComparison } from "./RunComparison";
import { ResearchRunDetails } from "./ResearchRunDetails";
import { isLocalOperatorHost, mlflowRunUrl } from "./operator-tools";
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
export function MLflowResearch({ onOpenModel, onScoreModel, onTrain, canManage, canTrain,
  datasets, registryKey, onSelectGroup, onOpenResource, canInspectOperations = false }: {
  onOpenModel: (id: string) => void;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  canInspectOperations?: boolean;
  onScoreModel: (id: string, group: string) => void;
  onTrain: (group: string) => void;
  canManage: boolean; canTrain: boolean;
  datasets: Dataset[]; registryKey: string; onSelectGroup: (key: string) => void;
}) {
  const [localOperatorHost, setLocalOperatorHost] = useState(false);
  useEffect(() => { setLocalOperatorHost(isLocalOperatorHost()); }, []);
  const operatorLinks = canInspectOperations && localOperatorHost;
  const [experiments, setExperiments] = useState<MLflowExperiment[]>([]);
  const [models, setModels] = useState<MLflowModelVersion[]>([]);
  const [runs, setRuns] = useState<MLflowRun[]>([]);
  const [experimentCursor, setExperimentCursor] = useState<string | null>(null);
  const [modelCursor, setModelCursor] = useState<string | null>(null);
  const [runCursor, setRunCursor] = useState<string | null>(null);
  const [experimentId, setExperimentId] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [term, setTerm] = useState("");
  const [revision, setRevision] = useState(0);
  const [currentGroup, setCurrentGroup] = useState<ModelGroup | null>(null);
  const group = currentGroup?.registry_key === registryKey ? currentGroup : null;
  const groupTitle = group?.title ?? (registryKey === "default" ? "기본 활동 이상 탐지" : registryKey);
  const groupArchived = group?.archived ?? false;
  const [modelSelectionKey, setModelSelectionKey] = useState(registryKey);
  const [runKind, setRunKind] = useState("ml_train");
  const [runTerm, setRunTerm] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [experimentError, setExperimentError] = useState("");
  const [runError, setRunError] = useState("");
  const [runsLoading, setRunsLoading] = useState(true);
  // 연구 선택·새로고침 이후 도착한 이전 페이지가 현재 목록을 오염시키지 않는다.
  const activeSelection = useRef({ experimentId, revision, registryKey });
  activeSelection.current = { experimentId, revision, registryKey };

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setModelCursor(null);
    void request<MLflowModelVersionPage>(url("/api/ml/registry/models", {
      registry_key: registryKey, include_archived: "true",
    })).then(model => {
      if (!active) return;
      setModelSelectionKey(registryKey);
      setModels(model.items); setModelCursor(model.next_page_token ?? null);
    }).catch(exc => { if (active) setError(formatError(exc)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [revision, registryKey]);

  useEffect(() => {
    let active = true;
    setExperimentError("");
    void request<MLflowExperimentPage>("/api/ml/experiments").then(page => {
      if (!active) return;
      setExperiments(page.items); setExperimentCursor(page.next_page_token ?? null);
    }).catch(exc => { if (active) setExperimentError(formatError(exc)); });
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

  async function loadMore(kind: "experiments" | "models" | "runs") {
    if (loadingMore) return;
    const cursor = kind === "experiments" ? experimentCursor :
      kind === "models" ? modelCursor : runCursor;
    if (!cursor) return;
    const selection = { experimentId, revision, registryKey };
    const reportError = kind === "models" ? setError : kind === "runs" ? setRunError : setExperimentError;
    setLoadingMore(true); reportError("");
    try {
      if (kind === "experiments") {
        const page = await request<MLflowExperimentPage>(url("/api/ml/experiments", { page_token: cursor }));
        if (activeSelection.current.revision !== selection.revision) return;
        setExperiments(old => [...old, ...page.items]);
        setExperimentCursor(page.next_page_token ?? null);
      } else if (kind === "models") {
        const page = await request<MLflowModelVersionPage>(url("/api/ml/registry/models", {
          page_token: cursor, registry_key: registryKey, include_archived: "true",
        }));
        if (activeSelection.current.revision !== selection.revision ||
            activeSelection.current.registryKey !== selection.registryKey) return;
        setModels(old => [...old, ...page.items]);
        setModelCursor(page.next_page_token ?? null);
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
          (kind !== "models" || activeSelection.current.registryKey === selection.registryKey) &&
          (kind !== "runs" || activeSelection.current.experimentId === selection.experimentId)) {
        reportError(formatError(exc));
      }
    } finally {
      setLoadingMore(false);
    }
  }

  const filter = term.trim().toLowerCase();
  const datasetName = (id: string | null | undefined) => datasets.find(item => item.id === id)?.name ??
    (id ? "현재 목록 외 Dataset" : "Dataset 정보 없음");
  const filteredModels = modelSelectionKey !== registryKey ? [] : models.filter(item =>
    [groupTitle, item.version, datasetName(item.dataset_id), item.job_id]
      .some(value => value.toLowerCase().includes(filter)));
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
    <ModelRegistryManager activeKey={registryKey} onSelect={onSelectGroup}
      canManage={canManage} onGroupResolved={setCurrentGroup}
      onChanged={() => setRevision(x => x + 1)} />
    <section className="panel model-version-panel">
      <div className="panel-head"><div><h2>{groupTitle} · 모델 버전</h2>
        <p>학습 데이터와 시각을 확인하고 사용할 버전을 선택하세요.</p></div>
        <div className="actions">
          {canTrain && <button type="button" disabled={groupArchived}
            onClick={() => onTrain(registryKey)}>이 그룹에 새 버전 학습 →</button>}
          <button type="button" className="secondary" onClick={() => setRevision(x => x + 1)}>새로고침</button>
        </div>
      </div>
      <label className="model-search">버전 검색
        <input value={term} onChange={event => setTerm(event.target.value)}
          placeholder="버전·학습 Dataset" aria-label="모델 버전 검색" />
      </label>
      <p className="hint">현재 불러온 모델 버전에서 검색합니다.{modelCursor ? " 이전 버전은 더 보기를 통해 조회할 수 있습니다." : ""}</p>
      {loading && <p role="status">모델 버전을 조회하고 있습니다.</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && filteredModels.length === 0 && <p className="muted">
        {term.trim() ? "현재 불러온 버전에서 검색 조건과 일치하는 결과가 없습니다." : modelCursor ? "현재 페이지에 표시할 버전이 없습니다. 이전 버전을 더 조회할 수 있습니다." : "이 그룹에 등록된 모델 버전이 없습니다. 새 학습으로 첫 버전을 등록할 수 있습니다."}</p>}
      <div className="model-version-list">
        {filteredModels.map(item => <article className="model-version-row" key={item.name + "@" + item.version}>
          <div className="model-version-identity"><h3>{groupTitle} · v{item.version}</h3>
            <p>{datasetName(item.dataset_id)}</p>
            <small>{item.created_at ? formatRecordedAt(item.created_at) : "학습 시각 정보 없음"}
              {item.archived ? " · 보관됨" : " · 등록 완료"}</small>
          </div>
          <div className="model-index-actions">
            <button type="button" disabled={item.archived || groupArchived}
              onClick={() => onScoreModel(item.model_id, registryKey)}>데이터에 적용 →</button>
            <button type="button" className="secondary" onClick={() => onOpenModel(item.model_id)}>모델 상세</button>
            {operatorLinks && item.run_id && <a href={mlflowRunUrl(item.run_id)} target="_blank" rel="noopener noreferrer">학습 Run 운영 상세 ↗</a>}
            <details className="model-version-settings"><summary>버전 관리·식별자</summary>
              <p className="hint">Registry {item.name} · Job {item.job_id}</p>
              {(item.aliases?.length ?? 0) > 0 && <p>Alias: {item.aliases?.join(", ")}</p>}
              <ModelVersionManagement model={item} registryKey={registryKey} canManage={canManage}
                onChanged={() => setRevision(x => x + 1)} />
            </details>
          </div>
        </article>)}
      </div>
      {modelCursor && <button type="button" className="secondary" disabled={loadingMore}
        onClick={() => void loadMore("models")}>모델 버전 더 보기</button>}
    </section>
    <section className="panel research-history" aria-label="연구 실행 비교">
      <div className="panel-head"><div><h2>연구 실행 비교</h2>
        <p>연구 목적별 실행의 조건·설정·기록된 지표 차이를 확인합니다.</p></div></div>
      <details><summary>연구 묶음·비교 대상 선택</summary>
      <div className="research-history-body">
      <p>연구 묶음은 실험 목적을, 모델 그룹은 사용할 모델 버전의 묶음을 나타냅니다. 같은 종류의 실행을 최대 두 개 비교할 수 있습니다.</p>
      {experimentError && <p role="alert">실험 목록 조회 실패: {experimentError}</p>}
      {runError && <p role="alert">실행 이력 조회 실패: {runError}</p>}
      <div className="form-row">
        <label>연구 묶음
          <select value={experimentId} onChange={event => setExperimentId(event.target.value)} aria-label="연구 Experiment 선택">
            <option value="">기본 실험</option>
            {experiments.filter(item => item.name !== "기본 실험").map(item =>
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
      </details>
    </section>
  </div>;
}
