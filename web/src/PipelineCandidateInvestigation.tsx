/** Pipeline 후보 한 건의 관측값과 명시적으로 계산한 설명 결과를 분석한다. */
import { useEffect, useRef, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { DataChart } from "./DataChart";
import type { MLResourceKind } from "./PlatformNavigation";
import type { MLExplanationJob, MLExplanationJobSummary, ExplanationResult, PipelineCandidates, ModelRevision } from "./types";
import { PipelineShapExplanation } from "./PipelineShapExplanation";
import { activityFeatureLabel } from "./ActivityFeatureLabels";
import { groupActivityFeatures } from "./ActivityFeatureGroups";
import { FeatureValueComparison } from "./FeatureValueComparison";
import { formatObservedAt, jobStatusLabel } from "./display-format";

type Candidate = PipelineCandidates["items"][number];
type ExplanationJobPage = { items: MLExplanationJobSummary[]; next_cursor: string | null };

export function matchingExplanationJob(
  jobs: MLExplanationJobSummary[], scoringResultId: string, rowIndex: number,
  method = "median_reference_feature_replacement",
): MLExplanationJobSummary | undefined {
  return jobs.filter(item => item.spec.scoring_result_id === scoringResultId &&
    item.spec.row_index === rowIndex &&
    (item.spec.method ?? "median_reference_feature_replacement") === method)
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
}
const terminal = new Set(["Succeeded", "Failed", "Cancelled"]);
function numeric(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function format(value: unknown): string {
  const num = numeric(value);
  return num === null ? "미정의" :
    num.toLocaleString("ko-KR", { maximumFractionDigits: 6 });
}

export function SensitivityChart({ result }: { result: ExplanationResult }) {
  const raw = result.output.feature_sensitivities;
  const entries = Array.isArray(raw) ? raw as Array<Record<string, unknown>> : [];
  const ranked = entries.filter(entry => typeof entry.feature === "string" &&
    numeric(entry.anomaly_score_delta) !== null)
    .sort((a, b) => Math.abs(numeric(b.anomaly_score_delta)!) -
      Math.abs(numeric(a.anomaly_score_delta)!)).slice(0, 12);
  if (!ranked.length) return <p>시각화 가능한 점수 민감도 근거가 없습니다.</p>;
  const shown = [...ranked].reverse();
  const option = {
    aria: { enabled: true },
    // 비교에 필요한 정밀도만 보이고 원래 정밀도는 아래 표에서 확인한다.
    tooltip: { trigger: "axis", formatter: (params: Array<{ dataIndex: number }>) => {
      const item = shown[params[0]?.dataIndex ?? -1];
      if (!item) return "";
      const name = String(item.feature);
      const label = activityFeatureLabel(name);
      const delta = numeric(item.anomaly_score_delta);
      return `${label}${label !== name ? ` (${name})` : ""}<br/>기준값 치환 시 점수 변화 ${
        delta === null ? "미정의" : (delta > 0 ? "+" : "") + delta.toLocaleString("ko-KR", { maximumFractionDigits: 6 })}`;
    } },
    grid: { left: "24%", right: "8%", top: 18, bottom: 32 },
    xAxis: { type: "value" as const, name: "점수 변화" },
    yAxis: { type: "category" as const,
      data: shown.map(x => activityFeatureLabel(String(x.feature))),
      axisLabel: { width: 120, overflow: "truncate" as const } },
    series: [{ type: "bar" as const, name: "기준값 치환 시 점수 변화",
      data: shown.map(x => numeric(x.anomaly_score_delta)) }],
  };
  return <>
    <DataChart option={option} ariaLabel="선택한 후보의 Feature별 참조 중앙값 치환에 따른 이상 점수 민감도" />
    <p className="hint">실제 모델에서 각 Feature를 Train 참조 중앙값으로 하나씩 치환했을 때의 점수 차이입니다.
      SHAP·가법 기여도·원인 판정이 아닙니다. 양수는 기준값 치환 시 이상 점수가 낮아진 경우입니다.</p>
    <details>
      <summary>전체 Feature의 실제 값·참조값·점수 변화 표</summary>
      <div className="table-scroll"><table>
        <thead><tr><th>Feature</th><th>관측값</th><th>Train 참조 중앙값</th>
          <th>치환 시 점수</th><th>원래 점수와의 차이</th></tr></thead>
        <tbody>{entries.map((item, i) => <tr key={String(item.feature) + ":" + i}>
          <th>{activityFeatureLabel(String(item.feature))}
            {activityFeatureLabel(String(item.feature)) !== String(item.feature) &&
              <small className="activity-feature-original">{String(item.feature)}</small>}</th>
          <td>{item.missing_imputed ? "결측값 대체" : format(item.actual_value)}</td>
          <td>{format(item.reference_median)}</td>
          <td>{format(item.score_if_replaced)}</td>
          <td>{format(item.anomaly_score_delta)}</td>
        </tr>)}</tbody>
      </table></div>
    </details>
  </>;
}

export function PipelineCandidateInvestigation({
  candidate, scoringResultId, modelRevisionId, canRun, canCancel, onOpenResource,
  observationMode = false, scoreOrder, isDefaultObservation = false, onTryObservation,
}: {
  candidate: Candidate; scoringResultId: string; modelRevisionId: string | null;
  scoreOrder?: number;
  canRun: boolean; canCancel: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  observationMode?: boolean;
  isDefaultObservation?: boolean;
  onTryObservation?: (
    features: Record<string, number | null>, rowIndex: number, score: number,
  ) => string | null;
}) {
  const subject = observationMode ? "관측값" : "후보";
  const [model, setModel] = useState<ModelRevision | null>(null);
  const [modelError, setModelError] = useState("");
  const [experimentError, setExperimentError] = useState("");
  const [modelReload, setModelReload] = useState(0);
  const [job, setJob] = useState<MLExplanationJob | null>(null);
  const [result, setResult] = useState<ExplanationResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [lookupRetry, setLookupRetry] = useState(0);
  const [previousLookup, setPreviousLookup] = useState<"loading" | "ready" | "error">("loading");
  const [olderJobsExist, setOlderJobsExist] = useState(false);
  const submitted = useRef(false);
  useEffect(() => {
    if (!modelRevisionId) return;
    let active = true;
    setModel(null); setModelError("");
    void request<ModelRevision>("/api/ml/models/" + encodeURIComponent(modelRevisionId))
      .then(next => {
        if (!active) return;
        if (next.id !== modelRevisionId) setModelError("선택한 분석과 모델이 일치하지 않습니다.");
        else setModel(next);
      })
      .catch(reason => { if (active) setModelError(formatError(reason)); });
    return () => { active = false; };
  }, [modelRevisionId, modelReload]);
  const medians = model?.runtime?.feature_reference_medians;
  const referenceMap = model && Array.isArray(medians) &&
    medians.length === model.features.length &&
    medians.every(v => typeof v === "number" && Number.isFinite(v))
    ? new Map(model.features.map((name, i) => [name, medians[i] as number])) : null;
  // 집계 Feature의 표시 그룹이며, 모델 중요도나 원인 순위가 아니다.
  const activityGroups = groupActivityFeatures(candidate.features);
  // 이 세 지표는 결과 화면의 고정 미리보기이며 중요도나 이상 원인 순위가 아니다.
  const previewNames = ["io_bytes_per_sec", "net_bytes_per_sec", "cnt_file_create"]
    .filter(name => Object.prototype.hasOwnProperty.call(candidate.features, name));
  const previewFeatures = previewNames.length > 0
    ? previewNames : Object.keys(candidate.features).slice(0, 3);
  function referenceLabel(name: string): string {
    if (referenceMap?.has(name)) return format(referenceMap.get(name));
    if (!modelRevisionId) return "모델 판본 없음";
    if (modelError) return "조회 오류";
    if (!model) return "조회 중";
    return "비교 기준 없음";
  }
  function referenceDifference(name: string, value: number | null): string | null {
    const reference = referenceMap?.get(name);
    if (typeof value !== "number" || !Number.isFinite(value) ||
        typeof reference !== "number" || !Number.isFinite(reference)) return null;
    const difference = value - reference;
    if (!Number.isFinite(difference)) return null;
    if (difference === 0) return "학습 중앙값과 같음";
    const magnitude = Math.abs(difference);
    const amount = magnitude < 0.000001 ? magnitude.toExponential(2) : format(magnitude);
    return `학습 중앙값 대비 ${difference > 0 ? "+" : "−"}${amount}`;
  }
  useEffect(() => {
    let active = true;
    setPreviousLookup("loading");
    // 기존 권한 계약을 사용하는 최근 Job 목록에서 동일 후보의 설명 기록을 복원한다.
    void request<ExplanationJobPage>("/api/ml/explanations/jobs?limit=100")
      .then(page => {
        if (!active || submitted.current) return;
        const previous = matchingExplanationJob(page.items, scoringResultId, candidate.row_index);
        if (!previous) {
          setOlderJobsExist(Boolean(page.next_cursor));
          setPreviousLookup("ready");
          return;
        }
        setOlderJobsExist(false);
        return request<MLExplanationJob>(
          "/api/ml/explanations/jobs/" + encodeURIComponent(previous.id)
        ).then(restored => {
          if (!active || submitted.current) return;
          if (restored.spec.scoring_result_id !== scoringResultId ||
              restored.spec.row_index !== candidate.row_index) {
            throw new Error("이전 설명 Job의 Scoring Result와 후보 행 번호가 일치하지 않습니다.");
          }
          setJob(restored); setPreviousLookup("ready");
        });
      })
      .catch(() => { if (active && !submitted.current) setPreviousLookup("error"); });
    return () => { active = false; };
  }, [scoringResultId, candidate.row_index, lookupRetry]);
  useEffect(() => {
    if (!job || terminal.has(job.status) || error) return;
    let active = true;
    const interval = setInterval(() => {
      void request<MLExplanationJob>("/api/ml/explanations/jobs/" + encodeURIComponent(job.id))
        .then(next => { if (active) { setJob(next); setError(""); } })
        .catch(e => { if (active) setError(formatError(e)); });
    }, 2000);
    return () => { active = false; clearInterval(interval); };
  }, [job?.id, job?.status, error, retry]);
  useEffect(() => {
    const resultId = job?.result?.explanation_result_id;
    if (job?.status !== "Succeeded") return;
    if (typeof resultId !== "string") {
      setError("완료된 설명 Job에서 설명 결과 판본을 찾지 못했습니다.");
      return;
    }
    let active = true;
    void request<ExplanationResult>("/api/ml/explanations/" + encodeURIComponent(resultId))
      .then(next => {
        if (!active) return;
        if (next.scoring_result_id !== scoringResultId ||
            next.row_index !== candidate.row_index) {
          setError("설명 판본과 선택한 후보의 Scoring 행 번호가 일치하지 않습니다.");
          return;
        }
        setResult(next); setError("");
      })
      .catch(e => { if (active) setError(formatError(e)); });
    return () => { active = false; };
  }, [job?.id, job?.status, job?.result?.explanation_result_id, scoringResultId, candidate.row_index, retry]);
  async function start() {
    if (!canRun || busy || previousLookup !== "ready" ||
        (job && !terminal.has(job.status))) return;
    submitted.current = true;
    setBusy(true); setJob(null); setResult(null); setError("");
    try {
      setJob(await submitJob<MLExplanationJob>("/api/ml/explanations/jobs", {
        scoring_result_id: scoringResultId, row_index: candidate.row_index,
      }));
    } catch (e) { setError(formatError(e)); }
    finally { setBusy(false); }
  }
  async function cancelExplanation() {
    if (!job || terminal.has(job.status) || job.cancel_requested || busy || !canCancel) return;
    const requestedJobId = job.id;
    setBusy(true); setError("");
    try {
      const updated = await request<MLExplanationJob>(
        "/api/ml/explanations/jobs/" + encodeURIComponent(requestedJobId) + "/cancel",
        { method: "POST" },
      );
      setJob(previous => previous?.id === requestedJobId ? updated : previous);
    } catch (e) { setError(formatError(e)); }
    finally { setBusy(false); }
  }
  return <section className="pipeline-candidate-inspection"
    aria-label={observationMode
      ? isDefaultObservation ? "기본 표시 관측값 분석" : "선택한 관측값 분석"
      : "선택한 후보 분석"}>
    <h5 id="pipeline-candidate-detail-heading" tabIndex={-1}>
      {observationMode ? (isDefaultObservation ? "점수순 첫 번째 관측값 미리보기" : "선택한 관측값")
        : `선택한 후보 #${candidate.row_index.toLocaleString("ko-KR")}`}</h5>
    <p>이상 점수 <strong>{format(candidate.anomaly_score)}</strong>
      {typeof scoreOrder === "number" ? ` · 점수순 ${scoreOrder.toLocaleString("ko-KR")}번째` : ""}
      {" · "}높을수록 모델이 더 특이하게 본 {subject}입니다.</p>
    {observationMode && !candidate.observed_at && !candidate.observation_id
      ? <p className="observation-source-missing">관측 시각·원본 관측치 ID 없음
          <strong>결과 내부 행 #{candidate.row_index.toLocaleString("ko-KR")}</strong>
          <small>이 번호는 해당 Scoring Result에서만 유효하며 원본 이벤트의 ID가 아닙니다.</small>
        </p>
      : observationMode
        ? <p className="observation-meta">
            {candidate.observed_at && <span title={candidate.observed_at}>
              관측 시각 <strong>{formatObservedAt(candidate.observed_at)}</strong></span>}
            {candidate.observation_id && <span>원본 관측치 ID <strong>{candidate.observation_id}</strong></span>}
            <span>결과 행 #{candidate.row_index.toLocaleString("ko-KR")}</span>
            {!candidate.observation_id && <span>원본 관측치 ID 없음</span>}
          </p>
        : <dl className="pipeline-candidate-observation">
          <div><dt>관측 시각</dt><dd title={candidate.observed_at ?? undefined}>{candidate.observed_at
            ? formatObservedAt(candidate.observed_at) : "시각 근거 없음"}</dd></div>
          <div><dt>관측치 ID (UUIDv4)</dt><dd>{candidate.observation_id ?? "별도 관측치 ID 없음"}</dd></div>
          <div><dt>결과 내부 행 번호</dt><dd>{candidate.row_index.toLocaleString("ko-KR")}</dd></div>
        </dl>}
    {!observationMode && <p className="hint">활동 Feature 관측치는 원시 이벤트 한 건이라는 뜻이 아니며,
      관측 시각은 고유 ID가 아닙니다. UUIDv4 형식만으로 개인정보 비식별성이 보장되지는 않습니다.</p>}
    {observationMode && onTryObservation && canRun && <div className="observation-try-experiment">
      <p>이 관측값의 Feature를 가져와 숫자를 바꿔보세요. 원래 분석 결과는 그대로 유지됩니다.</p>
      <button type="button" className="secondary" onClick={() => {
        setExperimentError(onTryObservation(candidate.features, candidate.row_index,
          candidate.anomaly_score) ?? "");
      }}>이 관측값으로 실험하기 →</button>
      {experimentError && <p role="alert">{experimentError}</p>}
    </div>}
    {observationMode && <PipelineShapExplanation candidate={candidate} scoringResultId={scoringResultId}
      modelRevisionId={modelRevisionId} canRun={canRun} canCancel={canCancel}
      onOpenResource={onOpenResource} />}
    {observationMode && <section className="observation-activity" aria-label="관측된 활동 특징">
      <div className="observation-activity-heading">
        <h6>활동 특징</h6>
        <p>선택한 관측값의 실제 활동 지표입니다. 학습 중앙값은 비교 기준이며 정상 범위나 사고 원인을 의미하지 않습니다.</p>
      </div>
      <div className="observation-activity-highlights">
        {previewFeatures.map(name => <div key={name} className="observation-activity-highlight">
          <span title={name}>{activityFeatureLabel(name)}</span>
          <FeatureValueComparison actual={candidate.features[name]}
            reference={referenceMap?.get(name)} />
          {referenceDifference(name, candidate.features[name]) &&
            <small className="observation-activity-difference">
              {referenceDifference(name, candidate.features[name])}
            </small>}
        </div>)}
      </div>
      {!modelRevisionId && <p className="hint">학습 모델 판본 참조가 없어 중앙값을 비교할 수 없습니다.</p>}
      {modelRevisionId && !model && !modelError &&
        <p role="status">학습 참조 중앙값을 조회하는 중입니다.</p>}
      {modelError && <div className="pipeline-readiness-blocked" role="alert">
        <p>학습 중앙값 조회 실패: {modelError}</p>
        <button type="button" className="secondary" onClick={() => setModelReload(n => n + 1)}>
          학습 참조값 다시 조회
        </button>
      </div>}
      {model && !referenceMap && <p className="hint">이 모델에는 비교 가능한 학습 중앙값이 없습니다.</p>}
      <details className="observation-activity-all">
      <summary>전체 Feature {Object.keys(candidate.features).length}개 보기</summary>
      <div className="observation-activity-group-intro">
        <strong>활동별 지표</strong>
        <span>전체 {Object.keys(candidate.features).length}개 · 아래 그룹별 실제값과 학습 참조값</span>
      </div>
      <div className="observation-activity-groups">
        {activityGroups.map(group => {
          // 상단 고정 지표와 각 그룹의 미리보기에 같은 Feature를 반복 노출하지 않는다.
          const remainingGroup = group.entries.filter(([name]) => !previewFeatures.includes(name));
          const shown = remainingGroup.slice(0, 1);
          const hidden = remainingGroup.slice(1);
          return <section key={group.id} className="observation-activity-group">
          <div className="observation-activity-group-heading">
            <strong>{group.title}</strong><span>{group.entries.length}개 입력값</span>
          </div>
          <p>{group.description}</p>
          {shown.length === 0 && <p className="hint">이 그룹에서 표시한 지표는 상단 활동 특징에 있습니다.</p>}
          <div className="observation-activity-figures">
            {shown.map(([name, value]) =>
              <div key={name} className="observation-activity-figure">
                <span title={name}>{activityFeatureLabel(name)}</span>
                <FeatureValueComparison actual={value} reference={referenceMap?.get(name)} />
                {referenceDifference(name, value) &&
                  <small className="observation-activity-difference">{referenceDifference(name, value)}</small>}
              </div>)}
          </div>
          {hidden.length > 0 && <details className="observation-activity-extra">
            <summary>추가 지표 {hidden.length}개 확인</summary>
            <div className="table-scroll"><table>
              <thead><tr><th>Feature</th><th>관측값</th><th>Train 중앙값</th><th>중앙값 대비</th></tr></thead>
              <tbody>{hidden.map(([name, value]) => <tr key={name}>
                <th title={name}>{activityFeatureLabel(name)}</th><td>{format(value)}</td>
                <td>{referenceLabel(name)}</td>
                <td>{referenceDifference(name, value) ?? "비교 불가"}</td>
              </tr>)}</tbody>
            </table></div>
          </details>}
        </section>;
        })}
      </div>
      <p className="hint">학습 중앙값과의 차이는 정상 범위·Feature 중요도·이상 원인을 의미하지 않습니다.</p>
      </details>
    </section>}
    {!observationMode && <details className="pipeline-feature-details">
      <summary>전체 {Object.keys(candidate.features).length}개 Feature 관측값·학습 참조값 비교</summary>
      <p className="hint">Train Feature별 중앙값은 비교 기준입니다.
        값의 차이만으로 원인이나 모델 기여도를 판단하지 않습니다.</p>
    {!modelRevisionId && <p className="hint">학습 모델 판본을 확인할 수 없어 참조값 비교가 제한됩니다.</p>}
    {modelRevisionId && !model && !modelError &&
      <p role="status">학습 참조값을 조회하고 있습니다.</p>}
    {modelError && <div role="alert" className="pipeline-readiness-blocked">
      <p>학습 참조값을 불러오지 못했습니다: {modelError}</p>
      <button type="button" className="secondary" onClick={() => setModelReload(n => n + 1)}>
        다시 조회
      </button>
    </div>}
    {model && !referenceMap && <p className="hint">이전 모델에는 비교 가능한 학습 중앙값이 없습니다.</p>}
    <div className="table-scroll pipeline-candidate-comparison"><table>
      <thead><tr><th>Feature</th><th>관측값</th><th>Train 참조 중앙값</th></tr></thead>
      <tbody>{Object.entries(candidate.features).map(([name, value]) =>
        <tr key={name}><th>{activityFeatureLabel(name)}
          {activityFeatureLabel(name) !== name &&
            <small className="activity-feature-original">{name}</small>}</th><td>{format(value)}</td>
          <td>{referenceMap?.has(name) ? format(referenceMap.get(name))
            : modelError ? "조회 오류"
            : !modelRevisionId ? "판본 없음" : !model ? "조회 중" : "근거 없음"}</td></tr>)}</tbody>
    </table></div>
    </details>}
    {!observationMode && <PipelineShapExplanation candidate={candidate} scoringResultId={scoringResultId}
      modelRevisionId={modelRevisionId} canRun={canRun} canCancel={canCancel}
      onOpenResource={onOpenResource} />}
    <section className="pipeline-sensitivity" aria-labelledby={`sensitivity-${candidate.row_index}`}>
    <h6 id={`sensitivity-${candidate.row_index}`}>Feature 하나를 학습 중앙값으로 바꾸면 점수가 어떻게 달라지나요?
      <small> 치환 민감도</small></h6>
    {previousLookup === "loading" && <p role="status">
      이 {subject}의 기존 민감도 계산을 확인하고 있습니다.
    </p>}
    {previousLookup === "error" && <div className="pipeline-readiness-blocked" role="alert">
      <p>이전 민감도 계산을 확인하지 못했습니다. 중복 실행을 막기 위해 다시 조회해 주세요.</p>
      <button type="button" className="secondary" onClick={() =>
        setLookupRetry(n => n + 1)}>이전 계산 다시 조회</button>
    </div>}
    {(!job || job.status === "Failed" || job.status === "Cancelled") && canRun &&
      <button type="button" className="secondary pipeline-sensitivity-start" onClick={() => void start()}
        disabled={busy || previousLookup !== "ready"}>
        {busy ? "민감도 계산 요청 중…" : job ? "민감도 다시 계산 →" : "치환 민감도 계산 →"}
      </button>}
    {!job && previousLookup === "ready" && olderJobsExist && <p className="hint">
      최근 100건의 민감도 계산에서 같은 {subject}를 찾지 못했습니다.
      더 오래된 결과는 작업 이력의 설명 작업에서 확인할 수 있습니다.
    </p>}
    {job && previousLookup === "ready" && <p className="hint">
      이 {subject}에 대해 이전에 계산한 민감도입니다. 다른 {subject}를 살펴본 뒤에도 다시 확인할 수 있습니다.
    </p>}
    {!canRun && <p className="hint">현재 계정은 민감도 계산을 요청할 권한이 없습니다.</p>}
    {job && <p role="status">민감도 계산: {jobStatusLabel(job.status)}
      {job.cancel_requested ? " · 취소 요청됨 (Worker 확인 중)" :
        job.status === "Running" || job.status === "Queued" ? " · 계산 중" : ""}
      {job.error_code ? " · 오류 " + job.error_code : ""}</p>}
    {job && !terminal.has(job.status) && canCancel && <button
      type="button" className="secondary" disabled={busy || job.cancel_requested}
      onClick={() => void cancelExplanation()}>
      {job.cancel_requested ? "민감도 계산 취소 요청 처리 중" : "민감도 계산 취소 요청"}
    </button>}
    {job?.status === "Failed" && <p role="alert">민감도 계산이 실패했습니다. 작업 이력에서 오류 근거를 확인하세요.</p>}
    {job?.status === "Cancelled" && <p>민감도 계산이 취소됐습니다.</p>}
    {error && <div role="alert" className="pipeline-readiness-blocked">{error}
      {job && <button className="secondary" type="button" onClick={() => {
        setError(""); setRetry(n => n + 1);
      }}>설명 근거 다시 조회하기</button>}</div>}
    {result && <>
      <SensitivityChart result={result} />
      <button type="button" className="secondary" onClick={() =>
        onOpenResource("explanation", result.id)}>설명 결과 판본 보기 →</button>
    </>}
    </section>
  </section>;
}
