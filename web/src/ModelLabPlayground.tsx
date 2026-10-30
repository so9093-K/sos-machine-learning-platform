/** Dataset 등록 없이 실제 Worker Playground Job으로 한 관측치를 점수화한다. */
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityProfile, profileFeatureNames } from "./ActivityProfile";
import { formatError, request, submitJob } from "./api";
import { activityFeatureLabel } from "./ActivityFeatureLabels";
import { groupActivityFeatures } from "./ActivityFeatureGroups";
import { formatFeatureValue } from "./feature-format";
import { FeatureResultDetails } from "./FeatureResultDetails";
import { PlaygroundResult, formatPlaygroundScore as formatScore, type PlaygroundComparison } from "./PlaygroundResult";
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

/** Dataset 관측값은 새로운 Job을 만들지 않고 편집 초깃값으로만 전달한다. */
export type ObservationExperiment = {
  id: string;
  modelRevisionId: string;
  scoringResultId: string;
  rowIndex: number;
  originalScore: number;
  fields: Record<string, string>;
};

/** 관측값 전체가 선택 모델의 Playground 입력 계약에 맞을 때에만 전달한다. */
export function observationInputForPlayground(
  model: ModelRevision, values: Record<string, number | null>,
): { fields: Record<string, string> | null; invalid: string[] } {
  const dtypes = model.runtime?.feature_dtypes;
  const invalid: string[] = [];
  const fields: Record<string, string> = {};
  model.features.forEach((name, index) => {
    const value = values[name];
    const dtype = Array.isArray(dtypes) ? String(dtypes[index] ?? "") : "";
    const raw = typeof value === "number" && Number.isFinite(value) ? String(value) : "";
    if (!playgroundNumberValid(raw, dtype)) invalid.push(name);
    else fields[name] = raw;
  });
  return { fields: invalid.length ? null : fields, invalid };
}

/** 관측값을 가져오기 전의 Job을 새 실험 결과처럼 노출하지 않는다. */
export function playgroundJobVisibleAfterImport(
  source: ObservationExperiment | null, jobAtImport: string | null, currentJobId: string | null,
): boolean {
  return source === null || (currentJobId !== null && currentJobId !== jobAtImport);
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

export type PlaygroundRun = { modelRevisionId: string; features: Record<string, number | null>; score: number };

export function ModelLabPlayground({ model, canRun, canCancel, observationInput, onBackToAnalysis, onLatestRun }: {
  model: ModelRevision; canRun: boolean; canCancel: boolean;
  observationInput?: ObservationExperiment | null;
  onBackToAnalysis?: () => void;
  /** 마지막으로 점수가 확인된 실제 제출 입력. 편집 중인 값이 아니다. */
  onLatestRun?: (run: PlaygroundRun) => void;
}) {
  const reference = exampleValues(model);
  const [inputSearch, setInputSearch] = useState("");
  const [inputFilter, setInputFilter] = useState("all");
  const [inputPage, setInputPage] = useState(0);
  const [activeFeature, setActiveFeature] = useState<string>();
  const [importedSource, setImportedSource] = useState<ObservationExperiment | null>(null);
  const [fromHistory, setFromHistory] = useState(false);
  const [importedAfterJobId, setImportedAfterJobId] = useState<string | null>(null);
  const appliedObservation = useRef<string | null>(null);
  const [fields, setFields] = useState<Record<string, string>>(() =>
    reference ?? Object.fromEntries(model.features.map(name => [name, ""])));
  const [editBaseline, setEditBaseline] = useState<Record<string, string>>(() =>
    reference ?? Object.fromEntries(model.features.map(name => [name, ""])));
  const [job, setJob] = useState<MLPlaygroundJob | null>(null);
  const [lastScore, setLastScore] = useState<number | null>(null);
  const [latestSuccessfulJob, setLatestSuccessfulJob] = useState<MLPlaygroundJob | null>(null);
  const [comparison, setComparison] = useState<PlaygroundComparison | null>(null);
  const [shapVisited, setShapVisited] = useState(false);
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
    setFromHistory(true);
    if (running || !restored.spec.features) return;
    const saved = restored.spec.features as Record<string, unknown>;
    const restoredFields = Object.fromEntries(model.features.map(name => [
      name, saved[name] == null ? "" : String(saved[name]),
    ]));
    setFields(restoredFields);
    setEditBaseline(restoredFields);
    setImportedSource(null);
    setImportedAfterJobId(null);
    if (restored.status === "Succeeded" && typeof restored.result?.anomaly_score === "number" &&
        Number.isFinite(restored.result.anomaly_score)) setLatestSuccessfulJob(restored);
    setInputSearch(""); setInputFilter("all"); setInputPage(0);
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

  const latestRunHandler = useRef(onLatestRun);
  latestRunHandler.current = onLatestRun;
  useEffect(() => {
    const score = latestSuccessfulJob?.result?.anomaly_score;
    const submitted = latestSuccessfulJob?.spec.features;
    if (!latestSuccessfulJob || typeof score !== "number" || !submitted || typeof submitted !== "object") return;
    latestRunHandler.current?.({ modelRevisionId: model.id, score,
      features: submitted as Record<string, number | null> });
  }, [latestSuccessfulJob?.id, model.id]);

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
  useEffect(() => {
    if (!observationInput || observationInput.modelRevisionId !== model.id ||
        appliedObservation.current === observationInput.id || running) return;
    // 실행 중 입력을 바꾸지 않으며, 새 관측값을 가져와도 Job은 자동 제출하지 않는다.
    appliedObservation.current = observationInput.id;
    setFields(observationInput.fields);
    setEditBaseline(observationInput.fields);
    setInputSearch(""); setInputFilter("all"); setInputPage(0);
    setImportedSource(observationInput);
    // 과거 Job 결과를 새 관측값 실험의 결과로 표시하지 않는다.
    setImportedAfterJobId(job?.id ?? null);
  }, [observationInput, model.id, running, job?.id]);
  const submittedFeatures = job?.spec.features;
  const submittedInput = submittedFeatures && typeof submittedFeatures === "object" &&
    !Array.isArray(submittedFeatures)
    ? submittedFeatures as Record<string, unknown> : null;
  const matchesCompletedInput = job?.status === "Succeeded" && lastScore !== null &&
    playgroundInputMatchesResult(fields, submittedInput, model.features);
  const editedNames = playgroundChangedFeatures(fields, editBaseline, model.features);
  const observationEdits = importedSource
    ? playgroundChangedFeatures(fields, importedSource.fields, model.features) : [];
  const showCurrentJob = playgroundJobVisibleAfterImport(importedSource, importedAfterJobId, job?.id ?? null);
  const newJobAfterImport = Boolean(importedSource && showCurrentJob);
  const inputGroups = groupActivityFeatures(
    Object.fromEntries(model.features.map(name => [name, null])));
  const searchedNames = model.features.filter(name => {
    const term = inputSearch.trim().toLocaleLowerCase();
    const matchesSearch = [name, activityFeatureLabel(name)].some(value => value.toLocaleLowerCase().includes(term));
    const matchesFilter = inputFilter === "all" ||
      (inputFilter === "changed" && (importedSource ? observationEdits : editedNames).includes(name)) ||
      (inputFilter === "invalid" && invalid.some(item => item.name === name)) ||
      inputGroups.find(group => group.id === inputFilter)?.entries.some(([entry]) => entry === name);
    return matchesSearch && matchesFilter;
  });
  const inputPageCount = Math.max(1, Math.ceil(searchedNames.length / 4));
  const currentInputPage = Math.min(inputPage, inputPageCount - 1);
  const shownInputs = searchedNames.slice(currentInputPage * 4, (currentInputPage + 1) * 4);
  const explanationAvailable = referenceMedians.length === model.features.length &&
    referenceMedians.every(value => typeof value === "number" && Number.isFinite(value));
  async function execute() {
    if (!canRun || running || invalid.length || submitting.current) return;
    submitting.current = true;
    setSending(true); setError("");
    const anchor: PlaygroundComparison | null = importedSource ? {
      modelId: model.id, label: "원래 관측값", score: importedSource.originalScore,
      fields: Object.fromEntries(model.features.map(name => [name, Number(importedSource.fields[name])])),
    } : latestSuccessfulJob ? {
      modelId: model.id, label: "이전 성공 입력", score: Number(latestSuccessfulJob.result?.anomaly_score),
      fields: latestSuccessfulJob.spec.features as Record<string, number>,
    } : reference ? {
      modelId: model.id, label: "학습 초기 입력", score: null,
      fields: Object.fromEntries(model.features.map(name => [name, Number(reference[name])])),
    } : null;
    try {
      const created = await submitJob<MLPlaygroundJob>("/api/ml/playground/jobs", {
        model_revision_id: model.id,
        features: Object.fromEntries(input.map(item => [item.name, item.value])),
      });
      // 제출이 실패한 경우에는 이전 결과와 비교 문맥을 잃지 않는다.
      setComparison(anchor);
      setLastScore(null);
      setJob(created);
      // 동일 멱등 키 재제출 시 이미 성공한 Job이 응답할 수 있다.
      // 완료 응답을 Polling 전용 경로에만 기대하면 점수가 사라진다.
      if (created.status === "Succeeded") {
        const score = created.result?.anomaly_score;
        if (typeof score === "number" && Number.isFinite(score)) {
          setLastScore(score);
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
  const allInputs = useRef<HTMLDetailsElement>(null);
  const profileAnchor = importedSource?.fields;
  const profileNames = useMemo(() => profileFeatureNames(model, Object.fromEntries(
    model.features.map(name => {
      const raw = profileAnchor?.[name];
      return [name, raw !== undefined && raw !== null && String(raw).trim() && Number.isFinite(Number(raw)) ? Number(raw) : null];
    }))), [model.id, importedSource?.id]);
  function editFeature(name?: string) {
    setInputSearch(name ?? ""); setInputFilter("all"); setInputPage(0);
    if (allInputs.current) allInputs.current.open = true;
    requestAnimationFrame(() => {
      const target = allInputs.current?.querySelector<HTMLInputElement>(name ? 'input[type="number"]' : 'input[type="search"]');
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ block: "center", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    });
  }
  const featureInput = (name: string) => <input type="number" inputMode="decimal"
    step={/^U?Int/.test(featureDtypes[model.features.indexOf(name)] ?? "") ? 1 : "any"}
    value={fields[name] ?? ""} disabled={running}
    onFocus={() => setActiveFeature(name)}
    aria-invalid={!playgroundNumberValid(fields[name] ?? "", featureDtypes[model.features.indexOf(name)] ?? "")}
    onChange={event => setFields(current => ({ ...current, [name]: event.target.value }))}
    aria-label={activityFeatureLabel(name) + " 입력값"} />;
  const featureField = (name: string) => <label className="model-lab-feature" key={name}>
    <span title={name + " · " + (featureDtypes[model.features.indexOf(name)] ?? "자료형 미확인")}>
      {activityFeatureLabel(name)}</span>
    {featureInput(name)}
    {reference && <small title={reference[name]}>학습 기준값 {formatFeatureValue(name, Number(reference[name]))}
      {(name === "io_bytes_per_sec" || name === "net_bytes_per_sec") && " · 입력 단위 B/s"}</small>}
  </label>;
  return <section className="panel model-lab-playground" tabIndex={-1} aria-label="직접 모델 점수 계산">
    <div className="model-lab-playground-head">
      <div><h3>입력 실험</h3>
        <p>활동 특징을 수정한 뒤 점수를 계산해 모델의 반응을 비교합니다.</p></div>
    </div>
    {observationInput && appliedObservation.current !== observationInput.id && running &&
      <p className="hint" role="status">진행 중인 점수 계산이 끝나면 선택한 관측값을 입력에 가져옵니다.</p>}
    {importedSource && <div className="model-lab-observation-source" role="status">
      <strong>Dataset 관측값에서 가져온 입력</strong>
      <p>원래 결과 행 #{importedSource.rowIndex.toLocaleString("ko-KR")} · 저장된 이상 점수 {formatScore(importedSource.originalScore)}.
        값을 바꿔 다시 계산해도 원래 Dataset 결과는 바뀌지 않습니다.</p>
      {onBackToAnalysis && <button type="button" className="secondary"
        onClick={onBackToAnalysis}>Dataset 분석 복귀 →</button>}
    </div>}
    <div className="model-lab-workspace">
      <div className="model-lab-input-pane">
    <div className="playground-input-heading">
      <h4>실험 입력</h4>
      {reference && <button type="button" className="ghost" disabled={running}
        onClick={() => {
          setFields(reference); setImportedSource(null); setImportedAfterJobId(null);
          setEditBaseline(reference);
          setFromHistory(false);
          setInputSearch(""); setInputFilter("all"); setInputPage(0);
        }}>
        학습 초기값 복원</button>}
    </div>
    <p className="hint">{importedSource ? "가져온 관측값에서 바꿀 항목을 고르세요."
      : fromHistory ? "이전 계산 기록에서 가져온 입력입니다. 값을 수정해 새 계산과 비교할 수 있습니다."
      : reference ? "초깃값은 학습 중앙값을 조합한 합성 입력입니다(정수 항목 반올림). 실제 관측값이나 정상 활동 예제가 아닙니다."
      : "초기 기준값이 없는 모델입니다. 모든 필수 항목을 입력해 주세요."}</p>
    <ActivityProfile model={model} names={profileNames} editing changedNames={editedNames}
      activeName={activeFeature} onEdit={editFeature}
      values={Object.fromEntries(input.map(item => [item.name, item.valid ? item.value : null]))}
      control={name => <div className="activity-profile-editor">{featureInput(name)}
        {(name === "io_bytes_per_sec" || name === "net_bytes_per_sec") && <small>B/s</small>}
        {reference && <button type="button" className="ghost" disabled={running || fields[name] === reference[name]}
          title={`학습 중앙값 기반 ${reference[name]} · 정수 항목 반올림`}
          aria-label={activityFeatureLabel(name) + " 학습 기준값 적용"}
          onClick={() => setFields(current => ({ ...current, [name]: reference[name] }))}>학습 기준값 적용</button>}
      </div>} />
    <details className="playground-all-inputs" ref={allInputs}>
      <summary>전체 입력 편집 · {model.features.length}개 항목</summary>
    <div className="playground-input-tools">
      <label>항목 찾기<input type="search" value={inputSearch} placeholder="예: 파일 쓰기"
        onChange={event => { setInputSearch(event.target.value); setInputPage(0); }} /></label>
      <label>표시 항목<select value={inputFilter}
        onChange={event => { setInputFilter(event.target.value); setInputPage(0); }}>
        <option value="all">전체 {model.features.length}개</option>
        <option value="changed">수정한 항목 {editedNames.length}개</option>
        {invalid.length > 0 && <option value="invalid">입력 확인 {invalid.length}개</option>}
        {inputGroups.map(group => <option key={group.id} value={group.id}>{group.title}</option>)}
      </select></label>
    </div>
    <div className="model-lab-input-grid">{shownInputs.map(featureField)}</div>
    {shownInputs.length === 0 && <p className="hint">조건에 맞는 입력 항목이 없습니다.</p>}
    <div className="feature-result-pages">
      <span role="status">{searchedNames.length}개 중 {shownInputs.length ? currentInputPage * 4 + 1 : 0}–{currentInputPage * 4 + shownInputs.length}개</span>
      {inputPageCount > 1 && <>
        <button type="button" className="secondary" disabled={currentInputPage === 0}
          onClick={() => setInputPage(currentInputPage - 1)}>이전 입력</button>
        <button type="button" className="secondary" disabled={currentInputPage === inputPageCount - 1}
          onClick={() => setInputPage(currentInputPage + 1)}>다음 입력</button>
      </>}
    </div>
    </details>
    {invalid.length > 0 && <p role="alert" className="model-lab-input-error">
      필수 입력값이나 자료형·정수 범위가 맞지 않는 Feature {invalid.length}개가 있습니다.
      {invalid.length <= 3 && " 확인: " + invalid.map(item => activityFeatureLabel(item.name)).join(", ")}
      <button type="button" className="ghost" onClick={() => editFeature(invalid[0].name)}>입력 오류 확인</button>
    </p>}
      </div>
      <aside className="model-lab-results-pane" aria-label="단일 입력 계산과 결과">
    <h4>실험 결과</h4>
    {(!showCurrentJob || job?.status !== "Succeeded" || lastScore === null) && <p className="model-lab-input-state" role="status">{running
      ? job?.cancel_requested ? "취소 요청 처리 중입니다." : "제출한 입력을 모델에서 계산하는 중입니다."
      : importedSource && !newJobAfterImport
        ? "가져온 입력을 계산할 준비가 됐습니다."
        : job?.status === "Succeeded"
          ? "계산이 완료됐지만 점수 확인이 필요합니다."
          : job?.status === "Failed" || job?.status === "Cancelled"
            ? "마지막 실행에서는 점수를 얻지 못했습니다. 현재 입력값을 계산하려면 다시 실행해 주세요."
            : "입력값을 준비한 뒤 점수를 계산하세요."}</p>}

    {error && <p role="alert">{error}</p>}
    <div className="model-lab-playground-actions">
      <button type="button" onClick={() => void execute()}
        disabled={!canRun || running || invalid.length > 0}>
        {running ? "모델 실행 중…" : "이상 점수 계산 →"}
      </button>
      {importedSource && observationEdits.length > 0 && <button type="button" className="secondary"
        onClick={() => setFields({ ...importedSource.fields })} disabled={running}>
        가져온 관측값 복원</button>}
      {job && !TERMINAL.has(job.status) && canCancel &&
        <button type="button" className="secondary" disabled={job.cancel_requested} onClick={() => void cancel()}>취소 요청</button>}
    </div>
    {job && showCurrentJob && job.status === "Failed" && <p role="alert">점수 계산에 실패했습니다 · {job.error ?? job.error_code ?? "실행 오류"}</p>}
    {job && showCurrentJob && job.status === "Cancelled" && <p role="status">점수 계산이 취소됐습니다.</p>}
    {job && showCurrentJob && job.status === "Succeeded" && lastScore !== null &&
      <PlaygroundResult model={model} job={job} comparison={comparison} matchesInput={Boolean(matchesCompletedInput)}
        onEdit={editFeature} onRestore={values => {
          setFields(Object.fromEntries(model.features.map(name => [name, String(values[name])])));
          setInputSearch(""); setInputFilter("all"); setInputPage(0);
        }} />}
    {showCurrentJob && job?.status === "Succeeded" && (explanationAvailable
      ? <details className="model-lab-explanation-toggle" onToggle={event => {
          if (event.currentTarget.open) setShapVisited(true);
        }}>
          <summary>모델 점수 설명 · SHAP</summary>
          {shapVisited && <PlaygroundExplanations key={job.id} source={job} canRun={canRun}
            canCancel={canCancel} shapSupported={model.features.length <= 64} />}
        </details>
      : <p className="hint">이 모델에는 학습 Feature 중앙값이 없어 현재 설명 방법을 계산할 수 없습니다.
          점수 계산 결과는 정상적으로 이용할 수 있습니다.</p>)}
      </aside>
    </div>
    <details className="model-lab-playground-history" onToggle={event => {
      if (event.currentTarget.open && !historyLoaded) void loadHistory(false);
    }}>
      <summary>이 모델의 이전 계산 결과</summary>
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
                <p>이상 점수 <strong>{formatScore(selectedHistory.result.anomaly_score)}</strong></p>}
              {selectedHistory.status === "Failed" &&
                <p role="status">실패 사유 · {selectedHistory.error ?? "실행 오류"}
                  {selectedHistory.error_code && <small> ({selectedHistory.error_code})</small>}</p>}
              {selectedHistory.status === "Cancelled" && <p>취소된 점수 계산입니다.</p>}
              {!TERMINAL.has(selectedHistory.status) && <p>이 실행은 아직 완료되지 않았습니다.</p>}
              <FeatureResultDetails key={selectedHistory.id}
                summary={`제출한 입력 확인 · ${model.features.length}개 항목`}
                columns={["제출한 값"]} rows={model.features.map(name => ({ feature: name,
                  values: [String((selectedHistory.spec.features as Record<string, unknown>)[name] ?? "값 없음")],
                }))} />
              <button type="button" className="secondary"
                disabled={running}
                onClick={() => useHistoryInput(selectedHistory)}>이 입력으로 다시 계산 준비</button>
              {selectedHistory.status === "Succeeded" && explanationAvailable &&
                <details className="model-lab-explanation-toggle">
                  <summary>이 점수 설명</summary>
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
