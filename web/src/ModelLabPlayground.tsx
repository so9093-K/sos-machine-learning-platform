/** Dataset 등록 없이 실제 Worker Playground Job으로 한 관측치를 점수화한다. */
import { useEffect, useRef, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { activityFeatureLabel } from "./ActivityFeatureLabels";
import type { MLPlaygroundJob, MLPlaygroundJobPage, MLPlaygroundJobSummary, ModelRevision } from "./types";

const TERMINAL = new Set(["Succeeded", "Failed", "Cancelled"]);

export function exampleValues(model: ModelRevision): Record<string, string> | null {
  const medians = model.runtime?.feature_reference_medians;
  const dtypes = model.runtime?.feature_dtypes;
  if (!Array.isArray(medians) || !Array.isArray(dtypes) ||
      medians.length !== model.features.length || dtypes.length !== model.features.length ||
      !medians.every(value => typeof value === "number" && Number.isFinite(value))) return null;
  return Object.fromEntries(model.features.map((name, i) => {
    const dtype = String(dtypes[i]);
    const value = dtype.startsWith("Int") || dtype.startsWith("UInt")
      ? Math.round(medians[i] as number) : medians[i];
    return [name, String(Math.max(dtype.startsWith("UInt") ? 0 : -Infinity, value))];
  }));
}

export function playgroundNumberValid(raw: string, dtype: string): boolean {
  if (!raw.trim()) return false;
  const value = Number(raw);
  if (!Number.isFinite(value)) return false;
  if (/^U?Int(8|16|32|64)$/.test(dtype)) {
    if (!Number.isSafeInteger(value)) return false;
    const bits = Number(dtype.match(/\d+$/)?.[0] ?? "0");
    const unsigned = dtype.startsWith("UInt");
    const low = unsigned ? 0 : -(2 ** (bits - 1));
    const high = Math.min(Number.MAX_SAFE_INTEGER,
      unsigned ? 2 ** bits - 1 : 2 ** (bits - 1) - 1);
    return value >= low && value <= high;
  }
  if (dtype === "Float32") return Math.abs(value) <= 3.4028234663852886e38;
  return dtype === "Float64";
}

export function ModelLabPlayground({ model, canRun, canCancel, onDataset }: {
  model: ModelRevision; canRun: boolean; canCancel: boolean; onDataset: () => void;
}) {
  const reference = exampleValues(model);
  const [mode, setMode] = useState<"example" | "edit">(reference ? "example" : "edit");
  const [fields, setFields] = useState<Record<string, string>>(() =>
    reference ?? Object.fromEntries(model.features.map(name => [name, ""])));
  const [expanded, setExpanded] = useState(false);
  const [job, setJob] = useState<MLPlaygroundJob | null>(null);
  const [lastScore, setLastScore] = useState<number | null>(null);
  const [previousScore, setPreviousScore] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const submitting = useRef(false);
  const historyFetching = useRef(false);
  const [history, setHistory] = useState<MLPlaygroundJobSummary[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");

  async function loadHistory(append: boolean) {
    if (historyFetching.current || (append && !historyCursor)) return;
    historyFetching.current = true;
    setHistoryLoading(true); setHistoryError("");
    try {
      const params = new URLSearchParams({ model_revision_id: model.id });
      if (append && historyCursor) params.set("cursor", historyCursor);
      const page = await request<MLPlaygroundJobPage>("/api/ml/playground/jobs?" + params);
      setHistory(old => append
        ? [...old, ...page.items.filter(item => !old.some(existing => existing.id === item.id))]
        : page.items);
      setHistoryCursor(page.next_cursor ?? null);
      setHistoryLoaded(true);
    } catch (cause) { setHistoryError(formatError(cause)); }
    finally { historyFetching.current = false; setHistoryLoading(false); }
  }

  async function reopenHistory(id: string) {
    if (running) return;
    setError("");
    try {
      const restored = await request<MLPlaygroundJob>(
        "/api/ml/playground/jobs/" + encodeURIComponent(id));
      if (restored.spec.model_revision_id !== model.id)
        throw new Error("현재 모델과 다른 실행 기록입니다.");
      setJob(restored);
      setLastScore(restored.status === "Succeeded" &&
        typeof restored.result?.anomaly_score === "number"
          ? restored.result.anomaly_score : null);
      setPreviousScore(null);
      const saved = restored.spec.features;
      if (saved && typeof saved === "object" && !Array.isArray(saved)) {
        const values = saved as Record<string, unknown>;
        setFields(Object.fromEntries(model.features.map(name => [
          name, values[name] == null ? "" : String(values[name]),
        ])));
        setMode("edit");
      }
    } catch (cause) { setError(formatError(cause)); }
  }

  useEffect(() => {
    if (!job || TERMINAL.has(job.status)) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      try {
        const next = await request<MLPlaygroundJob>("/api/ml/playground/jobs/" + encodeURIComponent(job.id));
        if (alive) {
          setJob(next);
          const value = next.result?.anomaly_score;
          if (next.status === "Succeeded" && typeof value === "number" && Number.isFinite(value))
            setLastScore(value);
        }
      } catch (cause) {
        if (alive) setError("실행 상태 조회 실패: " + formatError(cause));
      } finally {
        if (alive) timer = setTimeout(() => void poll(), 2000);
      }
    };
    timer = setTimeout(() => void poll(), 1200);
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [job?.id, job?.status]);

  const input = model.features.map((name, index) => {
    const raw = fields[name]?.trim() ?? "";
    const dtype = String(model.runtime.feature_dtypes?.[index] ?? "");
    return { name, value: raw ? Number(raw) : null,
      valid: playgroundNumberValid(raw, dtype) };
  });
  const invalid = input.filter(item => !item.valid);
  const running = sending || Boolean(job && !TERMINAL.has(job.status));
  async function execute() {
    if (!canRun || running || invalid.length || submitting.current) return;
    submitting.current = true;
    setSending(true); setError("");
    setPreviousScore(lastScore); setLastScore(null); setJob(null);
    try {
      const created = await submitJob<MLPlaygroundJob>("/api/ml/playground/jobs", {
        model_revision_id: model.id,
        features: Object.fromEntries(input.map(item => [item.name, item.value])),
      });
      setJob(created);
    } catch (cause) { setError(formatError(cause)); }
    finally { submitting.current = false; setSending(false); }
  }
  async function cancel() {
    if (!job || TERMINAL.has(job.status) || !canCancel) return;
    try {
      setJob(await request<MLPlaygroundJob>("/api/ml/playground/jobs/" + encodeURIComponent(job.id) + "/cancel",
        { method: "POST" }));
    } catch (cause) { setError(formatError(cause)); }
  }
  const shortNames = model.features.slice(0, 6);
  const otherNames = model.features.slice(6);
  const featureField = (name: string) => <label className="model-lab-feature" key={name}>
    <span>{activityFeatureLabel(name)} <small>{name} · {
      model.runtime.feature_dtypes?.[model.features.indexOf(name)] ?? "자료형 미확인"
    }</small></span>
    <input type="number" inputMode="decimal" step="any" value={fields[name] ?? ""}
      disabled={mode === "example" || running}
      onChange={event => setFields(current => ({ ...current, [name]: event.target.value }))}
      aria-label={activityFeatureLabel(name) + " 입력값"} />
  </label>;
  return <section className="panel model-lab-playground" aria-label="직접 모델 점수 계산">
    <div className="model-lab-playground-head">
      <div><h3>활동 한 건으로 모델 체험하기</h3>
        <p>기준 입력값을 그대로 적용하거나 숫자를 바꾸어 모델의 실제 점수 변화를 확인합니다.</p></div>
      <button type="button" className="secondary" onClick={onDataset}>Dataset 전체에 적용 →</button>
    </div>
    <div className="model-lab-mode-tabs" role="group" aria-label="입력 방식">
      <button type="button" aria-pressed={mode === "example"} className="secondary"
        disabled={!reference}
        onClick={() => { setMode("example"); if (reference) setFields(reference); }}>
        기준값으로 체험
      </button>
      <button type="button" aria-pressed={mode === "edit"} className="secondary"
        onClick={() => setMode("edit")}>직접 수정해보기</button>
    </div>
    {reference ? <p className="hint">입력값은 이 모델의 학습 Feature 중앙값을 바탕으로 구성했습니다.
      정수 Feature는 정수로 조정했으며, 실제 관측치나 정상 활동의 판정값이 아닙니다.</p>
      : <p className="hint" role="status">이 모델 판본에는 초기 입력 기준값이 없어 자동으로 채우지 않았습니다.
        직접 수정 모드에서 모든 필수 Feature 값을 입력해 주세요.</p>}
    <div className="model-lab-input-grid">{shortNames.map(featureField)}</div>
    {otherNames.length > 0 && <details open={expanded}
      onToggle={event => setExpanded(event.currentTarget.open)}
      className="model-lab-extra-features">
      <summary>나머지 필수 입력값 {otherNames.length}개 {expanded ? "접기" : "확인"}</summary>
      <div className="model-lab-input-grid">{otherNames.map(featureField)}</div>
    </details>}
    {invalid.length > 0 && <p role="alert" className="model-lab-input-error">
      필수 입력값이나 자료형·정수 범위가 맞지 않는 Feature {invalid.length}개가 있습니다.
      {invalid.length <= 3 && " 확인: " + invalid.map(item => activityFeatureLabel(item.name)).join(", ")}
    </p>}
    <p className="hint">입력 Feature {model.features.length}개가 실제 모델에 전달됩니다.
      실행을 요청하면 입력값과 결과가 권한이 적용되는 Job 이력에 보존됩니다.</p>
    {error && <p role="alert">{error}</p>}
    <div className="model-lab-playground-actions">
      <button type="button" onClick={() => void execute()}
        disabled={!canRun || running || invalid.length > 0}>
        {running ? "모델 실행 중…" : "실제 이상 점수 계산하기 →"}
      </button>
      {reference && <button type="button" className="secondary"
        onClick={() => setFields(reference)} disabled={running}>기준값으로 초기화</button>}
      {job && !TERMINAL.has(job.status) && canCancel &&
        <button type="button" className="secondary" onClick={() => void cancel()}>취소 요청</button>}
    </div>
    {job && <div className="model-lab-playground-result" aria-live="polite">
      <strong>점수 계산 · {({ Queued: "대기 중", Running: "실행 중", Succeeded: "완료",
        Failed: "실패", Cancelled: "취소됨" } as Record<string, string>)[job.status] ?? job.status}</strong>
      {job.status === "Succeeded" && lastScore !== null &&
        <div className="model-lab-score-row">
          <div><span>이번 입력의 이상 점수</span><strong>{lastScore.toFixed(5)}</strong></div>
          {previousScore !== null && <div><span>이전 계산과의 차이</span>
            <strong>{(lastScore - previousScore) > 0 ? "+" : ""}{(lastScore - previousScore).toFixed(5)}</strong></div>}
        </div>}
      {job.status === "Failed" && <p role="alert">점수 계산 실패 · {job.error_code ?? "실행 오류"}</p>}
      <p className="hint">점수가 높을수록 모델에서 이례적인 입력입니다. 확률이나 사건 판정은 아닙니다.</p>
    </div>}
    <details className="model-lab-playground-history" onToggle={event => {
      if (event.currentTarget.open && !historyLoaded) void loadHistory(false);
    }}>
      <summary>이 모델의 이전 체험 결과 다시 보기</summary>
      <div className="model-lab-history-body">
        <p className="hint">이전에 제출한 입력과 실제 Job 결과를 선택해 다시 확인할 수 있습니다.</p>
        <button type="button" className="secondary" disabled={historyLoading}
          onClick={() => void loadHistory(false)}>실행 기록 새로고침</button>
        {historyError && <p role="alert">{historyError}</p>}
        {historyLoading && <p role="status">실행 기록 조회 중…</p>}
        {historyLoaded && history.length === 0 && <p className="hint">
          현재 조회한 실행 기록에는 이 모델의 결과가 없습니다.</p>}
        <div className="model-lab-history-list">
          {history.map(item => <button type="button" className="surface-button"
            key={item.id} disabled={running}
            aria-pressed={job?.id === item.id} onClick={() => void reopenHistory(item.id)}>
            <span>{new Date(item.created_at).toLocaleString("ko-KR")} · {item.status}</span>
            <span>결과 보기 →</span>
          </button>)}
        </div>
        {historyCursor && <button type="button" className="secondary"
          disabled={historyLoading} onClick={() => void loadHistory(true)}>
          이전 실행 더 조회하기
        </button>}
      </div>
    </details>
  </section>;
}
