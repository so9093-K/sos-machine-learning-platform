/** 첫 IF Pipeline에 이미 존재하는 불변 ML 판본으로 구성한 결과 검토 경험. */
import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { DataChart } from "./DataChart";
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

/** 저장된 고정 폭 구간 빈도만 표시하며 행 표본을 임의로 확대하지 않는다. */
function ScoreHistogram({ summary, title, threshold }: {
  summary: Record<string, unknown>; title: string; threshold?: number;
}) {
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
  if (!valid) return <p className="hint">이 Run에는 전체 점수 분포 집계가 저장돼 있지 않습니다.
    기존 최솟값·평균·최댓값 근거는 위 비교 그래프에서 확인할 수 있습니다.</p>;
  const option = {
    aria: { enabled: true },
    tooltip: { trigger: "item" },
    grid: { left: 60, right: 38, top: 26, bottom: 55 },
    xAxis: { type: "value" as const, name: "이상 점수",
      min: threshold === undefined ? edges[0] : Math.min(edges[0], threshold),
      max: threshold === undefined ? edges[edges.length - 1]
        : Math.max(edges[edges.length - 1], threshold) },
    yAxis: { type: "value" as const, name: "행 수", minInterval: 1 },
    series: [{
      type: "bar" as const, name: "구간별 실제 관측 수",
      data: counts.map((n, i) => [(edges[i] + edges[i + 1]) / 2, n]),
      ...(threshold === undefined ? {} : { markLine: {
        silent: true, symbol: "none",
        label: { formatter: "Calibration 기준", position: "insideEndTop" as const },
        data: [{ xAxis: threshold }],
      } }),
    }],
  };
  return <div className="pipeline-score-chart">
    <DataChart option={option} ariaLabel={title + " 점수 구간별 전체 관측 수" +
      (threshold === undefined ? "" : " 및 Calibration 임계값")} />
    <p className="hint">{title}의 실제 점수를 전체 조회해 집계한 {counts.length}개 고정 폭 구간입니다.
      {hist?.constant_scores ? "모든 점수가 같아 그래프 표시 범위만 확장했습니다. " : ""}
      임계값 경계에 걸친 단일 막대는 후보 수를 의미하지 않습니다.</p>
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
    yAxis: { type: "value" as const, scale: true, name: "이상 점수" },
    series: keys.map((key, i) => ({
      type: "scatter" as const,
      name: labels[i],
      symbolSize: i === 1 ? 14 : 10,
      data: summaries.map(s => metric(s, key)),
      ...(i === 1 ? { markLine: {
        silent: true, symbol: "none",
        label: { formatter: "Calibration 경계", position: "insideEndTop" as const },
        data: [{ yAxis: evidence.policy.threshold }],
      } } : {}),
    })),
  };
  return <div className="pipeline-score-chart">
    <DataChart option={option} ariaLabel="Train, Calibration, Test의 실제 최소·평균·최대 이상 점수와 Calibration 임계값 비교" />
    <p className="hint">위 비교 그래프는 세 구간의 최솟값·평균·최댓값만 표현합니다.
      전체 점수 분포는 아래의 단계별 히스토그램에서 확인할 수 있습니다.
      표시된 임계값은 Calibration에서 정해 Test에 적용한 기준입니다.</p>
  </div>;
}

/** 불변 결과의 실제 의미와 출처를 확인한 뒤 단계별 분석 근거를 제공한다. */
export function PipelineEvidenceView({ evidence, run, onOpenResource }: {
  evidence: Evidence; run: PipelineRun; onOpenResource: OpenResult;
}) {
  const { model, calibration, test, policy, evaluation } = evidence;
  const train = model.training_score_summary;
  const cal = calibration.score_summary;
  const scored = test.score_summary;
  const decision = evaluation.decision_metrics;
  const trainRows = run.input_plan?.partition_sizes.train;
  const calibrationRows = run.input_plan?.partition_sizes.calibration;
  const testRows = run.input_plan?.partition_sizes.test;
  return <div className="pipeline-analysis-report" aria-label="Pipeline 결과 분석">
    <div className="pipeline-section-heading">
      <div>
        <span className="eyebrow">ANALYSIS · 실행 근거와 결과 검토</span>
        <h4>이번 Run의 분석 결과</h4>
        <p>학습한 모델, Calibration에서 정한 기준, Test에 적용한 결과를
          같은 Dataset 판본의 근거로 연결해 확인합니다.</p>
      </div>
    </div>
    <ScoreRangeChart evidence={evidence} />
    <div className="pipeline-analysis-phases">
      <section className="pipeline-analysis-phase" aria-labelledby="pipeline-training-evidence">
        <h5 id="pipeline-training-evidence">01 · Training — 무엇을 학습했는가?</h5>
        <dl>
          <div><dt>학습 구간</dt><dd>{format(trainRows)}행</dd></div>
          <div><dt>알고리즘</dt><dd>{model.algorithm}</dd></div>
          <div><dt>입력 Feature</dt><dd>{model.features.length}개</dd></div>
          <div><dt>결측값 처리</dt><dd>{model.runtime.preprocessing_mode === "train_median_v1"
            ? "Train 중앙값 대체" : "별도 모델 판본 근거 확인"}</dd></div>
          <div><dt>학습 점수 범위</dt><dd>{format(metric(train, "score_min"))} ~ {format(metric(train, "score_max"))}</dd></div>
          <div><dt>학습 평균 점수</dt><dd>{format(metric(train, "score_mean"))}</dd></div>
        </dl>
        <ScoreHistogram summary={train} title="Training" />
        <p className="hint">학습 점수는 Train 데이터 내부 진단이며 독립적인 탐지 정확도가 아닙니다.
          Isolation Forest에는 epoch별 loss/accuracy 곡선이 정의되어 있지 않습니다.</p>
        <details><summary>학습 설정·Feature 계약 확인</summary>
          <pre>{JSON.stringify(model.params, null, 2)}</pre>
          <p>Feature: {model.features.join(" · ")}</p>
        </details>
        <button type="button" className="secondary" onClick={() =>
          onOpenResource("model", model.id)}>모델 판본 열기 →</button>
      </section>
      <section className="pipeline-analysis-phase" aria-labelledby="pipeline-calibration-evidence">
        <h5 id="pipeline-calibration-evidence">02 · Calibration — 기준은 어떻게 정했는가?</h5>
        <dl>
          <div><dt>Calibration 구간</dt><dd>{format(calibrationRows)}행</dd></div>
          <div><dt>방법</dt><dd>{policy.mode === "quantile" ? "점수 분위수" : policy.mode}</dd></div>
          <div><dt>기준 상위 비율</dt><dd>{policy.alert_fraction === null
            ? "해당 없음" : format(policy.alert_fraction * 100) + "%"}</dd></div>
          <div><dt>적용 임계값</dt><dd>{format(policy.threshold)}</dd></div>
          <div><dt>Calibration 점수 범위</dt>
            <dd>{format(metric(cal, "score_min"))} ~ {format(metric(cal, "score_max"))}</dd></div>
          <div><dt>Calibration 평균 점수</dt><dd>{format(metric(cal, "score_mean"))}</dd></div>
          <div><dt>Calibration 후보 수</dt>
            <dd>{format(metric(policy.decision_summary, "predicted_anomalies"))}건</dd></div>
        </dl>
        <ScoreHistogram summary={cal} title="Calibration" threshold={policy.threshold} />
        <p className="hint">현재 IF Pipeline은 EVT/POT가 아닌 Calibration 점수의
          선형 보간 분위수로 경계를 정합니다. 동점 때문에 실제 후보 비율은 설정값과 다를 수 있습니다.</p>
        <div className="pipeline-actions">
          <button type="button" className="secondary" onClick={() =>
            onOpenResource("policy", policy.id)}>판정 정책 열기 →</button>
          <button type="button" className="secondary" onClick={() =>
            onOpenResource("scoring", calibration.id)}>Calibration 점수 근거 →</button>
        </div>
      </section>
      <section className="pipeline-analysis-phase" aria-labelledby="pipeline-test-evidence">
        <h5 id="pipeline-test-evidence">03 · Test — 적용 후 무엇이 달라졌는가?</h5>
        <dl>
          <div><dt>Test 구간</dt><dd>{format(testRows)}행</dd></div>
          <div><dt>Test 점수 범위</dt><dd>{format(metric(scored, "score_min"))} ~ {format(metric(scored, "score_max"))}</dd></div>
          <div><dt>Test 평균 점수</dt><dd>{format(metric(scored, "score_mean"))}</dd></div>
          <div><dt>검토 후보</dt><dd>{format(metric(decision, "predicted_anomalies"))}건</dd></div>
          <div><dt>실제 Test 후보 비율</dt><dd>{metric(decision, "alert_rate") === null
            ? "미계산" : format(metric(decision, "alert_rate")! * 100) + "%"}</dd></div>
          <div><dt>정답 레이블</dt><dd>{evaluation.label_coverage === "none" ? "없음" : evaluation.label_coverage}</dd></div>
          <div><dt>Calibration/Test 시간 구간</dt><dd>{
            decision.temporal_nonoverlap_confirmed === true ? "비중복 확인" : "근거 미확인"
          }</dd></div>
        </dl>
        <ScoreHistogram summary={scored} title="Test" threshold={policy.threshold} />
        <p className="hint">Calibration에서 정한 정책을 별도 Test 시간 구간에 적용합니다.
          검토 후보는 실제 이상 사건이 아니며, 레이블이 없는 결과에는 정확도·Precision·Recall을 부여하지 않습니다.</p>
        <div className="pipeline-actions">
          <button type="button" className="secondary" onClick={() =>
            onOpenResource("scoring", test.id)}>Test 점수 결과 →</button>
          <button type="button" className="secondary" onClick={() =>
            onOpenResource("evaluation", evaluation.id)}>평가 판본 열기 →</button>
        </div>
      </section>
    </div>
  </div>;
}

/** URL Run이 변경되면 이전 응답을 폐기하며, 어떤 결과도 섞지 않는다. */
export function PipelineAnalysis({ run, onOpenResource }: {
  run: PipelineRun; onOpenResource: OpenResult;
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
      if (model.id !== modelId || calibration.id !== calibrationId || test.id !== testId ||
          policy.id !== policyId || evaluation.id !== evaluationId ||
          model.dataset_id !== run.dataset_id || model.dataset_version !== run.dataset_version ||
          calibration.dataset_id !== run.dataset_id || test.dataset_id !== run.dataset_id ||
          calibration.dataset_version !== run.dataset_version ||
          test.dataset_version !== run.dataset_version ||
          calibration.model_revision_id !== model.id || test.model_revision_id !== model.id ||
          policy.scoring_result_id !== calibration.id ||
          evaluation.scoring_result_id !== test.id || evaluation.policy_revision_id !== policy.id ||
          (run.input_plan != null && (
            metric(model.training_score_summary, "score_count") !== run.input_plan.partition_sizes.train ||
            calibration.row_count !== run.input_plan.partition_sizes.calibration ||
            test.row_count !== run.input_plan.partition_sizes.test))) {
        throw new Error("분석 결과의 입력 판본 또는 Calibration·Test 참조가 일치하지 않습니다.");
      }
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
    <strong>분석 근거를 조회하지 못했습니다.</strong>
    <p>{current.error}</p>
    <button type="button" className="secondary" onClick={() => setRetry(v => v + 1)}>분석 근거 다시 조회</button>
  </section>;
  if (!current?.evidence) return <p role="status">모델·Calibration·Test의 불변 분석 근거를 불러오는 중입니다.</p>;
  return <PipelineEvidenceView evidence={current.evidence} run={run} onOpenResource={onOpenResource} />;
}
