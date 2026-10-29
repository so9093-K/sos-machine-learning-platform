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
    ModelAreaNavigation, PlatformNavigation } = await vite.ssrLoadModule("/src/PlatformNavigation.tsx");

  const { activityFeatureLabel } = await vite.ssrLoadModule("/src/ActivityFeatureLabels.ts");
  assert.equal(activityFeatureLabel("cnt_proc_exec"), "프로세스 실행 횟수");
  assert.equal(activityFeatureLabel("unknown_custom"), "unknown_custom");
  const { groupActivityFeatures } = await vite.ssrLoadModule("/src/ActivityFeatureGroups.ts");
  const activityGroups = groupActivityFeatures({
    cnt_file_create: 3, cnt_proc_exec: 2, ratio_outbound_traffic: 0.4,
    io_bytes_per_sec: 7, external_feature: null,
  });
  assert.deepEqual(activityGroups.map(group => group.title), [
    "파일 활동", "프로세스 활동", "네트워크 활동", "활동량·시간 특성", "그 밖의 입력 Feature",
  ]);
  assert.equal(activityGroups.flatMap(group => group.entries).length, 5);
  const representativeGroups = groupActivityFeatures({
    cnt_file_create: 0, cnt_file_write: 9, uniq_file_touched: 5, max_write_size: 12,
    cnt_net_tcp: 4, ratio_outbound_traffic: 0.85, uniq_dest_ip: 7, net_bytes_per_sec: 20,
  });
  assert.equal(new Set(representativeGroups.flatMap(group =>
    group.entries.map(([name]) => name))).size, 8);
  // 독립 Scoring 결과는 별도 접기·선택 없이 전체 근거를 바로 제공한다.
  const { ScoringObservationExplorer, observationSelectionLabel } =
    await vite.ssrLoadModule("/src/ScoringObservationExplorer.tsx");
  assert.equal(observationSelectionLabel(false), "기본 표시");
  assert.equal(observationSelectionLabel(true), "현재 선택");
  const resultView = renderToStaticMarkup(React.createElement(ScoringObservationExplorer, {
    result: {
      id: "score-1", model_revision_id: "model-1", dataset_id: "dataset-1",
       dataset_version: "version-1", created_at: "2026-10-27T00:00:00Z", row_count: 3,
      score_summary: {
        score_min: 0.1, score_max: 0.3, score_mean: 0.2,
        histogram: { method: "equal_width_exact_v1", score_count: 3,
          bin_edges: [0.1, 0.2, 0.3], bin_counts: [1, 2] },
      },
    },
    canRun: false, canCancel: false, onOpenResource: () => {},
  }));
  assert.match(resultView, /전체 이상 점수 분포/);
  assert.match(resultView, /분석한 관측값/);
  assert.match(resultView, /최고 이상 점수/);
  assert.match(resultView, /평균 이상 점수/);
  assert.doesNotMatch(resultView, /선택 관측값 점수/);
  assert.match(resultView, /점수순 조회/);
  assert.match(resultView, /점수 상위 관측값/);
  assert.match(resultView, /분석 판본과 식별자/);
  assert.match(resultView, /evidence-facts-compact/);
  assert.match(resultView, /Dataset 판본/);
  assert.match(resultView, /Model Revision/);
  assert.match(resultView, /Scoring Result/);
  assert.match(resultView, /version-1/);
  assert.match(resultView, /score-1/);
  assert.match(resultView, /점수가 높은 관측값/);
  assert.match(resultView, /사건 발생 확률을 뜻하지 않습니다/);
  // 히스토그램이 없는 이전 결과에는 분포나 선택 위치를 추정하지 않는다.
  const resultWithoutHistogram = renderToStaticMarkup(React.createElement(ScoringObservationExplorer, {
    result: {
      id: "score-no-hist", model_revision_id: "model-1", dataset_id: "dataset-1",
      dataset_version: "version-1", created_at: "2026-10-27T00:00:00Z",
      row_count: 3, score_summary: { score_mean: 0.2, score_min: 0.1, score_max: 0.3 },
    },
    canRun: false, canCancel: false, onOpenResource: () => {},
  }));
  assert.match(resultWithoutHistogram, /검증 가능한 전체 점수 분포가 없습니다/);
  assert.doesNotMatch(resultWithoutHistogram, /점선은 선택한 관측값/);

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
    active: "topic", canInspectStorage: false, canViewAudit: false,
    onNavigate: () => {},
  }));
  assert.match(catalogPendingMenu, /활동 이상 탐지 화면 열기/);
  assert.match(catalogPendingMenu, /href="\/topics\/activity-anomaly"/);
  // 학습·실험 제작 화면은 웹 메뉴와 경로에 없다.
  assert.doesNotMatch(catalogPendingMenu, /href="\/research|href="\/training|학습·실험 관리|제작 도구/);
  assert.equal(routeAt("/training", "").tab, "overview");
  assert.equal(routeAt("/research", "").tab, "overview");
  assert.equal(routeAt("/pipelines", "").tab, "pipelines");
  const pipelinePath = contextPath("pipelines", { pipelineRunId: "run/one", datasetId: "data one" });
  assert.equal(pipelinePath, "/pipelines/runs/run%2Fone?dataset=data+one");
  const pipelineRoute = routeAt("/pipelines/runs/run%2Fone", "?dataset=data+one");
  assert.deepEqual(pipelineRoute.context, { pipelineRunId: "run/one", datasetId: "data one" });
  const pipelineScorePath = resourcePath("scoring", "test-score", { pipelineRunId: "run/one" });
  assert.equal(pipelineScorePath, "/scoring/results/test-score?pipelineRun=run%2Fone");
  const pipelineScore = routeAt("/scoring/results/test-score", "?pipelineRun=run%2Fone");
  const pipelinePolicy = nextNavigationContext(pipelineScore, "policies");
  assert.deepEqual(pipelinePolicy, { scoringId: "test-score", pipelineRunId: "run/one" });
  assert.equal(contextPath("policies", pipelinePolicy), "/policies?scoring=test-score&pipelineRun=run%2Fone");
  assert.deepEqual(nextNavigationContext(pipelineScore, "pipelines"), { pipelineRunId: "run/one" });
  const pipelineData = routeAt("/data", "?dataset=data-one&pipelineRun=run-one");
  assert.equal(contextPath("preview", nextNavigationContext(pipelineData, "preview")),
    "/data/query?dataset=data-one&pipelineRun=run-one");
  const pipelineHiddenMenu = renderToStaticMarkup(React.createElement(PlatformNavigation, {
    active: "overview", canInspectStorage: false, canViewAudit: false, canViewRuns: false, onNavigate: () => {},
  }));
  assert.doesNotMatch(pipelineHiddenMenu, /href="\/pipelines"/);
  const { pipelineResultId, PipelineResults } = await vite.ssrLoadModule("/src/PipelineRunDetails.tsx");
  const partialRun = { id: "partial-run", status: "Failed", stages: [
    { name: "training", job: { status: "Succeeded", result: { model_revision_id: "model-ready" } } },
    { name: "test", job: { status: "Failed", result: { scoring_result_id: "unconfirmed" } } },
  ] };
  assert.equal(pipelineResultId(partialRun, "training", "model_revision_id"), "model-ready");
  assert.equal(pipelineResultId(partialRun, "test", "scoring_result_id"), null);
  const partialResults = renderToStaticMarkup(React.createElement(PipelineResults, {
    run: partialRun, revision: 0, canRun: false, canCancel: false, canOpenMLflow: false,
    onOpenResource: () => {}, onOpenDataset: () => {},
  }));
  assert.match(partialResults, /학습 결과/);
  assert.doesNotMatch(partialResults, /Test 결과·평가|이상 후보 조사/);
  const scoringId = "score-v1";
  const first = routeAt("/evaluations", "?scoring=" + scoringId);
  assert.equal(first.tab, "evaluations");
  assert.equal(first.context.scoringId, scoringId);
  assert.equal(contextPath("policies", nextNavigationContext(first, "policies")),
    "/policies?scoring=score-v1");
  assert.equal(contextPath("explanations", nextNavigationContext(first, "explanations")),
    "/explanations?scoring=score-v1");
  assert.deepEqual(nextNavigationContext(first, "ml"), {});

  assert.equal(routeAt("/scoring", "?model=model-v1").context.modelId, "model-v1");
  assert.equal(contextPath("topic", { modelId: "model one" }), "/topics/activity-anomaly?model=model+one");
  assert.equal(routeAt("/topics/activity-anomaly", "?model=model-one").context.modelId, "model-one");
  assert.deepEqual(routeAt("/topics/activity-anomaly", "").context, {});
  assert.equal(contextPath("scoring", { modelId: "name + / ?" }),
    "/scoring?model=name+%2B+%2F+%3F");
  assert.equal(routeAt("/policies", "").context.scoringId, "");
  assert.equal(routeAt("/data", "?scoring=unrelated").context.scoringId, undefined);
  assert.equal(contextPath("preview", nextNavigationContext(routeAt("/data", "?dataset=data-v1"), "preview")),
    "/data/query?dataset=data-v1");

  const immutablePath = resourcePath("scoring", "score/with space");
  assert.deepEqual(routeAt("/scoring/results/score%2Fwith%20space", "?scoring=other").resource,
    { kind: "scoring", id: "score/with space" });
  assert.equal(immutablePath, "/scoring/results/score%2Fwith%20space");
  assert.deepEqual(routeAt("/models/model-v1", "").context, {});
  const fromModelLab = resourcePath("scoring", "score/one", {
    modelLabReturn: true, modelId: "model/a",
  });
  assert.equal(fromModelLab, "/scoring/results/score%2Fone?from=model-lab&model=model%2Fa");
  assert.deepEqual(routeAt("/scoring/results/score%2Fone",
    "?from=model-lab&model=model%2Fa").context,
    { modelLabReturn: true, modelId: "model/a" });
  const labDecision = contextPath("policies", {
    scoringId: "score one", modelLabReturn: true, modelId: "model/a",
  });
  assert.equal(labDecision,
    "/policies?scoring=score+one&from=model-lab&model=model%2Fa");
  const labDecisionRoute = routeAt("/policies",
    "?scoring=score+one&from=model-lab&model=model%2Fa");
  assert.deepEqual(labDecisionRoute.context, {
    scoringId: "score one", modelLabReturn: true, modelId: "model/a",
  });
  assert.equal(contextPath("explanations", nextNavigationContext(
    labDecisionRoute, "explanations")),
    "/explanations?scoring=score+one&from=model-lab&model=model%2Fa");
  assert.deepEqual(routeAt("/models/model-one",
    "?from=model-lab&model=model%2Fa").context,
    { modelLabReturn: true, modelId: "model/a" });
  assert.deepEqual(routeAt("/scoring/results/score-one", "?from=model-lab").context, {});
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
  assert.match(modelNav, /<details class="model-tools">/);
  const { ModelCatalog } = await vite.ssrLoadModule("/src/ModelCatalog.tsx");
  const catalog = renderToStaticMarkup(React.createElement(ModelCatalog, {
    onOpenModel: () => {}, onScoreModel: () => {},
  }));
  assert.match(catalog, /제공 모델/);
  assert.match(catalog, /조회 전용/);
  assert.doesNotMatch(catalog, /새 학습|모델 그룹|학습·실험/);

  // 데이터 화면 사이 이동에서 선택한 Dataset과 작업 문맥을 유지한다.
  assert.equal(routeAt("/data", "?dataset=chosen").context.datasetId, "chosen");
  for (const [tab, path] of [["understanding", "/data"], ["preview", "/data/query"],
      ["profile", "/data/profile"], ["preparation", "/data/prepare"]]) {
    const target = contextPath(tab, { datasetId: "data / one" });
    assert.equal(routeAt(path, target.slice(target.indexOf("?"))).context.datasetId, "data / one");
  }
  assert.deepEqual(nextNavigationContext(routeAt("/data", "?dataset=one"), "preview"),
    { datasetId: "one" });
  // 데이터 탐색 → 목록 → 모델 상세 → 적용과 직접 URL에서 선택 Dataset을 이어 간다.
  const sourceData = routeAt("/data", "?dataset=data+one");
  const modelsPath = contextPath("ml", nextNavigationContext(sourceData, "ml"));
  assert.equal(modelsPath, "/models?dataset=data+one");
  const modelDetailPath = resourcePath("model", "model-a", sourceData.context);
  assert.equal(modelDetailPath, "/models/model-a?dataset=data+one");
  const modelDetail = routeAt("/models/model-a", "?dataset=data+one");
  const applyContext = nextNavigationContext(modelDetail, "scoring");
  assert.deepEqual(routeAt("/scoring", contextPath("scoring", applyContext).split("?")[1]).context,
    { modelId: "model-a", datasetId: "data one" });
  const withDatasetNav = renderToStaticMarkup(React.createElement(PlatformNavigation, {
    active: "understanding", context: sourceData.context, resource: null,
    canInspectStorage: false, canViewAudit: false, onNavigate: () => {},
  }));
  assert.match(withDatasetNav, /href="\/models\?dataset=data\+one"/);
  assert.equal(contextPath("topic", nextNavigationContext(sourceData, "topic")),
    "/topics/activity-anomaly?dataset=data+one");
  const { checkModelDatasetInput, ModelDatasetCompatibility } = await vite.ssrLoadModule("/src/ModelDatasetCompatibility.tsx");
  const contract = { features: ["count", "ratio"], runtime: { feature_dtypes: ["Int64", "Float64"] } };
  assert.equal(checkModelDatasetInput(contract, [
    { name: "ratio", dtype: "Float64" }, { name: "count", dtype: "Int64" },
  ]).ready, true); // Dataset의 물리적 컬럼 순서와 무관하게 모델 순서로 자료형을 비교한다.
  assert.deepEqual(checkModelDatasetInput(contract, [{ name: "count", dtype: "Int64" }]).missing, ["ratio"]);
  assert.equal(checkModelDatasetInput(contract, [
    { name: "count", dtype: "Float64" }, { name: "ratio", dtype: "Float64" },
  ]).ready, false);
  assert.equal(checkModelDatasetInput(contract, [
    { name: "count", dtype: "String" }, { name: "ratio", dtype: "Float64" },
  ]).ready, false);
  assert.equal(checkModelDatasetInput({ ...contract, runtime: {} }, []).ready, false);
  const mismatchNotice = renderToStaticMarkup(React.createElement(ModelDatasetCompatibility, {
    model: contract, columns: [{ name: "count", dtype: "Int64" }], compact: true,
  }));
  assert.match(mismatchNotice, /선택한 Dataset에 적용할 수 없음/);
  const jobContext = { datasetId: "one", dataJobId: "job / one", dataJobKind: "exploration" };
  const jobPath = contextPath("jobs", jobContext);
  assert.deepEqual(routeAt("/activity/jobs", jobPath.slice(jobPath.indexOf("?"))).context, jobContext);
  assert.equal(nextNavigationContext(routeAt("/activity/jobs", jobPath.slice(jobPath.indexOf("?"))), "preview").dataJobId, undefined);
  const { dataJobLabel } = await vite.ssrLoadModule("/src/JobsPanel.tsx");
  assert.equal(dataJobLabel({ kind: "feature_register", spec: {} }), "Feature Service 등록");
  assert.equal(dataJobLabel({ kind: "feature_retrieve", spec: {} }), "Feature 시점 조회");
  assert.equal(dataJobLabel({ kind: "feature_retire", spec: {} }), "Feature Service 폐기");
  const { ObservationInvestigation } = await vite.ssrLoadModule("/src/ObservationInvestigation.tsx");
  const { SensitivityChart, formatSensitivityValue } = await vite.ssrLoadModule("/src/FeatureSensitivity.tsx");
  const { matchingExplanationJob } = await vite.ssrLoadModule("/src/ObservationSensitivity.tsx");
  const candidateWithContext = renderToStaticMarkup(
    React.createElement(ObservationInvestigation, {
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
  assert.match(candidateWithContext, /학습 중앙값/);
  assert.match(candidateWithContext, /입력값 확인 · 1개 항목/);
  assert.match(candidateWithContext, /점수 기여도/);
  const groupedObservation = renderToStaticMarkup(
    React.createElement(ObservationInvestigation, {
      candidate: {
        row_index: 9, anomaly_score: 0.71,
        features: { cnt_file_create: 3, cnt_proc_exec: 4, ratio_outbound_traffic: 0.6 },
      }, scoreOrder: 2, observationMode: true,
      scoringResultId: "score-1", modelRevisionId: null,
      canRun: false, canCancel: false, onOpenResource: () => {},
    }));
  assert.match(groupedObservation, /선택한 관측값/);
  const defaultObservation = renderToStaticMarkup(
    React.createElement(ObservationInvestigation, {
      candidate: {
        row_index: 0, anomaly_score: 0.91, features: { cnt_file_create: 2 },
        observed_at: "2026-01-01T02:00:00+00:00",
      },
      scoreOrder: 1, observationMode: true, isDefaultObservation: true,
      scoringResultId: "score-1", modelRevisionId: null,
      canRun: false, canCancel: false, onOpenResource: () => {},
    }));
  assert.match(defaultObservation, /첫 번째 관측값/);
  assert.match(defaultObservation, /기본 표시 관측값 분석/);
  assert.match(defaultObservation, /2026-01-01 02:00:00 UTC/);
  assert.doesNotMatch(defaultObservation, /관측 시각·원본 관측치 ID 없음/);
  assert.match(groupedObservation, /활동 선택/);
  const observationWithExperiment = renderToStaticMarkup(
    React.createElement(ObservationInvestigation, {
      candidate: {
        row_index: 9, anomaly_score: 0.71,
        features: { cnt_file_create: 3, cnt_proc_exec: 4 },
      }, observationMode: true, scoringResultId: "score-1", modelRevisionId: "model-1",
      canRun: true, canCancel: false, onOpenResource: () => {},
      onTryObservation: () => null,
    }));
  assert.match(observationWithExperiment, /이 관측값으로 실험/);
  assert.doesNotMatch(groupedObservation, /이 관측값으로 실험/);
  const noPermissionExperiment = renderToStaticMarkup(
    React.createElement(ObservationInvestigation, {
      candidate: { row_index: 9, anomaly_score: 0.71, features: { cnt_file_create: 3 } },
      observationMode: true, scoringResultId: "score-1", modelRevisionId: "model-1",
      canRun: false, canCancel: false, onOpenResource: () => {}, onTryObservation: () => null,
    }));
  assert.doesNotMatch(noPermissionExperiment, /이 관측값으로 실험/);
  // 참조 모델이 없으면 비율을 추정하지 않고 비교 기준 없음으로 표시한다.
  assert.match(groupedObservation, /학습 중앙값/);
  assert.match(groupedObservation, /모델 판본 없음/);
  assert.doesNotMatch(groupedObservation, /feature-reference-bars/);
  assert.match(groupedObservation, /observation-activity-picker/);
  assert.match(groupedObservation, /입력값 확인 · 3개 항목/);
  assert.match(groupedObservation, /관측 시각·원본 관측치 ID 없음/);
  assert.match(groupedObservation, /결과 내부 행 #9/);
  assert.doesNotMatch(groupedObservation, /scoring-activity-group-details/);
  assert.match(groupedObservation, /점수순 2번째/);
  assert.doesNotMatch(groupedObservation, /기본 미리보기|observation-selected-feature/);
  assert.ok(groupedObservation.indexOf("활동 선택") <
    groupedObservation.indexOf("점수 기여도"));
  assert.match(groupedObservation, /학습 모델 판본 참조가 없어/);
  const pipelineViewOnly = renderToStaticMarkup(
    React.createElement(ObservationInvestigation, {
      candidate: { row_index: 2, anomaly_score: 0.2, features: { cnt_proc_exec: 2 } },
      scoringResultId: "score-1", modelRevisionId: null,
      canRun: false, canCancel: false, onOpenResource: () => {},
    }));
  assert.match(pipelineViewOnly, /observation-activity-picker/);
  assert.ok(pipelineViewOnly.indexOf("입력값 확인 · 1개 항목") <
    pipelineViewOnly.indexOf("점수 기여도"));
  const { ShapContributionChart } = await vite.ssrLoadModule("/src/ShapExplanation.tsx");
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
  assert.match(shapPreview, /모델 점수에 기여한 주요 Feature 1개 \(SHAP\)/);
  assert.match(shapPreview, /Feature 기여도 합/);
  assert.match(shapPreview, /주요 Feature 1개/);
  assert.match(shapPreview, /양수: 점수를 높이는 방향/);
  // 개별 SHAP 기여를 실제 관측값·Train 중앙값과 연결해 확인할 수 있어야 한다.
  assert.match(shapPreview, /선택한 SHAP Feature 근거/);
  assert.match(shapPreview, /Feature 선택/);
  assert.match(shapPreview, /현재 살펴보는 Feature/);
  assert.match(shapPreview, /<select[^>]*>/);
  assert.match(shapPreview, /실제 관측값/);
  const shapForPlayground = renderToStaticMarkup(React.createElement(
    ShapContributionChart, { result: { id: "playground-explanation", output: {
      method: "permutation_shap_median_v1", baseline_score: 0.3, score: 0.5,
      reconstructed_score: 0.5, additivity_residual: 0,
      additive_attribution: true, model_output: "negative_sklearn_score_samples",
      baseline_row_count: 1, feature_contributions: [{
        feature: "outbound_traffic", shap_value: 0.2, actual_value: 4,
        reference_median: 1, missing_imputed: false,
      }],
    } }, valueLabel: "제출한 입력값", inputLabel: "이 제출 입력" }));
  assert.match(shapForPlayground, /제출한 입력값/);
  assert.doesNotMatch(shapForPlayground, /실제 관측값/);
  assert.match(shapPreview, /학습 참조 중앙값/);
  assert.match(shapPreview, /SHAP 점수 기여/);
  assert.match(shapPreview, /outbound_traffic/);
  assert.match(shapPreview, /<strong>0[.]2<\/strong>/);
  const zeroShap = renderToStaticMarkup(React.createElement(ShapContributionChart, {
    result: { id: "reference-input", output: {
      method: "permutation_shap_median_v1", baseline_score: 0.3279, score: 0.3279,
      reconstructed_score: 0.3279, additivity_residual: 0,
      additive_attribution: true, model_output: "negative_sklearn_score_samples",
      baseline_row_count: 1, feature_contributions: [{
        feature: "outbound_traffic", shap_value: 0, actual_value: 1,
        reference_median: 1, missing_imputed: false,
      }],
    } },
  }));
  assert.match(zeroShap, /이 입력의 SHAP 기여도는 모두 0입니다/);
  assert.doesNotMatch(zeroShap, /role="img"/); // SSR 차트 컨테이너 자체가 없어야 한다.
  assert.match(zeroShap, /SHAP 상세 계산 근거/);
  assert.match(zeroShap, /모든 실제 입력 Feature가 학습 참조 중앙값과 일치합니다/);
  const zeroShapWithDifferentInput = renderToStaticMarkup(React.createElement(
    ShapContributionChart, { result: { id: "zero-but-different", output: {
      method: "permutation_shap_median_v1", baseline_score: 0.3279, score: 0.3279,
      reconstructed_score: 0.3279, additivity_residual: 0,
      additive_attribution: true, model_output: "negative_sklearn_score_samples",
      baseline_row_count: 1, feature_contributions: [{
        feature: "outbound_traffic", shap_value: 0, actual_value: 20,
        reference_median: 1, missing_imputed: false,
      }],
    } } }));
  assert.match(zeroShapWithDifferentInput, /기여도가 모두 0이어도 입력 Feature가 학습 중앙값과 같다는 뜻은 아닙니다/);
  assert.doesNotMatch(zeroShapWithDifferentInput, /모든 실제 입력 Feature가 학습 참조 중앙값과 일치합니다/);
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
  assert.match(shapPreview, /SHAP 상세 계산 근거/);
  assert.match(shapPreview, /설명 방법과 기준/);
  assert.match(shapPreview, /설명 방법/);
  assert.match(shapPreview, /Permutation SHAP/);
  assert.match(shapPreview, /모델 점수 출력/);
  assert.match(shapPreview, /-score_samples/);
  assert.match(shapPreview, /합성 관측값 1개/);
  assert.match(shapPreview, /학습 데이터의 실제 분포나 정상 범위를 나타내지 않습니다/);
  assert.match(shapPreview, /재현 설정/);
  assert.match(shapPreview, /순열 반복/);
  assert.match(shapPreview, /Seed/);
  assert.match(shapPreview, /42/);
  assert.match(shapPreview, /0[.]50[.]0/);
  assert.ok(shapPreview.includes("<th>점수 기여</th>"));
  assert.ok(shapPreview.indexOf("설명 방법과 기준") <
    shapPreview.indexOf("재현 설정"));
  // 저장된 독립 Explanation도 SHAP/치환 민감도에 맞게 표시하며 다른 방법을 섞지 않는다.
  const { ExplanationEvidence, explanationSourceMatches } =
    await vite.ssrLoadModule("/src/MLResourcePages.tsx");
  const shapResult = {
    method: "permutation_shap_median_v1",
    scoring_result_id: "score-v1", model_revision_id: "model-v1", row_index: 7,
    output: {
      method: "permutation_shap_median_v1",
      scoring_result_id: "score-v1", model_revision_id: "model-v1", row_index: 7,
      baseline_score: 0.3, score: 0.5,
      reconstructed_score: 0.5, additivity_residual: 0,
      additive_attribution: true, model_output: "negative_sklearn_score_samples",
      baseline_row_count: 1, permutation_rounds: 3, sampling_seed: 42,
      shap_version: "0.50.0",
      feature_contributions: [
        { feature: "outbound_traffic", shap_value: 0.2, actual_value: 4,
          reference_median: 1, missing_imputed: false },
      ],
    },
  };
  assert.equal(explanationSourceMatches(shapResult), true);
  const playgroundResult = {
    ...shapResult,
    scoring_result_id: null, row_index: null, playground_job_id: "playground-job",
    output: { ...shapResult.output, scoring_result_id: null, row_index: null,
      playground_job_id: "playground-job" },
  };
  assert.equal(explanationSourceMatches(playgroundResult), true);
  assert.equal(explanationSourceMatches({
    ...playgroundResult, output: { ...playgroundResult.output,
      playground_job_id: "different-job" },
  }), false);
  assert.equal(explanationSourceMatches({
    ...playgroundResult, scoring_result_id: "score-v1",
  }), false);
  assert.equal(explanationSourceMatches({
    ...shapResult, output: { ...shapResult.output,
      playground_job_id: "unexpected-playground-job" },
  }), false);
  const detailShap = renderToStaticMarkup(React.createElement(ExplanationEvidence, {
    result: shapResult,
  }));
  assert.match(detailShap, /SHAP 모델 점수 기여도/);
  assert.match(detailShap, /Feature 기여도 합/);
  assert.doesNotMatch(detailShap, /참조 중앙값 치환에 따른 이상 점수 민감도/);
  const sensitivityResult = {
    method: "median_reference_feature_replacement",
    scoring_result_id: "score-v1", model_revision_id: "model-v1", row_index: 7,
    output: {
      method: "median_reference_feature_replacement",
      scoring_result_id: "score-v1", model_revision_id: "model-v1", row_index: 7,
      feature_sensitivities: [
        { feature: "outbound_traffic", actual_value: 4, reference_median: 1,
          anomaly_score_delta: 0.1, score_if_replaced: 0.4 },
      ],
    },
  };
  const detailSensitivity = renderToStaticMarkup(React.createElement(ExplanationEvidence, {
    result: sensitivityResult,
  }));
  assert.match(detailSensitivity, /점수 변화가 큰 항목/);
  assert.match(detailSensitivity, /개별 값 확인 · 1개 항목/);
  assert.doesNotMatch(detailSensitivity, /SHAP 모델 점수 기여도/);
  const unsupportedExplanation = renderToStaticMarkup(React.createElement(ExplanationEvidence, {
    result: { method: "unknown_method_v1", output: { method: "unknown_method_v1" } },
  }));
  assert.match(unsupportedExplanation, /표시는 아직 지원하지 않습니다/);
  const mismatchedExplanation = renderToStaticMarkup(React.createElement(ExplanationEvidence, {
    result: { ...shapResult, output: { ...shapResult.output,
      method: "median_reference_feature_replacement" } },
  }));
  assert.match(mismatchedExplanation, /방법과 계산 근거가 일치하지 않습니다/);
  const wrongScoringEvidence = renderToStaticMarkup(React.createElement(ExplanationEvidence, {
    result: { ...shapResult, output: { ...shapResult.output,
      scoring_result_id: "another-scoring" } },
  }));
  assert.match(wrongScoringEvidence, /Scoring·모델·관측 행 판본이 계산 근거와 일치하지 않습니다/);
  assert.doesNotMatch(wrongScoringEvidence, /SHAP 모델 점수 기여도/);
  const missingEvidence = renderToStaticMarkup(React.createElement(ExplanationEvidence, {
    result: { ...sensitivityResult, output: { ...sensitivityResult.output,
      feature_sensitivities: [] } },
  }));
  assert.match(missingEvidence, /Feature별 계산 근거가 없거나 유효하지 않습니다/);
  assert.doesNotMatch(missingEvidence, /chart/);
  const invalidSensitivity = renderToStaticMarkup(React.createElement(ExplanationEvidence, {
    result: { ...sensitivityResult, output: { ...sensitivityResult.output,
      feature_sensitivities: [{ feature: "outbound_traffic", actual_value: 4,
        reference_median: 1, anomaly_score_delta: "invalid", score_if_replaced: 0.4 }] } },
  }));
  assert.match(invalidSensitivity, /Feature별 계산 근거가 없거나 유효하지 않습니다/);
  const missingOutput = renderToStaticMarkup(React.createElement(ExplanationEvidence, {
    result: { ...shapResult, output: null },
  }));
  assert.match(missingOutput, /방법과 계산 근거가 일치하지 않습니다/);
  const { escapeShapTooltipText } = await vite.ssrLoadModule("/src/ShapExplanation.tsx");
  assert.equal(escapeShapTooltipText('<img src=x onerror="test()">&'),
    '&lt;img src=x onerror=&quot;test()&quot;&gt;&amp;');
  const groupShap = renderToStaticMarkup(React.createElement(ShapContributionChart, {
    result: { output: { ...shapResult.output, score: 0.5,
      feature_contributions: Array.from({ length: 10 }, (_, i) => ({
        feature: "feature_" + i, shap_value: 0.02, actual_value: 4,
        reference_median: 1, missing_imputed: false,
      })) } },
  }));
  assert.match(groupShap, /나머지 5개 Feature 기여도 합계/);
  assert.match(groupShap, /단일 Feature의 기여도가 아닙니다/);
  assert.match(groupShap, /주요 Feature 5개/);
  assert.equal((groupShap.match(/<tbody>[\s\S]*?<\/tbody>/)?.[0].match(/<tr>/g) ?? []).length, 6);
  assert.doesNotMatch(groupShap, /실제 모델 이상 점수 기준 주요 8개 Feature와 나머지 합/);
  assert.match(candidateWithContext, /원본 관측치 ID/);
  assert.match(candidateWithContext, /18c4be49-18c4-4f32-9894-8c3bfabbd008/);
  const { ExampleGallery } = await vite.ssrLoadModule("/src/ExampleGallery.tsx");
  const failedGallery = renderToStaticMarkup(React.createElement(ExampleGallery, {
    examples: [], busy: false, loading: false, canUse: true,
    error: "temporary failure", onRetry: () => {}, onUse: () => {},
  }));
  assert.match(failedGallery, /예제 다시 조회/);
  assert.doesNotMatch(failedGallery, /현재 제공되는 예제가 없습니다/);
  assert.equal(routeAt("/models/model-v1", "").resource?.kind, "model");
  assert.equal(routeAt("/scoring/results/score-v1", "").resource?.kind, "scoring");
  const { ModelLabPlayground, exampleValues, playgroundInputMatchesResult,
    playgroundChangedFeatures,
    observationInputForPlayground, playgroundJobVisibleAfterImport } =
    await vite.ssrLoadModule("/src/ModelLabPlayground.tsx");
  const imported = { id: "source-1", modelRevisionId: "model-1", scoringResultId: "sc-1",
    rowIndex: 12, originalScore: 0.7, fields: { a: "2" } };
  assert.equal(playgroundJobVisibleAfterImport(imported, "job-old", "job-old"), false);
  assert.equal(playgroundJobVisibleAfterImport(imported, null, null), false);
  assert.equal(playgroundJobVisibleAfterImport(imported, "job-old", "job-new"), true);
  assert.equal(playgroundJobVisibleAfterImport(null, "job-old", "job-old"), true);
  assert.deepEqual(playgroundChangedFeatures(
    { a: "1e3", b: "0.25", c: "5" }, { a: "1000", b: "0.25", c: "4" },
    ["a", "b", "c"]), ["c"]);
  assert.equal(playgroundInputMatchesResult({ a: "1e3", b: "0.25" },
    { a: 1000, b: 0.25 }, ["a", "b"]), true);
  assert.equal(playgroundInputMatchesResult({ a: "1001", b: "0.25" },
    { a: 1000, b: 0.25 }, ["a", "b"]), false);
  assert.equal(playgroundInputMatchesResult({ a: "", b: "0.25" },
    { a: 1000, b: 0.25 }, ["a", "b"]), false);
  assert.equal(playgroundInputMatchesResult({ a: "1000", b: "0.25" },
    { a: 1000, b: 0.25, c: 1 }, ["a", "b"]), false);
  const { FeatureValueComparison } = await vite.ssrLoadModule("/src/FeatureValueComparison.tsx");
  const numericComparison = renderToStaticMarkup(React.createElement(FeatureValueComparison,
    { actual: 500, reference: 5, actualLabel: "현재 입력값" }));
  assert.match(numericComparison, /현재 입력값/);
  assert.match(numericComparison, /학습 중앙값/);
  assert.match(numericComparison, /500/);
  assert.match(numericComparison, /약 100배/);
  assert.doesNotMatch(numericComparison, /feature-reference-bars|width:/);
  const tinyValue = renderToStaticMarkup(React.createElement(FeatureValueComparison,
    { actual: 0.0000000034, reference: 1 }));
  assert.match(tinyValue, /3[.]400e-9/);
  assert.doesNotMatch(tinyValue, /<strong>0<\/strong>/);
  const noRatio = renderToStaticMarkup(React.createElement(FeatureValueComparison,
    { actual: -5, reference: 0 }));
  assert.match(noRatio, /-5/);
  assert.doesNotMatch(noRatio, /약 .*배|Infinity|NaN/);
  const zeroMedian = renderToStaticMarkup(React.createElement(FeatureValueComparison,
    { actual: 1000, reference: 0 }));
  assert.match(zeroMedian, /학습 중앙값/);
  assert.match(zeroMedian, /\+1,000/);
  assert.doesNotMatch(zeroMedian, /약 .*배|Infinity|NaN/);
  const exampleModel = {
    id: "model-test", features: ["cnt_proc_exec", "cnt_file_del"],
    runtime: { feature_reference_medians: [12, 2], feature_dtypes: ["Int64", "Int64"] },
  };
  assert.deepEqual(exampleValues(exampleModel), { cnt_proc_exec: "12", cnt_file_del: "2" });
  assert.deepEqual(observationInputForPlayground(exampleModel,
    { cnt_proc_exec: 7, cnt_file_del: 3 }),
    { fields: { cnt_proc_exec: "7", cnt_file_del: "3" }, invalid: [] });
  // 학습 참조값과 다르다고 사용자가 관측값을 수정한 것은 아니다.
  assert.deepEqual(playgroundChangedFeatures({ cnt_proc_exec: "7", cnt_file_del: "3" },
    { cnt_proc_exec: "7", cnt_file_del: "3" }, exampleModel.features), []);
  assert.deepEqual(playgroundChangedFeatures({ cnt_proc_exec: "8", cnt_file_del: "3" },
    { cnt_proc_exec: "7", cnt_file_del: "3" }, exampleModel.features), ["cnt_proc_exec"]);
  assert.deepEqual(observationInputForPlayground(exampleModel,
    { cnt_proc_exec: 7, cnt_file_del: null }),
    { fields: null, invalid: ["cnt_file_del"] });
  assert.deepEqual(observationInputForPlayground(exampleModel,
    { cnt_proc_exec: 7.5, cnt_file_del: 3 }),
    { fields: null, invalid: ["cnt_proc_exec"] });
  assert.deepEqual(observationInputForPlayground(exampleModel,
    { cnt_proc_exec: 7, cnt_file_del: Number.NaN }),
    { fields: null, invalid: ["cnt_file_del"] });
  assert.equal(exampleValues({ ...exampleModel, runtime: {} }), null);
  const { playgroundNumberValid } = await vite.ssrLoadModule("/src/ModelLabPlayground.tsx");
  assert.ok(playgroundNumberValid("12", "Int64"));
  assert.ok(!playgroundNumberValid("12.3", "Int64"));
  assert.ok(!playgroundNumberValid("128", "Int8"));
  assert.ok(!playgroundNumberValid("-1", "UInt8"));
  assert.ok(!playgroundNumberValid("1e39", "Float32"));
  assert.ok(playgroundNumberValid("1.75", "Float32"));
  const manualHtml = renderToStaticMarkup(React.createElement(ModelLabPlayground, {
    model: { ...exampleModel, runtime: { feature_dtypes: ["Int64", "Int64"] } },
    canRun: true, canCancel: false,
  }));
  assert.match(manualHtml, /단일 입력 점수 계산/);
  assert.match(manualHtml, /초기 기준값이 없는 모델/);
  assert.match(manualHtml, /입력 확인 2개/);
  const playgroundHtml = renderToStaticMarkup(React.createElement(ModelLabPlayground, {
    model: exampleModel, canRun: true, canCancel: true,
  }));
  assert.doesNotMatch(playgroundHtml, /Dataset 전체에 적용 →/);
  assert.match(playgroundHtml, /프로세스 실행 횟수/);
  assert.match(playgroundHtml, /파일 활동/);
  assert.match(playgroundHtml, /프로세스 활동/);
  assert.doesNotMatch(playgroundHtml, /네트워크 활동/); // 입력에 없는 그룹은 만들지 않는다.
  assert.match(playgroundHtml, /학습 기준값을 채웠습니다/);
  assert.doesNotMatch(playgroundHtml, /model-lab-reference-grid|model-lab-feature-groups/);
  assert.match(playgroundHtml, /입력값을 준비한 뒤 점수를 계산하세요/);
  assert.match(playgroundHtml, /model-lab-workspace/);
  assert.match(playgroundHtml, /실험 결과/);
  const { PlaygroundResult, playgroundScoreChange } = await vite.ssrLoadModule("/src/PlaygroundResult.tsx");
  const { ModelScoreContext, verifiedScoreHistogram } = await vite.ssrLoadModule("/src/ModelScoreContext.tsx");
  const trainSummary = { score_count: 3, score_min: 0.2, score_max: 0.6,
    histogram: { method: "equal_width_exact_v1", score_count: 3,
      bin_edges: [0.2, 0.4, 0.6], bin_counts: [1, 2] } };
  assert.ok(verifiedScoreHistogram(trainSummary, 3));
  assert.equal(verifiedScoreHistogram(trainSummary, 4), null);
  assert.equal(verifiedScoreHistogram({ ...trainSummary, histogram: {
    ...trainSummary.histogram, bin_counts: [2, 2] } }, 3), null);
  assert.equal(verifiedScoreHistogram({ ...trainSummary, histogram: {
    ...trainSummary.histogram, bin_edges: [0.2, 0.1, 0.6] } }, 3), null);
  const contextualModel = { ...exampleModel, training_score_summary: trainSummary };
  const completedInput = { id: "input-2", status: "Succeeded",
    spec: { model_revision_id: exampleModel.id, features: { cnt_proc_exec: 50, cnt_file_del: 2 } },
    result: { anomaly_score: 0.75 } };
  const originalInput = { modelId: exampleModel.id, label: "원래 관측값", score: 0.4,
    fields: { cnt_proc_exec: 12, cnt_file_del: 2 } };
  const inputResult = comparison => renderToStaticMarkup(React.createElement(PlaygroundResult, {
    model: contextualModel, job: completedInput, comparison, matchesInput: true,
    onEdit: () => {}, onRestore: () => {},
  }));
  assert.match(inputResult(originalInput), /입력 1개를 바꾼 결과/);
  assert.match(inputResult(originalInput), /0.400000/);
  assert.match(inputResult(originalInput), /0.750000/);
  assert.match(inputResult(originalInput), /더 높은 이상 점수/);
  assert.match(inputResult(originalInput), /학습 입력에서 나온 최대 점수보다 높습니다/);
  assert.match(inputResult(originalInput), /실제 학습 점수 분포/);
  assert.doesNotMatch(inputResult({ ...originalInput, modelId: "another" }), /원래 관측값|점수 차이/);
  assert.doesNotMatch(inputResult({ ...originalInput, score: null }), /<span>점수 차이<\/span>/);
  assert.match(playgroundScoreChange(0.4, 0.4), /같습니다/);
  assert.match(playgroundScoreChange(0.4, 0.4000001), /차이가 있지만/);
  assert.match(playgroundScoreChange(0.12345649, 0.12345651), /더 높은/);
  assert.match(playgroundScoreChange(0.4, 0.3), /더 낮은/);
  const missingScoreContext = renderToStaticMarkup(React.createElement(ModelScoreContext, {
    model: { ...exampleModel, training_score_summary: {} }, score: 0.75,
  }));
  assert.match(missingScoreContext, /보존돼 있지 않습니다/);
  assert.doesNotMatch(missingScoreContext, /role="img"|점수 범위 안/);
  const applicationContext = { modelId: "model-a", modelLabReturn: true };
  assert.deepEqual(routeAt("/scoring", contextPath("scoring", applicationContext).split("?")[1]).context,
    applicationContext);
  const { PlaygroundExplanations, PlaygroundSensitivity } =
    await vite.ssrLoadModule("/src/PlaygroundExplanations.tsx");
  assert.equal(formatSensitivityValue(0.00000000052), "5.200e-10");
  const explained = renderToStaticMarkup(React.createElement(PlaygroundExplanations, {
    source: { id: "completed-job", status: "Succeeded", spec: { model_revision_id: "model-1" },
      result: { anomaly_score: 0.45 } },
    canRun: true, canCancel: true,
  }));
  assert.match(explained, /SHAP 기여도/);
  assert.match(explained, /추가 실험 · 기준값 치환/);
  assert.doesNotMatch(explained, /설명 방법|Feature 치환 민감도 계산/);
  const playgroundSensitivity = renderToStaticMarkup(React.createElement(PlaygroundSensitivity, {
    result: { id: "explanation-1", output: {
      feature_sensitivities: [
        { feature: "file_create_count", actual_value: 6, reference_median: 2,
          score_if_replaced: 0.3, anomaly_score_delta: 0.15 },
      ],
    } },
  }));
  const zeroSensitivity = renderToStaticMarkup(React.createElement(PlaygroundSensitivity, {
    result: { id: "zero-sensitivity", output: {
      feature_sensitivities: [
        { feature: "file_create_count", actual_value: 2, reference_median: 2,
          score_if_replaced: 0.3, anomaly_score_delta: 0 },
      ],
    } },
  }));
  assert.match(zeroSensitivity, /입력값이 이미 학습 중앙값과 같습니다/);
  const unchangedScore = renderToStaticMarkup(React.createElement(PlaygroundSensitivity, {
    result: { output: { feature_sensitivities: [{ feature: "cnt_file_create", actual_value: 5,
      reference_median: 2, score_if_replaced: 0.3, anomaly_score_delta: 0 }] } },
  }));
  assert.match(unchangedScore, /기준값과 다른 입력/);
  assert.doesNotMatch(unchangedScore, /입력값이 이미 학습 중앙값과 같습니다/);
  assert.doesNotMatch(zeroSensitivity, /playground-sensitivity-bars/);
  assert.match(zeroSensitivity, /개별 값 확인 · 1개 항목/);
  assert.doesNotMatch(zeroSensitivity, /role="img"/);
  assert.match(playgroundSensitivity, /치환 후 점수/);
  assert.match(playgroundSensitivity, /0[.]15/);
  assert.match(playgroundHtml, /이상 점수 계산 →/);
  assert.match(playgroundHtml, /항목 찾기/);
  assert.match(playgroundHtml, /표시 항목/);
  const { ModelLabUse, isActivityModelCompatible } =
    await vite.ssrLoadModule("/src/ModelLabUse.tsx");
  assert.equal(isActivityModelCompatible(
    { algorithm: "isolation_forest", features: ["a", "b"] },
    { algorithm: "isolation_forest", required_features: ["a", "b"] }), true);
  assert.equal(isActivityModelCompatible(
    { algorithm: "isolation_forest", features: ["b", "a"] },
    { algorithm: "isolation_forest", required_features: ["a", "b"] }), false);
  assert.equal(isActivityModelCompatible(
    { algorithm: "other", features: ["a", "b"] },
    { algorithm: "isolation_forest", required_features: ["a", "b"] }), false);
  const pendingUse = renderToStaticMarkup(React.createElement(ModelLabUse, {
    datasets: [], hasMoreDatasets: false, onLoadMoreDatasets: async () => {},
    canRun: true, canCancel: true, selectedModelId: "", onSelectModel: () => {},
    onOpenResource: () => {}, onHistory: () => {},
  }));
  assert.match(pendingUse, /모델 적용/);
  assert.match(pendingUse, /사용할 수 있는 모델을 확인하는 중/);
  const { PlatformOverview } = await vite.ssrLoadModule("/src/PlatformOverview.tsx");
  const home = renderToStaticMarkup(React.createElement(PlatformOverview, {
    brandName: "ACME AI Lab",
    datasets: [], onNavigate: () => {}, onOpenDataset: () => {},
  }));
  assert.match(home, /ACME AI Lab · EXPLORE/);
  assert.doesNotMatch(home, /SOS · EXPLORE/);
  assert.match(home, /플랫폼 개요/);
  assert.match(home, /주요 기능/);
  assert.match(home, /모델 제공 상태 확인 중/);
  assert.match(home, /<button type="button">모델 목록 →<\/button>/);
  assert.match(home, /<h4>모델 활용<\/h4>/);
  assert.doesNotMatch(home, /<h4>연구·실험<\/h4>|새 학습|새로운 실험/);
  assert.match(home, /활동 이상 탐지/);
  assert.doesNotMatch(home, /최근 ML 실행/);
  const { ActivityWorkspace } = await vite.ssrLoadModule("/src/ActivityWorkspace.tsx");
  const activity = renderToStaticMarkup(React.createElement(ActivityWorkspace, {
    active: "data", onChange: () => {}, onNavigate: () => {},
    onOpenResource: () => {}, datasets: [], canCancel: false,
  }));
  assert.match(activity, /모델 적용 작업/);
  assert.doesNotMatch(activity, /Workflow 분석|학습/);
  const { ModelApplicationWorkspace } = await vite.ssrLoadModule("/src/ModelApplicationWorkspace.tsx");
  const useModel = renderToStaticMarkup(React.createElement(ModelApplicationWorkspace, {
    model: { ...exampleModel, training_score_summary: {} }, datasets: [], canRun: true,
    canCancel: true, onOpenResource: () => {},
  }));
  assert.match(useModel, /Dataset 분석/);
  assert.match(useModel, /값 직접 입력/);
  const { MLScoringPanel } = await vite.ssrLoadModule("/src/MLScoringPanel.tsx");
  const restoredInput = renderToStaticMarkup(React.createElement(MLScoringPanel, {
    model: exampleModel, datasets: [], selectedDatasetId: "outside-first-page",
    canRun: true, canCancel: true, onOpenResult: () => {}, onOpenResource: () => {},
  }));
  assert.match(restoredInput, /<option value="outside-first-page" selected=""/);
  assert.match(restoredInput, /선택한 Dataset · 목록 외/);
  const sensitivity = renderToStaticMarkup(React.createElement(SensitivityChart, {
    result: { output: { feature_sensitivities: [
      { feature: "f1", anomaly_score_delta: 0.12 },
    ] } },
  }));
  assert.match(sensitivity, /인과적 기여도는 아닙니다/);
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
  assert.match(renderToStaticMarkup(React.createElement(ObservationInvestigation, {
    candidate: { row_index: 5, anomaly_score: 0.8, features: { "io_bytes_per_sec": 42 } },
    scoringResultId: "score-1", canRun: true, canCancel: true, onOpenResource: () => {},
  })), /추가 실험 · 기준값 치환/);
  console.log("Navigation and model use: assertions passed");
} finally {
  await vite.close();
}
