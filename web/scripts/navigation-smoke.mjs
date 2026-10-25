import assert from "node:assert/strict";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

// 기존 Vite 변환을 사용한다. 브라우저/서버에 의존하지 않는 URL 선택 계약만 확인한다.
const vite = await createServer({
  configFile: false,
  plugins: [react()],
  optimizeDeps: { noDiscovery: true, include: [] },
  server: { middlewareMode: true },
  appType: "custom",
});

try {
  const { routeAt, contextPath, nextNavigationContext, resourcePath,
    pipelineRunSelectionContext, ModelAreaNavigation, PlatformNavigation } = await vite.ssrLoadModule("/src/PlatformNavigation.tsx");

  const { modelLabLabels, modelLabTree } = await vite.ssrLoadModule("/src/ModelLabTaxonomy.ts");
  const grouped = modelLabTree([
    { id: "a", name: "활동 이상 탐지", learning_type: "unsupervised",
      task_type: "anomaly_detection", topic: "activity_anomaly" },
    { id: "b", name: "지도 학습 이상 탐지", learning_type: "supervised",
      task_type: "anomaly_detection", topic: "other_anomaly" },
  ]);
  assert.equal(grouped.length, 2);
  assert.deepEqual(grouped.map(group => group.tasks[0].id), ["anomaly_detection", "anomaly_detection"]);
  assert.equal(grouped.flatMap(group => group.tasks.flatMap(task => task.topics)).length, 2);
  assert.deepEqual(modelLabLabels({
    learning_type: "unsupervised", task_type: "anomaly_detection", topic: "activity_anomaly",
  }),
    ["비지도 학습", "이상 탐지", "활동 이상 탐지"]);
  assert.deepEqual(modelLabLabels({
    learning_type: "semi_supervised", task_type: "anomaly_detection", topic: "new_topic",
  }), ["semi_supervised", "이상 탐지", "new_topic"]);
  const catalogPendingMenu = renderToStaticMarkup(React.createElement(PlatformNavigation, {
    active: "pipelines", canInspectStorage: false, canViewAudit: false,
    onNavigate: () => {},
  }));
  assert.match(catalogPendingMenu, /활동 이상 탐지 화면 열기/);
  assert.match(catalogPendingMenu, /href="\/pipelines"/);
  const scoringId = "score-v1";
  const first = routeAt("/evaluations", "?scoring=" + scoringId);
  assert.equal(first.tab, "evaluations");
  assert.equal(first.context.scoringId, scoringId);
  assert.equal(contextPath("policies", nextNavigationContext(first, "policies")),
    "/policies?scoring=score-v1");
  assert.equal(contextPath("explanations", nextNavigationContext(first, "explanations")),
    "/explanations?scoring=score-v1");
  assert.deepEqual(nextNavigationContext(first, "training"), {});

  const training = routeAt("/training", "?dataset=data-v1");
  assert.equal(contextPath("ml", nextNavigationContext(training, "ml")),
    "/models?dataset=data-v1");
  assert.equal(contextPath("training", nextNavigationContext(routeAt(
    "/models", "?dataset=data-v1"), "training")), "/training?dataset=data-v1");

  assert.equal(routeAt("/scoring", "?model=model-v1").context.modelId, "model-v1");
  assert.equal(contextPath("scoring", { modelId: "name + / ?" }),
    "/scoring?model=name+%2B+%2F+%3F");
  assert.equal(routeAt("/policies", "").context.scoringId, "");
  assert.equal(routeAt("/data", "?scoring=unrelated").context.scoringId, undefined);

  const immutablePath = resourcePath("scoring", "score/with space");
  assert.deepEqual(routeAt("/scoring/results/score%2Fwith%20space", "?scoring=other").resource,
    { kind: "scoring", id: "score/with space" });
  assert.equal(immutablePath, "/scoring/results/score%2Fwith%20space");
  const pipelineResource = resourcePath("model", "model/first", {
    pipelineReturn: true, pipelineRunId: "run/1", datasetId: "data v1",
    candidateRowIndex: 42,
  });
  assert.equal(pipelineResource,
    "/models/model%2Ffirst?from=pipeline&run=run%2F1&dataset=data+v1&candidate=42");
  assert.deepEqual(routeAt("/models/model%2Ffirst",
    "?from=pipeline&run=run%2F1&dataset=data+v1&candidate=42").context,
    { pipelineReturn: true, pipelineRunId: "run/1", datasetId: "data v1",
      candidateRowIndex: 42 });
  assert.deepEqual(routeAt("/models/model-v1", "").context, {});
  assert.deepEqual(routeAt("/models/model-v1", "?from=pipeline").context, {});
  assert.equal(contextPath("pipelines", { pipelineRunId: "run-1", candidateRowIndex: 7 }),
    "/pipelines?run=run-1&candidate=7");
  assert.equal(routeAt("/pipelines", "?run=run-1&candidate=7").context.candidateRowIndex, 7);
  assert.equal(routeAt("/pipelines", "?run=run-1&candidate=abc").context.candidateRowIndex, undefined);
  assert.equal(routeAt("/pipelines", "?candidate=7").context.candidateRowIndex, undefined);
  const focusedRoute = routeAt("/pipelines", "?run=run-1&candidate=8&section=calibration");
  assert.equal(focusedRoute.context.pipelineAnalysisSection, "calibration");
  assert.equal(focusedRoute.context.candidateRowIndex, 8);
  assert.equal(contextPath("pipelines", focusedRoute.context),
    "/pipelines?run=run-1&candidate=8&section=calibration");
  assert.equal(routeAt("/pipelines", "?run=run-1&section=incorrect").context.pipelineAnalysisSection,
    undefined);
  const preselectedRun = routeAt("/pipelines", "?run=run-1&candidate=8&section=test").context;
  assert.equal(contextPath("pipelines",
    pipelineRunSelectionContext(preselectedRun, "run-1", "actual-dataset")),
    "/pipelines?dataset=actual-dataset&run=run-1&candidate=8&section=test");
  assert.equal(contextPath("pipelines",
    pipelineRunSelectionContext(preselectedRun, "other-run", "other-dataset")),
    "/pipelines?dataset=other-dataset&run=other-run");
  assert.equal(contextPath("pipelines", pipelineRunSelectionContext(preselectedRun, "", "")),
    "/pipelines");
  assert.equal(resourcePath("policy", "policy-1", {
    pipelineReturn: true, pipelineRunId: "run-1",
    pipelineAnalysisSection: "calibration", candidateRowIndex: 8,
  }), "/policies/policy-1?from=pipeline&run=run-1&candidate=8&section=calibration");
  // 직접 연 Scoring/Model 상세에서 영역 탭으로 이동해도 동일 자산을 이어받는다.
  assert.equal(nextNavigationContext(
    routeAt("/scoring/results/score-v1", ""), "policies").scoringId, "score-v1");
  assert.equal(contextPath("evaluations", nextNavigationContext(
    routeAt("/scoring/results/score-v1", ""), "evaluations")),
    "/evaluations?scoring=score-v1");
  assert.equal(contextPath("scoring", nextNavigationContext(
    routeAt("/models/model-v1", ""), "scoring")), "/scoring?model=model-v1");
  assert.deepEqual(nextNavigationContext(
    routeAt("/models/model-v1", ""), "evaluations"), {});
  assert.deepEqual(nextNavigationContext(
    routeAt("/scoring/results/score-v1", ""), "training"), {});

  // 실제 모델 영역 링크도 Ctrl/새 탭 열기에서 같은 문맥을 유지한다.
  const resultNav = renderToStaticMarkup(React.createElement(ModelAreaNavigation, {
    active: "scoring", context: {},
    resource: { kind: "scoring", id: scoringId }, onNavigate: () => {},
  }));
  assert.match(resultNav, /href="\/evaluations\?scoring=score-v1"/);
  assert.match(resultNav, /href="\/policies\?scoring=score-v1"/);
  assert.match(resultNav, /href="\/explanations\?scoring=score-v1"/);
  const modelNav = renderToStaticMarkup(React.createElement(ModelAreaNavigation, {
    active: "ml", context: {},
    resource: { kind: "model", id: "model-v1" }, onNavigate: () => {},
  }));
  assert.match(modelNav, /href="\/scoring\?model=model-v1"/);

  // 학습 폼도 URL Dataset을 최초 선택값으로 렌더한다.
  const { MLWorkbench } = await vite.ssrLoadModule("/src/MLWorkbench.tsx");
  const trainingForm = renderToStaticMarkup(React.createElement(MLWorkbench, {
    datasets: [{ id: "data-v1", name: "Example Dataset", kind: "file" }],
    initialDatasetId: "data-v1", onDatasetSelected: () => {},
    canTrain: false, canCancel: false, onOpenModel: () => {},
  }));
  assert.match(trainingForm, /<option value="data-v1" selected=""/);

  const pagedDatasetForm = renderToStaticMarkup(React.createElement(MLWorkbench, {
    datasets: [], initialDatasetId: "data-not-on-first-page",
    onDatasetSelected: () => {}, canTrain: false, canCancel: false,
    onOpenModel: () => {},
  }));
  assert.match(pagedDatasetForm, /현재 목록 외 Dataset/);
  assert.match(pagedDatasetForm, /<option value="data-not-on-first-page" selected=""/);


  // 등록형 Pipeline의 선택 Dataset·Run 및 준비 화면 복귀 문맥.
  const pipelineRoute = routeAt("/pipelines", "?dataset=dataset-1&run=run-1");
  assert.equal(pipelineRoute.context.datasetId, "dataset-1");
  assert.equal(pipelineRoute.context.pipelineRunId, "run-1");
  assert.equal(contextPath("pipelines", { datasetId: "data 1", pipelineRunId: "run/1" }),
    "/pipelines?dataset=data+1&run=run%2F1");
  assert.deepEqual(nextNavigationContext(
    routeAt("/data", "?from=pipeline"), "preparation"), { pipelineReturn: true });
  assert.equal(contextPath("understanding", { pipelineReturn: true }), "/data?from=pipeline");
  assert.equal(routeAt("/data/prepare", "?from=pipeline").context.pipelineReturn, true);
  assert.equal(contextPath("preparation", { pipelineReturn: true, datasetId: "dataset-1" }),
    "/data/prepare?from=pipeline&dataset=dataset-1");
  assert.deepEqual(nextNavigationContext(
    routeAt("/data", "?from=pipeline&dataset=dataset-1"), "preparation"),
    { pipelineReturn: true, datasetId: "dataset-1" });
  assert.equal(routeAt("/data", "?from=pipeline&dataset=dataset-1").context.datasetId, "dataset-1");
  assert.equal(routeAt("/data", "?dataset=ignored").context.datasetId, "");
  assert.equal(contextPath("jobs", { pipelineReturn: true, datasetId: "dataset-1" }),
    "/activity/jobs?from=pipeline&dataset=dataset-1");
  assert.deepEqual(nextNavigationContext(
    routeAt("/data/prepare", "?from=pipeline&dataset=dataset-1"), "jobs"),
    { pipelineReturn: true, datasetId: "dataset-1" });
  assert.deepEqual(nextNavigationContext(
    routeAt("/activity/jobs", "?from=pipeline&dataset=dataset-1"), "preview"),
    { pipelineReturn: true, datasetId: "dataset-1" });
  assert.deepEqual(nextNavigationContext(routeAt("/activity/jobs", ""), "understanding"),
    { pipelineReturn: false });
  assert.deepEqual(nextNavigationContext(
    routeAt("/data", "?from=pipeline"), "ml"), {});
  assert.equal(contextPath("pipelines", { datasetId: "dataset-from-data" }),
    "/pipelines?dataset=dataset-from-data");
  assert.equal(routeAt("/activity/pipelines", "").tab, "pipeline-jobs");
  const exploredFromRun = {
    pipelineReturn: true, datasetId: "dataset-from-run",
    pipelineRunId: "run-1", candidateRowIndex: 8, pipelineAnalysisSection: "test",
  };
  assert.equal(contextPath("understanding", exploredFromRun),
    "/data?from=pipeline&dataset=dataset-from-run&run=run-1&candidate=8&section=test");
  const exploredPath = contextPath("understanding", exploredFromRun);
  const preparedContext = nextNavigationContext(
    routeAt("/data", exploredPath.slice(exploredPath.indexOf("?"))), "preparation");
  assert.deepEqual(preparedContext, exploredFromRun);
  assert.equal(contextPath("pipelines", preparedContext),
    "/pipelines?dataset=dataset-from-run&run=run-1&candidate=8&section=test");

  // 서버 계산 없이 첫 Pipeline 소비 화면의 입구·준비 안내를 렌더링한다.
  const { PipelineWorkspace, isRegistrationPending, filterCandidatesByScoreRange } =
    await vite.ssrLoadModule("/src/PipelineWorkspace.tsx");
  // Training Job 성공만으로 Run 전체가 성공한 것처럼 표시하지 않는다.
  const pendingRun = {
    status: "Running", current_stage: "training",
    stages: [{ name: "training", job: { status: "Succeeded" } }],
  };
  assert.equal(isRegistrationPending(pendingRun), true);
  assert.equal(isRegistrationPending({
    ...pendingRun, stages: [{ name: "training", job: { status: "Running" } }],
  }), false);
  assert.equal(isRegistrationPending({ ...pendingRun, current_stage: "calibration" }), false);
  assert.equal(isRegistrationPending({ ...pendingRun, status: "Succeeded" }), false);
  assert.equal(isRegistrationPending(null), false);

  const { MLTrackingDetails } = await vite.ssrLoadModule("/src/MLTrackingStatus.tsx");
  // Retry는 학습 실패가 아니며, 등록 오류 코드를 소비자가 확인할 수 있어야 한다.
  const retryTracking = renderToStaticMarkup(React.createElement(MLTrackingDetails, {
    status: { state: "Retry", attempts: 2, last_error_code: "SOS-MLFLOW-HTTP-503" },
    error: "",
  }));
  assert.match(retryTracking, /MLflow 기록·등록 재시도 대기 · 시도 2회/);
  assert.match(retryTracking, /SOS-MLFLOW-HTTP-503/);
  assert.match(retryTracking, /SOS Job 결과는 바뀌지 않으며/);
  const recoveredTracking = renderToStaticMarkup(React.createElement(MLTrackingDetails, {
    status: { state: "Succeeded", run_id: "mlflow-run-1" }, error: "",
  }));
  assert.match(recoveredTracking, /MLflow 기록 완료 · Run ID mlflow-run-1/);
  assert.doesNotMatch(recoveredTracking, /role="alert"|SOS-MLFLOW-HTTP-503/);

  const { PipelineAnalysis, PipelineEvidenceView, validatePipelineEvidence } = await vite.ssrLoadModule("/src/PipelineAnalysis.tsx");
  const { PipelineCandidateInvestigation, SensitivityChart, matchingExplanationJob } =
    await vite.ssrLoadModule("/src/PipelineCandidateInvestigation.tsx");
  const candidateWithContext = renderToStaticMarkup(
    React.createElement(PipelineCandidateInvestigation, {
      candidate: {
        row_index: 8, anomaly_score: 0.85, features: { ratio_outbound_traffic: 0.9 },
        observed_at: "2026-01-01T02:00:00Z",
        observation_id: "18c4be49-18c4-4f32-9894-8c3bfabbd008",
      },
      scoringResultId: "score-1", modelRevisionId: "model-1",
      canRun: false, canCancel: false, onOpenResource: () => {},
    })
  );
  assert.match(candidateWithContext, /2026-01-01T02:00:00Z/);
  assert.match(candidateWithContext, /Train 참조 중앙값/);
  assert.match(candidateWithContext, /전체 1개 Feature 관측값/);
  assert.match(candidateWithContext, /모델 판단 근거 자세히 보기 \(SHAP\)/);
  const { ShapContributionChart } = await vite.ssrLoadModule("/src/PipelineShapExplanation.tsx");
  const shapPreview = renderToStaticMarkup(React.createElement(ShapContributionChart, {
    result: { output: {
      method: "permutation_shap_median_v1", baseline_score: 0.3, score: 0.5,
      reconstructed_score: 0.5, additivity_residual: 0,
      additive_attribution: true, model_output: "negative_sklearn_score_samples",
      baseline_row_count: 1, permutation_rounds: 3, sampling_seed: 42,
      shap_version: "0.50.0",
      feature_contributions: [
        { feature: "outbound_traffic", shap_value: 0.2, actual_value: 4,
          reference_median: 1, missing_imputed: false },
      ],
    } },
  }));
  assert.match(shapPreview, /점수 변화에 기여한 주요 Feature 1개 \(SHAP\)/);
  assert.match(shapPreview, /Feature 기여도 합/);
  assert.match(shapPreview, /주요 Feature 1개/);
  assert.match(shapPreview, /양수: 이상 점수를 높이는 방향/);
  const unverifiedShap = renderToStaticMarkup(React.createElement(ShapContributionChart, {
    result: { output: {
      method: "permutation_shap_median_v1", baseline_score: 0.3, score: 0.5,
      reconstructed_score: 0.5, additivity_residual: 0,
      additive_attribution: true, model_output: "negative_sklearn_score_samples",
      baseline_row_count: 1,
      feature_contributions: [{ feature: "outbound_traffic", shap_value: -0.9,
        actual_value: 4, reference_median: 1, missing_imputed: false }],
    } },
  }));
  assert.match(unverifiedShap, /검증에 실패했습니다/);
  assert.doesNotMatch(unverifiedShap, /실제 SOS 이상 점수 기준 Feature별/);
  assert.match(shapPreview, /전체 SHAP 값과 계산 근거/);
  assert.match(candidateWithContext, /실제 사건을 확정하지 않습니다/);
  // 인접 점수 구간의 경계값은 한 구간에만 속해야 한다.
  const scoreItems = [
    { row_index: 1, anomaly_score: 0.1, features: {} },
    { row_index: 2, anomaly_score: 0.2, features: {} },
    { row_index: 3, anomaly_score: 0.3, features: {} },
  ];
  assert.deepEqual(filterCandidatesByScoreRange(scoreItems,
    { lower: 0.1, upper: 0.2, includeUpper: false }).map(x => x.row_index), [1]);
  assert.deepEqual(filterCandidatesByScoreRange(scoreItems,
    { lower: 0.2, upper: 0.3, includeUpper: true }).map(x => x.row_index), [2, 3]);
  assert.deepEqual(filterCandidatesByScoreRange(scoreItems, null).map(x => x.row_index), [1, 2, 3]);
  const props = {
    datasets: [], initialDatasetId: "", initialRunId: "",
    hasMoreDatasets: false, onLoadMoreDatasets: async () => {},
    onSelectDataset: () => {}, onSelectRun: () => {},
    canRun: true, canCancel: true, onNavigate: () => {}, onOpenResource: () => {},
  };
  // 비어 있는 첫 진입에서도 제공 기능과 실행 가능한 예제를 발견할 수 있어야 한다.
  const emptyJourney = renderToStaticMarkup(React.createElement(PipelineWorkspace, {
    ...props, hasPipelineExample: true, canCreateDataset: true,
    example: { id: "activity-anomaly-if", row_count: 240, columns: ["ts", "io_bytes_per_sec"],
      description: "44개 활동 Feature가 있는 합성 데이터", title: "활동 이상 탐지 예제",
      guide: "입력과 점수를 확인합니다", version: "v1" },
    onUseExample: async () => ({ id: "example", name: "example", version: "v1", kind: "file" }),
  }));
  assert.match(emptyJourney, /예제로 모델 학습 시작/);
  assert.match(emptyJourney, /id="pipeline-example-start"/);
  assert.match(emptyJourney, /내 Dataset으로 시작/);
  assert.doesNotMatch(emptyJourney, /어떤 과정을 경험하나요|이해를 돕는 개념도|예제 구성 살펴보기/);
  assert.doesNotMatch(emptyJourney, /id="model-lab-example-preview"/);
  assert.doesNotMatch(emptyJourney, /학습 결과.*이상 후보.*다른 데이터에 적용/);
  assert.doesNotMatch(emptyJourney, /이 데이터로 분석 실행하기/);
  const withoutPermission = renderToStaticMarkup(React.createElement(PipelineWorkspace, {
    ...props, hasPipelineExample: true, canCreateDataset: false, canRun: false,
  }));
  assert.match(withoutPermission, /id="pipeline-example-start"[^>]*disabled=""/);
  const withoutExample = renderToStaticMarkup(React.createElement(PipelineWorkspace, {
    ...props, hasPipelineExample: false, examplesLoading: false, canCreateDataset: true,
  }));
  assert.match(withoutExample, /id="pipeline-example-start"[^>]*disabled=""/);
  const { ModelLabIntroduction } = await vite.ssrLoadModule("/src/ModelLabIntroduction.tsx");
  const unavailableExample = renderToStaticMarkup(React.createElement(ModelLabIntroduction, {
    definition: null, examplesLoading: false, exampleBusy: false, examplePhase: null,
    canCreateDataset: true, canRun: true, catalogError: "",
    examplesError: "temporary failure", onRetryExamples: () => {},
    onRetryCatalog: () => {}, onStartExample: () => {}, onOwnData: () => {},
  }));
  assert.match(unavailableExample, /예제 목록을 불러오지 못했습니다/);
  assert.match(unavailableExample, /예제 다시 조회/);
  assert.match(unavailableExample, /id="pipeline-example-start"[^>]*disabled=""/);
  const { ExampleGallery } = await vite.ssrLoadModule("/src/ExampleGallery.tsx");
  const failedGallery = renderToStaticMarkup(React.createElement(ExampleGallery, {
    examples: [], busy: false, loading: false, canUse: true,
    error: "temporary failure", onRetry: () => {}, onUse: () => {},
  }));
  assert.match(failedGallery, /예제 다시 조회/);
  assert.doesNotMatch(failedGallery, /현재 제공되는 예제가 없습니다/);
  const selectedJourney = renderToStaticMarkup(React.createElement(PipelineWorkspace, {
    ...props, datasets: [{ id: "dataset-1", name: "활동 데이터", version: "revision-1", kind: "file" }],
    initialDatasetId: "dataset-1",
  }));
  assert.match(selectedJourney, /<option value="dataset-1" selected=""/);
  assert.match(selectedJourney, /입력 조건 확인/);
  // 장기 비전 Preview 주소는 홈으로 이동하되 기존 ML 결과 URL은 보존한다.
  assert.equal(routeAt("/research", "").tab, "overview");
  assert.equal(routeAt("/model-experience", "").tab, "overview");
  assert.equal(routeAt("/models/model-v1", "").resource?.kind, "model");
  assert.equal(routeAt("/scoring/results/score-v1", "").resource?.kind, "scoring");
  const { PlatformOverview } = await vite.ssrLoadModule("/src/PlatformOverview.tsx");
  const home = renderToStaticMarkup(React.createElement(PlatformOverview, {
    datasets: [], onNavigate: () => {}, onOpenDataset: () => {},
    onOpenPipelineRun: () => {},
  }));
  assert.match(home, /무엇을 시작할까요/);
  assert.match(home, /활동 이상 탐지/);
  assert.doesNotMatch(home, /최근 ML 실행/);
  const { PipelineHistoryPanel } = await vite.ssrLoadModule("/src/PipelineHistoryPanel.tsx");
  const historyPanel = renderToStaticMarkup(React.createElement(PipelineHistoryPanel, {
    datasets: [], onOpenRun: () => {}, onStart: () => {},
  }));
  assert.match(historyPanel, /활동 이상 탐지 분석 이력/);
  const { ActivityWorkspace } = await vite.ssrLoadModule("/src/ActivityWorkspace.tsx");
  const activity = renderToStaticMarkup(React.createElement(ActivityWorkspace, {
    active: "pipeline", onChange: () => {}, onNavigate: () => {},
    onOpenResource: () => {}, onOpenPipelineRun: () => {}, datasets: [], canCancel: false,
  }));
  assert.match(activity, /모델 실험실 분석/);
  assert.match(activity, /활동 이상 탐지 분석 이력/);
  const { PipelineModelUsage } = await vite.ssrLoadModule("/src/PipelineModelUsage.tsx");
  const useModel = renderToStaticMarkup(React.createElement(PipelineModelUsage, {
    modelId: "model-v1", datasets: [], canRun: true, canCancel: true, onOpenResource: () => {},
  }));
  assert.match(useModel, /학습한 모델 사용해보기/);
  const deepLinkedJourney = renderToStaticMarkup(React.createElement(PipelineWorkspace, {
    ...props, initialRunId: "previous-run",
  }));
  assert.match(deepLinkedJourney, /선택한 실행을 불러오는 중입니다/);
  assert.match(deepLinkedJourney, /aria-labelledby="pipeline-run-heading"/);
  assert.doesNotMatch(deepLinkedJourney, /pipeline-run-context-header/);
  assert.doesNotMatch(deepLinkedJourney, /비지도 학습/);
  assert.doesNotMatch(deepLinkedJourney, /예제로 모델 학습 시작/);
  assert.doesNotMatch(deepLinkedJourney, /내 데이터로 학습하거나 분석 방법 자세히 보기/);
  assert.match(deepLinkedJourney, /새 모델 학습하기/);
  assert.match(deepLinkedJourney, /선택한 실행/);
  assert.doesNotMatch(deepLinkedJourney, /aria-label="분석 이용 단계"/);
  assert.doesNotMatch(deepLinkedJourney, /아직 선택된 실행이 없습니다/);
  const completedRun = {
    id: "run-demo", definition_id: "activity-anomaly-if", status: "Succeeded",
    dataset_id: "dataset-1", dataset_version: "v1",
    stages: [
      { name: "training", job: { result: { model_revision_id: "model-a" } } },
      { name: "calibration", job: { result: { scoring_result_id: "cal-a" } } },
      { name: "policy", job: { result: { policy_revision_id: "policy-a" } } },
      { name: "test", job: { result: { scoring_result_id: "test-a" } } },
      { name: "evaluation", job: { result: { evaluation_revision_id: "eval-a" } } },
    ],
    input_plan: {
      timestamp_column: "ts", train_end: "2026-01-02T00:00:00+00:00",
      calibration_end: "2026-01-03T00:00:00+00:00",
      partition_sizes: { train: 60, calibration: 20, test: 20 },
    },
  };
  const temporalScope = partition => ({
    partition, timestamp_column: "ts", train_end: "2026-01-02T00:00:00+00:00",
    calibration_end: "2026-01-03T00:00:00+00:00",
    model_revision_id: "model-a", dataset_id: "dataset-1", dataset_version: "v1",
    train_rows: 60, calibration_rows: 20, test_rows: 20,
  });
  const resultEvidence = {
    model: { id: "model-a", algorithm: "isolation_forest", params: { n_estimators: 100 },
      dataset_id: "dataset-1", dataset_version: "v1", features: ["f1"],
      runtime: { preprocessing_mode: "train_median_v1" },
      training_score_summary: {
        score_min: 0.1, score_mean: 0.2, score_max: 0.4, score_count: 60,
        histogram: { method: "equal_width_exact_v1", bin_edges: [0.1, 0.25, 0.4],
          bin_counts: [30, 30], score_count: 60, constant_scores: false },
      } },
    calibration: { id: "cal-a", dataset_id: "dataset-1", dataset_version: "v1",
      model_revision_id: "model-a", row_count: 20, score_direction: "higher_is_more_anomalous",
      feature_names: ["f1"], score_summary: {
      temporal_scope: temporalScope("calibration"),
      score_min: 0.15, score_mean: 0.25, score_max: 0.5, score_count: 20,
      histogram: { method: "equal_width_exact_v1", bin_edges: [0.15, 0.325, 0.5],
        bin_counts: [10, 10], score_count: 20, constant_scores: false },
    } },
    test: { id: "test-a", dataset_id: "dataset-1", dataset_version: "v1",
      model_revision_id: "model-a", row_count: 20, score_direction: "higher_is_more_anomalous",
      feature_names: ["f1"], score_summary: {
      temporal_scope: temporalScope("test"),
      score_min: 0.2, score_mean: 0.3, score_max: 0.6, score_count: 20,
      histogram: { method: "equal_width_exact_v1", bin_edges: [0.2, 0.4, 0.6],
        bin_counts: [15, 5], score_count: 20, constant_scores: false },
    } },
    policy: { id: "policy-a", mode: "quantile", alert_fraction: 0.005,
      threshold: 0.47, scoring_result_id: "cal-a",
      decision_summary: { score_count: 20, predicted_anomalies: 2 } },
    evaluation: { id: "eval-a", scoring_result_id: "test-a",
      policy_revision_id: "policy-a", label_coverage: "none",
      decision_metrics: { score_count: 20, predicted_anomalies: 3, alert_rate: 0.15 } },
  };
  assert.equal(validatePipelineEvidence(completedRun, resultEvidence), null);
  assert.match(validatePipelineEvidence(completedRun, {
    ...resultEvidence,
    calibration: { ...resultEvidence.calibration, score_summary: {
      ...resultEvidence.calibration.score_summary, temporal_scope: temporalScope("test"),
    } },
  }), /시간 분할/);
  assert.match(validatePipelineEvidence(completedRun, {
    ...resultEvidence, test: { ...resultEvidence.test, feature_names: ["other_feature"] },
  }), /Feature 서명/);
  assert.match(validatePipelineEvidence(completedRun, {
    ...resultEvidence, evaluation: { ...resultEvidence.evaluation, decision_metrics: {
      ...resultEvidence.evaluation.decision_metrics, predicted_anomalies: 4,
    } },
  }), /평가 근거/);
  const reportFor = initialSection => renderToStaticMarkup(React.createElement(PipelineEvidenceView, {
    run: completedRun, evidence: resultEvidence, initialSection, onOpenResource: () => {},
  }));
  const report = reportFor("overview");
  assert.match(report, /현재 분석 근거 탐색/);
  assert.match(report, /aria-pressed="true"/);
  assert.match(report, /실제 최소·평균·최대 이상 점수/);
  assert.match(report, /Training 점수 구간별 전체 관측 수/);
  assert.match(report, /Calibration 점수 구간별 전체 관측 수/);
  assert.match(report, /Test 점수 구간별 전체 관측 수/);
  const trainingReport = reportFor("training");
  assert.match(trainingReport, /Training — 무엇을 학습했는가/);
  assert.match(trainingReport, /Training 점수 구간별 전체 관측 수/);
  const interactiveOverview = renderToStaticMarkup(React.createElement(PipelineEvidenceView, {
    run: completedRun, evidence: resultEvidence, onOpenResource: () => {},
    onSelectTestScoreRange: () => {}, onInvestigateCandidates: () => {},
  }));
  assert.match(interactiveOverview, /이상 후보 확인하기/);
  assert.match(interactiveOverview, /점수 분포와 판정 기준 자세히 보기/);
  assert.match(interactiveOverview, /이 구간 후보 보기/);
  assert.match(interactiveOverview, /후보 탐색/);
  assert.match(interactiveOverview, /그래프 막대나 아래 집계 표에서 점수 구간을 선택/);
  const calibrationReport = reportFor("calibration");
  assert.match(calibrationReport, /Calibration — 기준은 어떻게 정했는가/);
  assert.match(calibrationReport, /0.5%/);
  assert.match(calibrationReport, /Calibration 점수 구간별 전체 관측 수/);
  const testReport = reportFor("test");
  const testWithCandidates = renderToStaticMarkup(React.createElement(PipelineEvidenceView, {
    run: completedRun, evidence: resultEvidence, initialSection: "test",
    onInvestigateCandidates: () => {}, onOpenResource: () => {},
  }));
  assert.match(testWithCandidates, /검토할 이상 후보 살펴보기/);
  assert.match(testReport, /Test — 적용 후 무엇이 달라졌는가/);
  assert.match(testReport, /15%/);
  assert.match(testReport, /Test 점수 구간별 전체 관측 수/);
  const sensitivity = renderToStaticMarkup(React.createElement(SensitivityChart, {
    result: { output: { feature_sensitivities: [
      { feature: "f1", anomaly_score_delta: 0.12 },
    ] } },
  }));
  assert.match(sensitivity, /SHAP·가법 기여도·원인 판정이 아닙니다/);
  const explanationJobSummary = (id, scoring_result_id, row_index, created_at) => ({
    id, spec: { scoring_result_id, row_index }, created_at,
  });
  assert.equal(matchingExplanationJob([
    explanationJobSummary("older", "score-1", 5, "2026-10-20T12:00:00Z"),
    explanationJobSummary("other-row", "score-1", 6, "2026-10-22T12:00:00Z"),
    explanationJobSummary("latest", "score-1", 5, "2026-10-23T12:00:00Z"),
  ], "score-1", 5)?.id, "latest");
  assert.equal(matchingExplanationJob([
    explanationJobSummary("other", "score-2", 5, "2026-10-23T12:00:00Z"),
  ], "score-1", 5), undefined);
  assert.match(renderToStaticMarkup(React.createElement(PipelineCandidateInvestigation, {
    candidate: { row_index: 5, anomaly_score: 0.8, features: { "io_bytes_per_sec": 42 } },
    scoringResultId: "score-1", canRun: true, canCancel: true, onOpenResource: () => {},
  })), /이 후보의 점수 민감도 계산/);
  assert.match(renderToStaticMarkup(React.createElement(PipelineAnalysis, {
    run: { id: "run-1", stages: [], status: "Succeeded" }, onOpenResource: () => {},
  })), /분석 판본 참조가 불완전합니다/);

  console.log("ML / Pipeline navigation and Consumer entry: assertions passed");
} finally {
  await vite.close();
}
