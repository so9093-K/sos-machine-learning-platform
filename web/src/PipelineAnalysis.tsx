/** 첫 IF Pipeline에 이미 존재하는 불변 ML 판본으로 구성한 결과 검토 경험. */
import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { DataChart } from "./DataChart";
import { EvaluationSummary } from "./EvaluationSummary";
import { formatObservedAt } from "./display-format";
import type { MLResourceKind } from "./PlatformNavigation";
import type {
  PipelineRun, ModelRevision, ScoringResult, DecisionPolicyRevision, EvaluationRevision,
} from "./types";

type Evidence = {
  model: ModelRevision; calibration: ScoringResult; test: ScoringResult;
  policy: DecisionPolicyRevision; evaluation: EvaluationRevision;
};
type OpenResult = (kind: MLResourceKind, id: string) => void;

function metric(source: { [key: string]: unknown } | null | undefined, key: string): number | null {
  const value = source?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function format(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("ko-KR", { maximumFractionDigits: 6 }) : "미계산";
}
function resultRef(run: PipelineRun, stage: string, field: string): string | null {
  const result = run.stages?.find(item => item.name === stage)?.job.result;
  const id = result?.[field];
  return typeof id === "string" && id.length > 0 ? id : null;
}

/** 여러 독립 판본을 한 분석으로 연결할 때 시간 분할·모델·점수 의미까지 검증한다. */
export function validatePipelineEvidence(run: PipelineRun, evidence: Evidence): string | null {
  const { model, calibration, test, policy, evaluation } = evidence;
  const plan = run.input_plan;
  if (!plan || !run.stages) return "완료된 Run의 시간 분할 또는 단계 근거가 없습니다.";
  if (model.id !== resultRef(run, "training", "model_revision_id") ||
      calibration.id !== resultRef(run, "calibration", "scoring_result_id") ||
      test.id !== resultRef(run, "test", "scoring_result_id") ||
      policy.id !== resultRef(run, "policy", "policy_revision_id") ||
      evaluation.id !== resultRef(run, "evaluation", "evaluation_revision_id") ||
      model.dataset_id !== run.dataset_id || model.dataset_version !== run.dataset_version ||
      calibration.dataset_id !== run.dataset_id || test.dataset_id !== run.dataset_id ||
      calibration.dataset_version !== run.dataset_version ||
      test.dataset_version !== run.dataset_version ||
      model.algorithm !== "isolation_forest" ||
      policy.mode !== "quantile" || policy.alert_fraction !== 0.005 ||
      !Number.isFinite(policy.threshold) ||
      calibration.model_revision_id !== model.id || test.model_revision_id !== model.id ||
      policy.scoring_result_id !== calibration.id ||
      evaluation.scoring_result_id !== test.id || evaluation.policy_revision_id !== policy.id ||
      metric(model.training_score_summary, "score_count") !== plan.partition_sizes.train) {
    return "분석 결과의 모델·Dataset·정책 또는 판본 참조가 일치하지 않습니다.";
  }
  const sameMoment = (left: unknown, right: string) =>
    typeof left === "string" && Number.isFinite(Date.parse(left)) &&
    Date.parse(left) === Date.parse(right);
  for (const [scoring, partition, rows] of [
    [calibration, "calibration", plan.partition_sizes.calibration],
    [test, "test", plan.partition_sizes.test],
  ] as const) {
    const scope = scoring.score_summary.temporal_scope;
    if (!scope || typeof scope !== "object") {
      return "Scoring Result의 시간 구간 근거가 누락됐습니다.";
    }
    const time = scope as Record<string, unknown>;
    if (scoring.row_count !== rows ||
        metric(scoring.score_summary, "score_count") !== rows ||
        scoring.score_direction !== "higher_is_more_anomalous" ||
        scoring.feature_names.length !== model.features.length ||
        scoring.feature_names.some((feature, i) => feature !== model.features[i]) ||
        time.partition !== partition ||
        time.timestamp_column !== plan.timestamp_column ||
        !sameMoment(time.train_end, plan.train_end) ||
        !sameMoment(time.calibration_end, plan.calibration_end) ||
        time.model_revision_id !== model.id ||
        time.dataset_id !== run.dataset_id ||
        time.dataset_version !== run.dataset_version ||
        time.train_rows !== plan.partition_sizes.train ||
        time.calibration_rows !== plan.partition_sizes.calibration ||
        time.test_rows !== plan.partition_sizes.test) {
      return "분석 결과의 Feature 서명 또는 Train·Calibration·Test 시간 분할이 일치하지 않습니다.";
    }
  }
  const count = metric(evaluation.decision_metrics, "score_count");
  const candidates = metric(evaluation.decision_metrics, "predicted_anomalies");
  const rate = metric(evaluation.decision_metrics, "alert_rate");
  const calibrated = metric(policy.decision_summary, "score_count");
  if (calibrated !== calibration.row_count || count !== test.row_count ||
      candidates === null || !Number.isInteger(candidates) ||
      candidates < 0 || candidates > test.row_count ||
      rate === null || Math.abs(rate - candidates / test.row_count) > 1e-10) {
    return "Calibration 정책 또는 Test 후보 수·비율의 평가 근거가 일치하지 않습니다.";
  }
  return null;
}

/** 저장된 고정 폭 구간 빈도만 표시하며 행 표본을 임의로 확대하지 않는다. */
export type PipelineScoreRange = { lower: number; upper: number; includeUpper: boolean };

function ScoreHistogram({ summary, title, threshold, onSelectRange }: {
  summary: Record<string, unknown>; title: string; threshold?: number;
  onSelectRange?: (range: PipelineScoreRange) => void;
}) {
  const [selectedBin, setSelectedBin] = useState<number | null>(null);
  const raw = summary.histogram;
  const hist = raw && typeof raw === "object" ? raw as Record<string, unknown> : null;
  const edges = hist && Array.isArray(hist.bin_edges) ? hist.bin_edges : [];
  const counts = hist && Array.isArray(hist.bin_counts) ? hist.bin_counts : [];
  const valid = hist?.method === "equal_width_exact_v1" &&
    typeof hist.score_count === "number" && Number.isInteger(hist.score_count) &&
    edges.length === counts.length + 1 && counts.length >= 1 && counts.length <= 128 &&
    edges.every(x => typeof x === "number" && Number.isFinite(x)) &&
    counts.every(x => typeof x === "number" && Number.isInteger(x) && x >= 0) &&
    counts.reduce((a: number, b: unknown) => a + Number(b), 0) === hist.score_count &&
    metric(summary, "score_count") === hist.score_count &&
    edges.every((x, i) => i === 0 || x > edges[i - 1]);
  if (!valid) return <p className="hint">{hist
    ? "저장된 점수 분포 집계의 경계·건수가 검증되지 않아 그래프를 표시하지 않습니다."
    : "이 분석에는 전체 점수 분포 집계가 저장돼 있지 않습니다."}
    기존 최솟값·평균·최댓값 근거는 위 비교 그래프에서 확인할 수 있습니다.</p>;
  const bin = selectedBin === null ? null : Math.min(selectedBin, counts.length - 1);
  function selectBin(index: number) {
    if (index < 0 || index >= counts.length) return;
    setSelectedBin(index);
    onSelectRange?.({ lower: edges[index], upper: edges[index + 1], includeUpper: index === counts.length - 1 });
  }
  const option = {
    aria: { enabled: true },
    tooltip: { trigger: "item" },
    grid: { left: 58, right: 28, top: 32, bottom: 57 },
    xAxis: { type: "value" as const, name: "이상 점수", nameLocation: "middle" as const, nameGap: 32,
      axisLabel: { hideOverlap: true, formatter: (value: number) => value.toFixed(3) },
      min: threshold === undefined ? edges[0] : Math.min(edges[0], threshold),
      max: threshold === undefined ? edges[edges.length - 1]
        : Math.max(edges[edges.length - 1], threshold) },
    yAxis: { type: "value" as const, name: "행 수", minInterval: 1 },
    series: [{
      type: "bar" as const, name: "구간별 실제 관측 수",
      data: counts.map((n, i) => [(edges[i] + edges[i + 1]) / 2, n]),
      ...(threshold === undefined ? {} : { markLine: {
        silent: true, symbol: "none",
        label: { show: false },
        data: [{ xAxis: threshold }],
      } }),
    }],
  };
  return <div className="pipeline-score-chart">
    <DataChart option={option} ariaLabel={title + " 점수 구간별 전체 관측 수" +
      (threshold === undefined ? "" : " 및 Calibration 임계값")}
      onDataClick={selectBin} />
    {threshold !== undefined && <p className="pipeline-threshold-note">
      Calibration 임계값 <strong>{threshold.toPrecision(6)}</strong> · 점선은 기준 점수입니다.
    </p>}
    {onSelectRange && <p className="hint">그래프 막대나 아래 목록에서 점수 구간을 선택하면
      현재 불러온 이상 후보를 해당 범위로 좁혀볼 수 있습니다.</p>}
    <p className="hint">전체 {format(Number(hist?.score_count))}건의 점수 분포입니다.
      {hist?.constant_scores ? "모든 점수가 같습니다." : ""}</p>
    <div className="histogram-bin-picker">
      <label>점수 구간
        <select aria-label={title + " 점수 구간"} value={bin ?? ""} onChange={event => selectBin(Number(event.target.value))}>
          <option value="" disabled>구간 선택</option>
          {counts.map((_, index) => <option key={index} value={index}>
            {edges[index].toPrecision(4)} ~ {edges[index + 1].toPrecision(4)}
          </option>)}
        </select>
      </label>
      {bin !== null && <p role="status">이 구간 <strong>{Number(counts[bin]).toLocaleString("ko-KR")}건</strong>
        <small>{edges[bin].toPrecision(8)} 이상 · {edges[bin + 1].toPrecision(8)}{bin === counts.length - 1 ? " 이하" : " 미만"}</small>
      </p>}
    </div>
  </div>;
}

/** 각 점은 실제 저장된 최소·평균·최대값이며, 분포나 학습 곡선을 추정하지 않는다. */
function ScoreRangeChart({ evidence }: { evidence: Evidence }) {
  const summaries = [
    evidence.model.training_score_summary,
    evidence.calibration.score_summary,
    evidence.test.score_summary,
  ];
  const keys = ["score_min", "score_mean", "score_max"] as const;
  const labels = ["최소 점수", "평균 점수", "최대 점수"];
  const option = {
    aria: { enabled: true },
    tooltip: { trigger: "item" },
    legend: { data: labels, bottom: 0 },
    grid: { left: 72, right: 35, top: 36, bottom: 75 },
    xAxis: { type: "category" as const, data: ["Train", "Calibration", "Test"] },
    yAxis: { type: "value" as const, scale: true, name: "이상 점수",
      axisLabel: { hideOverlap: true, formatter: (value: number) => value.toFixed(3) } },
    series: keys.map((key, i) => ({
      type: "scatter" as const,
      name: labels[i],
      symbolSize: i === 1 ? 14 : 10,
      data: summaries.map(s => metric(s, key)),
      ...(i === 1 ? { markLine: {
        silent: true, symbol: "none",
        label: { show: false },
        data: [{ yAxis: evidence.policy.threshold }],
      } } : {}),
    })),
  };
  return <div className="pipeline-score-chart">
    <DataChart option={option} ariaLabel="Train, Calibration, Test의 실제 최소·평균·최대 이상 점수와 Calibration 임계값 비교" />
    <p className="pipeline-threshold-note">Calibration 임계값
      <strong>{evidence.policy.threshold.toPrecision(6)}</strong> · 점선은 Test에 적용한 기준입니다.</p>
    <p className="hint">위 비교 그래프는 세 구간의 최솟값·평균·최댓값만 표현합니다.
      전체 점수 분포는 Test 그래프와 학습·검토 기준의 상세에서 확인할 수 있습니다.
      표시된 임계값은 Calibration에서 정해 Test에 적용한 기준입니다.</p>
  </div>;
}

/** 불변 결과의 실제 의미와 출처를 확인한 뒤 단계별 분석 근거를 제공한다. */
export type PipelineAnalysisSection = "overview" | "training" | "calibration" | "test";
export function PipelineEvidenceView({ evidence, run, onOpenResource,
  initialSection = "overview", onSectionChange, onInvestigateCandidates, onSelectTestScoreRange,
}: {
  evidence: Evidence; run: PipelineRun; onOpenResource: OpenResult;
  initialSection?: PipelineAnalysisSection;
  onSectionChange?: (section: PipelineAnalysisSection) => void;
  onInvestigateCandidates?: () => void;
  onSelectTestScoreRange?: (range: PipelineScoreRange) => void;
}) {
  const [section, setSection] = useState<PipelineAnalysisSection>(initialSection);
  useEffect(() => { setSection(initialSection); }, [initialSection]);
  const { model, calibration, test, policy, evaluation } = evidence;
  const plan = run.input_plan;
  const decision = evaluation.decision_metrics;
  const candidates = metric(decision, "predicted_anomalies");
  const rate = metric(decision, "alert_rate");
  const unlabeled = evaluation.label_coverage === "none" || decision.evaluation_kind === "unlabeled";
  const toggle = (id: PipelineAnalysisSection, open: boolean) => {
    if (open && section !== id) { setSection(id); onSectionChange?.(id); }
    else if (!open && section === id) { setSection("overview"); onSectionChange?.("overview"); }
  };
  return <article className="pipeline-analysis-report training-report" aria-label="학습 및 검토 결과">
    <header className="training-report-heading">
      <div><span className="eyebrow">학습 모델</span>
        <h4>Isolation Forest{model.registry_version ? ` · v${model.registry_version}` : ""}</h4>
        <p>학습에 사용한 {format(plan?.partition_sizes.train)}건으로 모델을 만들고,
          이후 시간 구간의 {format(test.row_count)}건에 검토 기준을 적용했습니다.</p>
      </div>
    </header>
    <div className="training-report-outcome">
      <div><span>Test 구간의 검토 대상</span><strong>{format(candidates)}건</strong>
        <span>{format(test.row_count)}건 중 {rate === null ? "비율 미확인" : `${format(rate * 100)}%`}</span></div>
      <div>
        <p>{candidates === 0 ? "정한 점수 기준 이상인 관측값이 없습니다."
          : "정한 점수 기준 이상인 관측값입니다. 실제 활동과 점수 근거를 조사해 보세요."}</p>
        {candidates !== null && candidates > 0 && onInvestigateCandidates
          ? <button type="button" onClick={onInvestigateCandidates}>검토 대상 탐색 →</button>
          : <button type="button" className="secondary" onClick={() => onOpenResource("scoring", test.id)}>Test 관측값 탐색 →</button>}
      </div>
    </div>
    <div className="training-report-basis">
      <span>검토 기준</span>
      <p>기준을 정한 Calibration {format(calibration.row_count)}건의 상위 {policy.alert_fraction === null
        ? "미확인" : format(policy.alert_fraction * 100) + "%"} 점수로 경계를 정했습니다.
        Test에는 같은 경계 <strong>{format(policy.threshold)}</strong>를 적용했습니다.</p>
    </div>
    <section className="training-test-distribution" aria-label="검토 기준과 Test 점수 분포">
      <h4>Test 점수 분포</h4>
      {metric(model.training_score_summary, "score_mean") !== null && metric(test.score_summary, "score_mean") !== null &&
        <p className="training-score-comparison">평균 이상 점수 · 학습 {format(metric(model.training_score_summary, "score_mean"))}
          {" → "}Test {format(metric(test.score_summary, "score_mean"))}</p>}
      <ScoreHistogram summary={test.score_summary} title="Test" threshold={policy.threshold} onSelectRange={onSelectTestScoreRange} />
    </section>
    <p className="training-report-limitation">{unlabeled
      ? "정답 레이블이 없어 실제 사건 검출 정확도와 오탐 여부는 알 수 없습니다. 검토 대상 건수는 확인된 사건 수가 아닙니다."
      : decision.evaluation_kind === "labeled"
        ? "레이블과 비교한 평가입니다. 이 시간 구간의 지표가 다른 데이터에서의 성능을 보장하지는 않습니다."
        : "평가에서 확인한 범위와 레이블 유무는 아래 평가 근거에서 확인할 수 있습니다."}</p>
    <div className="training-report-details">
      <details open={section === "training"} onToggle={event => toggle("training", event.currentTarget.open)}>
        <summary>학습 데이터·설정</summary>
        <dl className="training-report-facts">
          <div><dt>학습 입력</dt><dd>{format(plan?.partition_sizes.train)}건 · Feature {model.features.length}개</dd></div>
          <div><dt>학습 종료 시각</dt><dd>{plan?.train_end ? formatObservedAt(plan.train_end) : "미확인"}</dd></div>
          <div><dt>결측값 처리</dt><dd>{model.runtime.preprocessing_mode === "train_median_v1" ? "학습 중앙값 대체" : "모델 판본 근거 확인"}</dd></div>
          {Object.entries(model.params).map(([name, value]) => <div key={name}>
            <dt>{{ n_estimators: "트리 수", max_samples: "트리별 표본 수", max_features: "트리별 Feature 범위",
              bootstrap: "복원 추출", random_state: "난수 Seed" }[name] ?? name}</dt>
            <dd>{value === "auto" ? "자동" : typeof value === "boolean" ? value ? "사용" : "사용 안 함" : String(value)}</dd>
          </div>)}
        </dl>
        {section === "training" && <ScoreHistogram summary={model.training_score_summary} title="Training" />}
        <button type="button" className="secondary" onClick={() => onOpenResource("model", model.id)}>모델 판본 열기 →</button>
      </details>
      <details open={section === "calibration"} onToggle={event => toggle("calibration", event.currentTarget.open)}>
        <summary>검토 기준 산정</summary>
        <p>Calibration 구간의 선형 보간 분위수로 점수 경계를 정했습니다. 동점 때문에 실제 비율은 설정 비율과 다를 수 있습니다.</p>
        {section === "calibration" && <ScoreHistogram summary={calibration.score_summary} title="Calibration" threshold={policy.threshold} />}
        <div className="actions"><button type="button" className="secondary" onClick={() => onOpenResource("policy", policy.id)}>판정 정책 열기 →</button>
          <button type="button" className="secondary" onClick={() => onOpenResource("scoring", calibration.id)}>Calibration 점수 결과 →</button></div>
      </details>
      <details open={section === "test"} onToggle={event => toggle("test", event.currentTarget.open)}>
        <summary>평가 범위·근거</summary>
        <EvaluationSummary evaluation={evaluation} />
        {section === "test" && <ScoreRangeChart evidence={evidence} />}
        <div className="actions"><button type="button" className="secondary" onClick={() => onOpenResource("evaluation", evaluation.id)}>평가 판본 열기 →</button>
          <button type="button" className="secondary" onClick={() => onOpenResource("scoring", test.id)}>Test 점수 결과 →</button></div>
      </details>
    </div>
  </article>;
}

export function PipelineAnalysis({
  run, onOpenResource, initialSection, onSectionChange, onInvestigateCandidates,
  onSelectTestScoreRange,
}: {
  run: PipelineRun; onOpenResource: OpenResult;
  initialSection?: PipelineAnalysisSection;
  onSectionChange?: (section: PipelineAnalysisSection) => void;
  onInvestigateCandidates?: () => void;
  onSelectTestScoreRange?: (range: PipelineScoreRange) => void;
}) {
  const modelId = resultRef(run, "training", "model_revision_id");
  const calibrationId = resultRef(run, "calibration", "scoring_result_id");
  const testId = resultRef(run, "test", "scoring_result_id");
  const policyId = resultRef(run, "policy", "policy_revision_id");
  const evaluationId = resultRef(run, "evaluation", "evaluation_revision_id");
  const key = [run.id, modelId, calibrationId, testId, policyId, evaluationId].join(":");
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<{ key: string; evidence: Evidence | null; error: string } | null>(null);
  useEffect(() => {
    if (!modelId || !calibrationId || !testId || !policyId || !evaluationId) return;
    let active = true;
    setState({ key, evidence: null, error: "" });
    void Promise.all([
      request<ModelRevision>("/api/ml/models/" + encodeURIComponent(modelId)),
      request<ScoringResult>("/api/ml/scoring/results/" + encodeURIComponent(calibrationId)),
      request<ScoringResult>("/api/ml/scoring/results/" + encodeURIComponent(testId)),
      request<DecisionPolicyRevision>("/api/ml/policies/" + encodeURIComponent(policyId)),
      request<EvaluationRevision>("/api/ml/evaluations/" + encodeURIComponent(evaluationId)),
    ]).then(([model, calibration, test, policy, evaluation]) => {
      const issue = validatePipelineEvidence(run, { model, calibration, test, policy, evaluation });
      if (issue) throw new Error(issue);
      if (active) setState({ key, evidence: { model, calibration, test, policy, evaluation }, error: "" });
    }).catch(error => { if (active) setState({ key, evidence: null, error: formatError(error) }); });
    return () => { active = false; };
  }, [key, retry]);
  if (!modelId || !calibrationId || !testId || !policyId || !evaluationId) {
    return <div className="pipeline-readiness-blocked" role="alert">
      완료된 Run의 분석 판본 참조가 불완전합니다. 아래 단계별 실행 근거를 확인하세요.
    </div>;
  }
  const current = state?.key === key ? state : null;
  if (current?.error) return <section className="pipeline-readiness-blocked" role="alert">
    <strong>현재 분석의 근거를 완전히 검증하지 못했습니다.</strong>
    <p>{current.error}</p>
    <p>일부 결과 판본을 조회할 수 없거나 참조가 일치하지 않습니다.
      이 상태에서는 전체 분석 보고서를 완성된 결과로 표시하지 않습니다.
      아래의 독립 판본은 각각의 권한 검사와 출처를 확인해 열 수 있습니다.</p>
    <div className="pipeline-actions">
      <button type="button" className="secondary" onClick={() => setRetry(v => v + 1)}>
        통합 분석 근거 다시 조회
      </button>
      {([
        ["model", modelId, "학습 모델"],
        ["scoring", calibrationId, "Calibration 점수"],
        ["policy", policyId, "판정 정책"],
        ["scoring", testId, "Test 점수"],
        ["evaluation", evaluationId, "평가 결과"],
      ] as const).map(([kind, id, label]) => id &&
        <button key={kind + ":" + id} type="button" className="secondary"
          onClick={() => onOpenResource(kind, id)}>{label} 개별 판본 보기 →</button>)}
    </div>
  </section>;
  if (!current?.evidence) return <p role="status">모델·Calibration·Test의 불변 분석 근거를 불러오는 중입니다.</p>;
  return <PipelineEvidenceView evidence={current.evidence} run={run}
    onOpenResource={onOpenResource} initialSection={initialSection}
    onSectionChange={onSectionChange}
    onInvestigateCandidates={onInvestigateCandidates}
    onSelectTestScoreRange={onSelectTestScoreRange} />;
}
