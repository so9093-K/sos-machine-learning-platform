import { useEffect, useMemo, useRef, useState } from "react";
import { formatError, request } from "./api";
import { ModelRegistryManager, ModelVersionManagement } from "./ModelRegistryManager";
import type {
  MLflowExperiment, MLflowExperimentPage, MLflowModelVersion,
  MLflowModelVersionPage, MLflowRun, MLflowRunPage,
} from "./types";

function url(path: string, params: Record<string, string | null>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => { if (value) query.set(key, value); });
  return path + (query.size ? "?" + query.toString() : "");
}

/** 연구 묶음·Run·모델 버전의 정본은 MLflow다. 실행 권한은 SOS API에서 판단한다. */
export function MLflowResearch({ onOpenModel, onScoreModel, onTrain }: {
  onOpenModel: (id: string) => void;
  onScoreModel: (id: string) => void;
  onTrain: () => void;
}) {
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
  const [registryKey, setRegistryKey] = useState("default");
  const [groupArchived, setGroupArchived] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  // 연구 선택·새로고침 이후 도착한 이전 페이지가 현재 목록을 오염시키지 않는다.
  const activeSelection = useRef({ experimentId, revision, registryKey });
  activeSelection.current = { experimentId, revision, registryKey };

  useEffect(() => {
    let active = true;
    void request<{items: Array<{registry_key: string; archived: boolean}>}>(
      "/api/ml/registry/groups",
    ).then(page => {
      if (active) setGroupArchived(
        page.items.some(item => item.registry_key === registryKey && item.archived));
    }).catch(() => { if (active) setGroupArchived(false); });
    return () => { active = false; };
  }, [registryKey, revision]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void Promise.all([
      request<MLflowExperimentPage>("/api/ml/experiments"),
      request<MLflowModelVersionPage>(url("/api/ml/registry/models", {
        registry_key: registryKey, include_archived: "true",
      })),
    ]).then(([exp, model]) => {
      if (!active) return;
      setExperiments(exp.items); setExperimentCursor(exp.next_page_token ?? null);
      setModels(model.items); setModelCursor(model.next_page_token ?? null);
    }).catch(exc => { if (active) setError(formatError(exc)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [revision, registryKey]);

  useEffect(() => {
    let active = true;
    setRuns([]); setRunCursor(null); setSelected([]);
    void request<MLflowRunPage>(url("/api/ml/experiments/runs", {
      experiment_id: experimentId, page_token: null,
    })).then(page => {
      if (!active) return;
      setRuns(page.items);
      setRunCursor(page.next_page_token ?? null);
    }).catch(exc => { if (active) setError(formatError(exc)); });
    return () => { active = false; };
  }, [revision, experimentId]);

  async function loadMore(kind: "experiments" | "models" | "runs") {
    if (loadingMore) return;
    const cursor = kind === "experiments" ? experimentCursor :
      kind === "models" ? modelCursor : runCursor;
    if (!cursor) return;
    const selection = { experimentId, revision, registryKey };
    setLoadingMore(true); setError("");
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
      setError(formatError(exc));
    } finally {
      setLoadingMore(false);
    }
  }

  const filter = term.trim().toLowerCase();
  const filteredModels = useMemo(() => models.filter(item =>
    [item.name, item.version, item.job_id].some(value => value.toLowerCase().includes(filter))),
    [models, filter]);
  const filteredRuns = useMemo(() => runs.filter(item =>
    [item.id, item.job_id, item.job_kind].some(value => value.toLowerCase().includes(filter))),
    [runs, filter]);
  const compared = selected.map(id => runs.find(item => item.id === id))
    .filter((item): item is MLflowRun => item !== undefined);
  const comparableKeys = (field: "metrics" | "params") =>
    [...new Set(compared.flatMap(item => Object.keys(item[field])))].sort();
  function toggleRun(id: string) {
    setSelected(current => current.includes(id)
      ? current.filter(value => value !== id) : [...current.slice(-1), id]);
  }

  return <div className="ml-resource-workspace">
    <ModelRegistryManager activeKey={registryKey} onSelect={setRegistryKey}
      onChanged={() => setRevision(x => x + 1)} />
    <section className="panel">
      <div className="panel-head">
        <div><h3>실험·모델 자산 관리</h3>
          <p>MLflow가 연구 Experiment·Run·모델 버전을 관리하고 SOS가 모델 실행과 권한을 확인합니다.</p>
        </div>
        <div className="actions">
          <button type="button" onClick={onTrain}>새 학습 →</button>
          <button type="button" className="secondary" onClick={() => setRevision(x => x + 1)}>
            새로고침
          </button>
        </div>
      </div>
      {loading && <p role="status">MLflow 기록을 조회하고 있습니다.</p>}
      {error && <p role="alert">{error}</p>}
      <label>연구 Experiment
        <select value={experimentId} onChange={event => setExperimentId(event.target.value)}
          aria-label="연구 Experiment 선택">
          <option value="">기본 실험</option>
          {experiments.filter(item => item.name !== "기본 실험").map(item =>
            <option value={item.id} key={item.id}>{item.name}</option>)}
        </select>
      </label>
      {experimentCursor && <button type="button" className="secondary"
        disabled={loadingMore} onClick={() => void loadMore("experiments")}>Experiment 더 보기</button>}
      <label>모델 및 Run 검색
        <input value={term} onChange={event => setTerm(event.target.value)}
          placeholder="모델 이름·버전·Job/Run ID" aria-label="모델과 Run 검색"/>
      </label>
    </section>
    <section className="panel">
      <h3>등록된 모델 버전</h3>
      {filteredModels.length === 0 && <p className="muted">등록된 모델이 없습니다. 학습 및 등록 후 표시됩니다.</p>}
      <div className="job-list">
        {filteredModels.map(item => <article className="ml-resource-row" key={item.name + "@" + item.version}>
          <div><strong>{item.name} · v{item.version}</strong>
            <p className="hint">상태 {item.status} · 생성 Job {item.job_id.slice(0, 12)}
              {item.aliases?.length ? " · Alias " + item.aliases.join(", ") : ""}
              {item.archived ? " · 보관됨" : ""}</p>
          </div>
          <div className="model-index-actions">
            <button type="button" className="secondary" onClick={() => onOpenModel(item.model_id)}>
              모델 근거 확인
            </button>
            <button type="button" disabled={item.archived || groupArchived}
              onClick={() => onScoreModel(item.model_id)}>Scoring →</button>
            <ModelVersionManagement model={item} registryKey={registryKey}
              onChanged={() => setRevision(x => x + 1)}/>
          </div>
        </article>)}
      </div>
      {modelCursor && <button type="button" className="secondary" disabled={loadingMore}
        onClick={() => void loadMore("models")}>모델 버전 더 보기</button>}
    </section>
    <section className="panel">
      <h3>Run 검색 및 비교</h3>
      <p className="hint">선택한 Experiment의 Run에서 최대 두 개를 비교합니다. Dataset·평가 조건이 다르면 성능 수치를 직접 비교할 수 없습니다.</p>
      {filteredRuns.length === 0 && <p className="muted">조회된 Run이 없습니다.</p>}
      <div className="job-list">
        {filteredRuns.map(item => <label className="ml-resource-row" key={item.id}>
          <input type="checkbox" checked={selected.includes(item.id)}
            onChange={() => toggleRun(item.id)}/>
          <span>{item.job_kind} · {item.id.slice(0, 12)} · {item.status}
            <small className="muted"> · Job {item.job_id.slice(0, 12)}</small>
          </span>
        </label>)}
      </div>
      {runCursor && <button type="button" className="secondary" disabled={loadingMore}
        onClick={() => void loadMore("runs")}>Run 더 보기</button>}
      {compared.length > 0 && <>
        <h4>선택 Run 비교</h4>
        {(["params", "metrics"] as const).map(field => <div key={field}>
          <h4>{field === "params" ? "실행 파라미터" : "기록된 지표"}</h4>
          <div className="table-scroll"><table>
            <thead><tr><th>항목</th>{compared.map(run => <th key={run.id}>{run.id.slice(0, 10)}</th>)}</tr></thead>
            <tbody>{comparableKeys(field).map(key => <tr key={key}>
              <td>{key}</td>{compared.map(run => <td key={run.id}>
                {run[field][key] === undefined ? "—" : String(run[field][key])}
              </td>)}
            </tr>)}</tbody>
          </table></div>
        </div>)}
      </>}
    </section>
  </div>;
}
