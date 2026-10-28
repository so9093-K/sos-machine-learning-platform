/** Dataset 등록 없이 실제 Worker Playground Job으로 한 관측치를 점수화한다. */
import { useEffect, useRef, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { activityFeatureLabel } from "./ActivityFeatureLabels";
import { groupActivityFeatures } from "./ActivityFeatureGroups";
import { FeatureValueComparison } from "./FeatureValueComparison";
import { PlaygroundExplanations } from "./PlaygroundExplanations";
import { jobStatusLabel } from "./display-format";
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

/** 현재 편집값이 실제로 성공한 Job에서 제출한 값과 일치하는지 확인한다. */
export function playgroundInputMatchesResult(
  fields: Record<string, string>, submitted: Record<string, unknown> | null | undefined,
  names: readonly string[],
): boolean {
  return Boolean(submitted) && Object.keys(submitted!).length === names.length &&
    names.every(name => {
      const raw = fields[name]?.trim() ?? "";
      const previous = submitted?.[name];
      return raw !== "" && Number.isFinite(Number(raw)) &&
        typeof previous === "number" && Number.isFinite(previous) &&
        Number(raw) === previous;
    });
}

/** 실패·취소·실행 중 상태는 마지막 성공 점수를 덮어쓰지 않는다. */
export function playgroundLatestSuccessfulScore(
  previous: number | null, completed: Pick<MLPlaygroundJob, "status" | "result">,
): number | null {
  const score = completed.result?.anomaly_score;
  return completed.status === "Succeeded" && typeof score === "number" && Number.isFinite(score)
    ? score : previous;
}

/** 숫자 표기의 차이가 아니라 실제 모델에 전달될 값의 차이를 찾는다. */
export function playgroundChangedFeatures(
  fields: Record<string, string>, reference: Record<string, string>, names: readonly string[],
): string[] {
  return names.filter(name => {
    const current = fields[name]?.trim() ?? "";
    const baseline = reference[name]?.trim() ?? "";
    if (!current || !baseline || !Number.isFinite(Number(current)) ||
        !Number.isFinite(Number(baseline))) return current !== baseline;
    return Number(current) !== Number(baseline);
  });
}

export function ModelLabPlayground({ model, canRun, canCancel }: {
  model: ModelRevision; canRun: boolean; canCancel: boolean;
}) {
  const reference = exampleValues(model);
  const [mode, setMode] = useState<"example" | "edit">(reference ? "example" : "edit");
  const [fields, setFields] = useState<Record<string, string>>(() =>
    reference ?? Object.fromEntries(model.features.map(name => [name, ""])));
  const [job, setJob] = useState<MLPlaygroundJob | null>(null);
  const [lastScore, setLastScore] = useState<number | null>(null);
  const [latestSuccessfulScore, setLatestSuccessfulScore] = useState<number | null>(null);
  const [latestSuccessfulJob, setLatestSuccessfulJob] = useState<MLPlaygroundJob | null>(null);
  const [comparisonJob, setComparisonJob] = useState<MLPlaygroundJob | null>(null);
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
  const [selectedHistory, setSelectedHistory] = useState<MLPlaygroundJob | null>(null);
  const [historyDetailError, setHistoryDetailError] = useState("");
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);
  const [historyDetailLoading, setHistoryDetailLoading] = useState(false);
  const historyRequest = useRef(0);

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
    // 다른 기록을 선택하거나 접는 동안 도착한 이전 응답은 화면 상태를 바꾸지 않는다.
    const requestNumber = ++historyRequest.current;
    if (selectedHistoryId === id) {
      setSelectedHistoryId(null);
      setSelectedHistory(null);
      setHistoryDetailLoading(false);
      setHistoryDetailError("");
      return;
    }
    setSelectedHistoryId(id);
    setSelectedHistory(null);
    setHistoryDetailLoading(true);
    setHistoryDetailError("");
    try {
      const restored = await request<MLPlaygroundJob>(
        "/api/ml/playground/jobs/" + encodeURIComponent(id));
      if (requestNumber !== historyRequest.current) return;
      if (restored.id !== id || restored.spec.model_revision_id !== model.id)
        throw new Error("선택한 기록 또는 모델 판본이 조회 결과와 일치하지 않습니다.");
      setSelectedHistory(restored);
    } catch (cause) {
      if (requestNumber === historyRequest.current) setHistoryDetailError(formatError(cause));
    } finally {
      if (requestNumber === historyRequest.current) setHistoryDetailLoading(false);
    }
  }

  function useHistoryInput(restored: MLPlaygroundJob) {
    if (running || !restored.spec.features) return;
    const saved = restored.spec.features as Record<string, unknown>;
    setFields(Object.fromEntries(model.features.map(name => [
      name, saved[name] == null ? "" : String(saved[name]),
    ])));
    setMode("edit");
  }

  useEffect(() => {
    if (!job || TERMINAL.has(job.status)) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      try {
        const next = await request<MLPlaygroundJob>("/api/ml/playground/jobs/" + encodeURIComponent(job.id));
        if (alive) {
          setError("");
          setJob(next);
          const value = next.result?.anomaly_score;
          if (next.status === "Succeeded") {
            if (typeof value === "number" && Number.isFinite(value)) {
              setLastScore(value);
              setLatestSuccessfulScore(current => playgroundLatestSuccessfulScore(current, next));
              setLatestSuccessfulJob(next);
            } else setError("점수화가 완료됐지만 결과 점수를 확인할 수 없습니다.");
          }
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

  const featureDtypes = Array.isArray(model.runtime.feature_dtypes)
    ? model.runtime.feature_dtypes as string[] : [];
  const referenceMedians = Array.isArray(model.runtime.feature_reference_medians)
    ? model.runtime.feature_reference_medians as number[] : [];
  const input = model.features.map((name, index) => {
    const raw = fields[name]?.trim() ?? "";
    const dtype = String(featureDtypes[index] ?? "");
    return { name, value: raw ? Number(raw) : null,
      valid: playgroundNumberValid(raw, dtype) };
  });
  const invalid = input.filter(item => !item.valid);
  const running = sending || Boolean(job && !TERMINAL.has(job.status));
  const submittedFeatures = job?.spec.features;
  const submittedInput = submittedFeatures && typeof submittedFeatures === "object" &&
    !Array.isArray(submittedFeatures)
    ? submittedFeatures as Record<string, unknown> : null;
  const matchesCompletedInput = job?.status === "Succeeded" && lastScore !== null &&
    playgroundInputMatchesResult(fields, submittedInput, model.features);
  const editedNames = reference ? playgroundChangedFeatures(fields, reference, model.features) : [];
  const comparisonNames = reference
    ? (editedNames.length > 0 ? editedNames : model.features).slice(0, 3) : [];
  const inputGroups = groupActivityFeatures(
    Object.fromEntries(model.features.map(name => [name, null])));
  const explanationAvailable = referenceMedians.length === model.features.length &&
    referenceMedians.every(value => typeof value === "number" && Number.isFinite(value));
  const comparedFeatures = comparisonJob && job?.status === "Succeeded" &&
    lastScore !== null && comparisonJob.id !== job.id &&
    comparisonJob.spec.model_revision_id === job.spec.model_revision_id
    ? model.features.filter(name => {
      const before = (comparisonJob.spec.features as Record<string, unknown>)[name];
      const after = (job.spec.features as Record<string, unknown>)[name];
      return before !== after;
    }) : [];

  async function execute() {
    if (!canRun || running || invalid.length || submitting.current) return;
    submitting.current = true;
    setSending(true); setError("");
    const comparisonScore = latestSuccessfulScore;
    const previousCompletedJob = latestSuccessfulJob;
    try {
      const created = await submitJob<MLPlaygroundJob>("/api/ml/playground/jobs", {
        model_revision_id: model.id,
        features: Object.fromEntries(input.map(item => [item.name, item.value])),
      });
      // 제출이 실패한 경우에는 이전 결과와 비교 문맥을 잃지 않는다.
      setPreviousScore(comparisonScore);
      setComparisonJob(previousCompletedJob);
      setLastScore(null);
      setJob(created);
      // 동일 멱등 키 재제출 시 이미 성공한 Job이 응답할 수 있다.
      // 완료 응답을 Polling 전용 경로에만 기대하면 점수가 사라진다.
      if (created.status === "Succeeded") {
        const score = created.result?.anomaly_score;
        if (typeof score === "number" && Number.isFinite(score)) {
          setLastScore(score);
          setLatestSuccessfulScore(current => playgroundLatestSuccessfulScore(current, created));
          setLatestSuccessfulJob(created);
        } else setError("점수화가 완료됐지만 결과 점수를 확인할 수 없습니다.");
      }
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
  const featureField = (name: string) => <label className="model-lab-feature" key={name}>
    <span>{activityFeatureLabel(name)} <small>{name} · {
      featureDtypes[model.features.indexOf(name)] ?? "자료형 미확인"
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
    </div>
    <div className="model-lab-workspace">
      <div className="model-lab-input-pane">
    <div className="model-lab-mode-tabs" role="group" aria-label="입력 방식">
      <button type="button" aria-pressed={mode === "example"} className="secondary"
        disabled={!reference || running}
        onClick={() => { setMode("example"); if (reference) setFields(reference); }}>
        기준값으로 체험
      </button>
      <button type="button" aria-pressed={mode === "edit"} className="secondary"
        disabled={running} onClick={() => setMode("edit")}>직접 수정해보기</button>
    </div>
    {reference ? <p className="hint">입력값은 이 모델의 학습 Feature 중앙값을 바탕으로 구성했습니다.
      정수 Feature는 정수로 조정했으며, 실제 관측치나 정상 활동의 판정값이 아닙니다.</p>
      : <p className="hint" role="status">이 모델 판본에는 초기 입력 기준값이 없어 자동으로 채우지 않았습니다.
        직접 수정 모드에서 모든 필수 Feature 값을 입력해 주세요.</p>}
    {mode === "edit" && <p className="hint">Isolation Forest는 학습 데이터 범위를 벗어난 값을 범위 끝의
      값과 비슷하게 다룹니다. 한 Feature에 아주 큰 값을 넣어도 점수가 크게 오르지 않을 수 있으며,
      여러 Feature가 함께 평소와 다를 때 점수 변화가 더 뚜렷해질 수 있습니다.</p>}
    <div className="model-lab-feature-groups">
      {inputGroups.map(group => {
        const names = group.entries.map(([name]) => name);
        const shown = names.slice(0, 3);
        const remaining = names.slice(3);
        const needsReview = names.filter(name => invalid.some(item => item.name === name)).length;
        return <section className="model-lab-feature-group" key={group.id}>
          <div className="model-lab-feature-group-heading">
            <strong>{group.title}</strong><span>{names.length}개 Feature
              {needsReview > 0 && ` · 입력 확인 ${needsReview}개`}</span>
          </div>
          <p className="hint">{group.description} · 모델 중요도 순위가 아닙니다.</p>
          <div className="model-lab-input-grid">{shown.map(featureField)}</div>
          {remaining.length > 0 && <details className="model-lab-extra-features">
            <summary>이 그룹의 나머지 필수 입력값 {remaining.length}개 확인</summary>
            <div className="model-lab-input-grid">{remaining.map(featureField)}</div>
          </details>}
        </section>;
      })}
    </div>
    {reference && <section className="model-lab-input-comparison"
      aria-label="입력 Feature와 Train 참조값 비교">
      <div className="model-lab-feature-group-heading">
        <strong>{editedNames.length > 0 ? "변경한 입력값과 학습 참조값" : "현재 입력값과 학습 참조값"}</strong>
        <span>{editedNames.length > 0 ? `변경 ${editedNames.length}개` : "기준 입력"}</span>
      </div>
      <p className="hint">같은 Feature 안에서만 막대 길이를 비교합니다. 학습 중앙값은 정상 범위나
        모델 기여도가 아닙니다. 중앙값이 0이거나 음수인 경우 등 비례 표현이 부적절하면 숫자로 표시합니다.</p>
      <div className="model-lab-reference-grid">
        {comparisonNames.map(name => <div key={name} className="model-lab-reference-item">
          <strong>{activityFeatureLabel(name)}</strong>
          <FeatureValueComparison actual={playgroundNumberValid(fields[name] ?? "",
            featureDtypes[model.features.indexOf(name)] ?? "")
            ? Number(fields[name]) : null}
            reference={referenceMedians[model.features.indexOf(name)]}
            actualLabel="현재 입력값" />
        </div>)}
      </div>
      {editedNames.length > comparisonNames.length && <small className="hint">
        변경한 나머지 {editedNames.length - comparisonNames.length}개 값은 위 Feature 입력에서 확인할 수 있습니다.
      </small>}
    </section>}
    {invalid.length > 0 && <p role="alert" className="model-lab-input-error">
      필수 입력값이나 자료형·정수 범위가 맞지 않는 Feature {invalid.length}개가 있습니다.
      {invalid.length <= 3 && " 확인: " + invalid.map(item => activityFeatureLabel(item.name)).join(", ")}
    </p>}
      </div>
      <aside className="model-lab-results-pane" aria-label="단일 입력 계산과 결과">
    <h4>계산과 결과</h4>
    <p className="model-lab-input-state" role="status">{running
      ? "제출한 입력을 모델에서 계산하는 중입니다."
      : matchesCompletedInput
        ? "현재 입력값은 최근 완료된 계산에 사용한 값과 일치합니다."
        : job?.status === "Succeeded" && lastScore !== null
          ? "입력값이 마지막 계산 이후 변경됐습니다. 아래 점수는 이전 입력의 결과이며 현재 입력은 아직 계산하지 않았습니다."
          : job?.status === "Failed" || job?.status === "Cancelled"
            ? "마지막 실행에서는 점수를 얻지 못했습니다. 현재 입력값을 계산하려면 다시 실행해 주세요."
            : "현재 입력값은 아직 계산하지 않았습니다. 점수를 확인하려면 실행을 요청하세요."}</p>
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
          <div><span>{matchesCompletedInput ? "현재 입력의 이상 점수" : "마지막 제출 입력의 이상 점수"}</span>
            <strong>{lastScore.toFixed(5)}</strong></div>
          {previousScore !== null && <div><span>이전 완료 계산과의 점수 차이</span>
            <strong>{(lastScore - previousScore) > 0 ? "+" : ""}{(lastScore - previousScore).toFixed(5)}</strong></div>}
        </div>}
      {job.status === "Failed" && <p role="alert">점수 계산 실패 · {job.error ?? "실행 오류"}
        {job.error_code && <small> ({job.error_code})</small>}</p>}
      <p className="hint">점수가 높을수록 모델에서 이례적인 입력입니다. 확률이나 사건 판정은 아닙니다.</p>
    </div>}
    {job?.status === "Succeeded" && lastScore !== null && comparisonJob &&
      comparedFeatures.length > 0 && <section className="model-lab-run-comparison"
        aria-label="이전 실행과 이번 실행 비교">
        <h5>두 완료 계산의 입력·점수 차이</h5>
        <p className="hint">같은 모델 판본으로 완료한 두 Job의 실제 제출값입니다.
          이 차이는 변경 Feature의 인과적 기여도를 뜻하지 않습니다.</p>
        <div className="model-lab-score-row">
          <div><span>이전 점수</span><strong>{typeof comparisonJob.result?.anomaly_score === "number"
            ? comparisonJob.result.anomaly_score.toFixed(5) : "없음"}</strong></div>
          <div><span>이번 점수</span><strong>{lastScore.toFixed(5)}</strong></div>
        </div>
        <p className="hint">바뀐 Feature {comparedFeatures.length}개</p>
        <div className="table-scroll"><table>
          <thead><tr><th>Feature</th><th>이전 실행</th><th>이번 실행</th></tr></thead>
          <tbody>{comparedFeatures.map(name => <tr key={name}>
            <td>{activityFeatureLabel(name)}</td>
            <td>{String((comparisonJob.spec.features as Record<string, unknown>)[name] ?? "결측")}</td>
            <td>{String((job.spec.features as Record<string, unknown>)[name] ?? "결측")}</td>
          </tr>)}</tbody>
        </table></div>
      </section>}
    {job?.status === "Succeeded" && (explanationAvailable
      ? <details className="model-lab-explanation-toggle">
          <summary>이 입력 설명하기 · SHAP / Feature 치환 민감도</summary>
          <PlaygroundExplanations key={job.id} source={job} canRun={canRun}
            canCancel={canCancel} shapSupported={model.features.length <= 64} />
        </details>
      : <p className="hint">이 모델에는 학습 Feature 중앙값이 없어 현재 설명 방법을 계산할 수 없습니다.
          점수 계산 결과는 정상적으로 이용할 수 있습니다.</p>)}
      </aside>
    </div>
    <details className="model-lab-playground-history" onToggle={event => {
      if (event.currentTarget.open && !historyLoaded) void loadHistory(false);
    }}>
      <summary>이 모델의 이전 체험 결과 다시 보기</summary>
      <div className="model-lab-history-body">
        <p className="hint">과거 결과를 펼쳐 확인합니다. 조회만으로 현재 편집 중인 입력값은 변경되지 않습니다.</p>
        <button type="button" className="secondary" disabled={historyLoading}
          onClick={() => void loadHistory(false)}>실행 기록 새로고침</button>
        {historyError && <p role="alert">{historyError}</p>}
        {historyLoading && <p role="status">실행 기록 조회 중…</p>}
        {historyLoaded && history.length === 0 && <p className="hint">
          현재 조회한 실행 기록에는 이 모델의 결과가 없습니다.</p>}
        <div className="model-lab-history-list">
          {history.map(item => <div className="model-lab-history-entry" key={item.id}>
            <button type="button" className="surface-button"
              aria-expanded={selectedHistoryId === item.id}
              onClick={() => void reopenHistory(item.id)}>
              <span>{new Date(item.created_at).toLocaleString("ko-KR")} · {jobStatusLabel(item.status)}</span>
              <span>{selectedHistoryId === item.id ? "상세 접기 ↑" : item.status === "Failed"
                ? "실패 이유 확인 ↓" : "결과 확인 ↓"}</span>
            </button>
            {selectedHistoryId === item.id && <div className="model-lab-history-detail">
              {historyDetailLoading && <p role="status">선택한 결과를 조회하는 중입니다.</p>}
              {historyDetailError && <p role="alert">기록 조회 실패: {historyDetailError}</p>}
              {selectedHistory && <>
              <strong>선택한 실행 · {jobStatusLabel(selectedHistory.status)}</strong>
              {selectedHistory.status === "Succeeded" &&
                typeof selectedHistory.result?.anomaly_score === "number" &&
                <p>이상 점수 <strong>{selectedHistory.result.anomaly_score.toFixed(5)}</strong></p>}
              {selectedHistory.status === "Failed" &&
                <p role="status">실패 사유 · {selectedHistory.error ?? "실행 오류"}
                  {selectedHistory.error_code && <small> ({selectedHistory.error_code})</small>}</p>}
              {selectedHistory.status === "Cancelled" && <p>취소된 점수 계산입니다.</p>}
              {!TERMINAL.has(selectedHistory.status) && <p>이 실행은 아직 완료되지 않았습니다.</p>}
              <details>
                <summary>제출 당시 Feature 입력값 {model.features.length}개 확인</summary>
                <dl className="model-lab-history-features">
                  {model.features.map(name => <div key={name}>
                    <dt>{activityFeatureLabel(name)}</dt>
                    <dd>{String((selectedHistory.spec.features as Record<string, unknown>)[name] ?? "값 없음")}</dd>
                  </div>)}
                </dl>
              </details>
              <button type="button" className="secondary"
                disabled={running}
                onClick={() => useHistoryInput(selectedHistory)}>이 입력으로 다시 계산 준비</button>
              {selectedHistory.status === "Succeeded" && explanationAvailable &&
                <details className="model-lab-explanation-toggle">
                  <summary>이전 실행의 SHAP / Feature 민감도 확인</summary>
                  <PlaygroundExplanations key={selectedHistory.id} source={selectedHistory}
                    canRun={canRun} canCancel={canCancel}
                    shapSupported={model.features.length <= 64} />
                </details>}
              </>}
            </div>}
          </div>)}
        </div>
        {historyCursor && <button type="button" className="secondary"
          disabled={historyLoading} onClick={() => void loadHistory(true)}>
          이전 실행 더 조회하기
        </button>}
      </div>
    </details>
  </section>;
}
