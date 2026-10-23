import { useEffect, useMemo, useState } from "react";
import { formatError, request } from "./api";

type Experiment = { id: string; name: string };
type ModelVersion = {
  name: string; version: string; model_id: string; run_id: string | null;
  status: string; job_id: string; aliases: string[];
};
type Run = {
  id: string; job_id: string; job_kind: string; status: string;
  start_time: number | null;
  params: Record<string, string>;
  metrics: Record<string, number>;
};

/** MLflow 관리 정본을 SOS 권한 API로 탐색한다. 모델 실행은 SOS가 담당한다. */
export function MLflowResearch({ onOpenModel, onScoreModel, onTrain }: {
  onOpenModel: (id: string) => void;
  onScoreModel: (id: string) => void;
  onTrain: () => void;
}) {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [models, setModels] = useState<ModelVersion[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [term, setTerm] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void Promise.all([
      request<Experiment[]>("/api/ml/experiments"),
      request<ModelVersion[]>("/api/ml/registry/models"),
      request<Run[]>("/api/ml/experiments/runs"),
    ]).then(([nextExperiments, nextModels, nextRuns]) => {
      if (!active) return;
      setExperiments(nextExperiments);
      setModels(nextModels);
      setRuns(nextRuns);
      setSelected(current => current.filter(id => nextRuns.some(run => run.id === id)));
    }).catch(exc => {
      if (active) setError(formatError(exc));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [revision]);

  const filteredModels = useMemo(() => models.filter(item =>
    [item.name, item.version, item.job_id].some(value =>
      value.toLowerCase().includes(term.trim().toLowerCase()))), [models, term]);
  const filteredRuns = useMemo(() => runs.filter(item =>
    [item.id, item.job_id, item.job_kind].some(value =>
      value.toLowerCase().includes(term.trim().toLowerCase()))), [runs, term]);
  const compared = selected.map(id => runs.find(item => item.id === id))
    .filter((item): item is Run => item !== undefined);
  const comparableKeys = (field: "metrics" | "params") =>
    [...new Set(compared.flatMap(item => Object.keys(item[field])))].sort();

  function toggleRun(id: string) {
    setSelected(current => current.includes(id)
      ? current.filter(value => value !== id)
      : [...current.slice(-1), id]);
  }

  return <div className="ml-resource-workspace">
    <section className="panel">
      <div className="panel-head">
        <div><h3>실험·모델 자산 관리</h3>
          <p>MLflow가 실험·Run·모델 버전을 관리합니다. SOS는 접근 권한과 실제 모델 실행을 검증합니다.</p>
        </div>
        <div className="actions">
          <button type="button" onClick={onTrain}>새 학습 →</button>
          <button type="button" className="secondary" onClick={() => setRevision(x => x + 1)}>
            관리 기록 새로고침
          </button>
        </div>
      </div>
      {loading && <p role="status">MLflow 관리 기록을 조회하고 있습니다.</p>}
      {error && <p role="alert">{error}</p>}
      <p className="hint">연구 영역: {experiments.map(item => item.name).join(", ") || "기록 없음"}</p>
      <label>실험·모델 검색
        <input value={term} onChange={event => setTerm(event.target.value)}
          placeholder="모델 이름·버전·Job/Run ID" aria-label="실험과 모델 검색"/>
      </label>
    </section>
    <section className="panel">
      <h3>등록된 모델 판본</h3>
      {filteredModels.length === 0 && <p className="muted">등록된 모델이 없습니다. 학습 및 Registry 등록 완료 후 표시됩니다.</p>}
      <div className="job-list">
        {filteredModels.map(item => <article className="ml-resource-row" key={item.name + "@" + item.version}>
          <div><strong>{item.name} · v{item.version}</strong>
            <p className="hint">상태 {item.status} · 생성 Job {item.job_id.slice(0, 12)}
              {item.aliases.length ? " · Alias " + item.aliases.join(", ") : ""}</p>
          </div>
          <div className="model-index-actions">
            <button type="button" className="secondary" onClick={() => onOpenModel(item.model_id)}>
              모델 근거 확인
            </button>
            <button type="button" onClick={() => onScoreModel(item.model_id)}>
              Scoring →
            </button>
          </div>
        </article>)}
      </div>
    </section>
    <section className="panel">
      <h3>실험 Run 검색 및 비교</h3>
      <p className="hint">최대 두 Run을 선택해 기록된 파라미터와 수치형 지표를 비교합니다. 서로 다른 Job 종류·Dataset·평가 조건의 수치는 직접적인 성능 비교가 아닙니다.</p>
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
