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

  const groupTraining = routeAt("/training", "?dataset=data-v1&group=research-team");
  assert.equal(contextPath("ml", nextNavigationContext(groupTraining, "ml")),
    "/models?dataset=data-v1&group=research-team");
  assert.equal(contextPath("scoring", nextNavigationContext(
    routeAt("/models", "?group=research-team"), "scoring")), "/scoring?group=research-team");
  const groupModel = resourcePath("model", "model-v1", { modelGroupKey: "research-team" });
  assert.equal(groupModel, "/models/model-v1?group=research-team");
  assert.equal(contextPath("scoring", nextNavigationContext(
    routeAt("/models/model-v1", "?group=research-team"), "scoring")),
    "/scoring?model=model-v1&group=research-team");
  assert.equal(routeAt("/training", "?group=invalid!").context.modelGroupKey, undefined);
  assert.equal(routeAt("/scoring", "?model=model-v1").context.modelId, "model-v1");
  assert.equal(contextPath("pipelines", { pipelineView: "learn" }), "/pipelines?view=learn");
  assert.equal(contextPath("pipelines", { pipelineView: "use" }), "/pipelines?view=use");
  assert.equal(routeAt("/pipelines", "?view=learn").context.pipelineView, "learn");
  assert.equal(routeAt("/pipelines", "?view=use").context.pipelineView, "use");
  assert.equal(contextPath("pipelines", { pipelineView: "use", modelId: "model one" }),
    "/pipelines?view=use&model=model+one");
  assert.equal(routeAt("/pipelines", "?view=use&model=model-one").context.modelId, "model-one");
  // 사용자 정의 모델 그룹의 직접 URL과 결과에서 복귀할 때 모델 선택이 유지된다.
  const groupedUse = contextPath("pipelines", {
    pipelineView: "use", modelId: "model-a", modelGroupKey: "anomaly-team",
  });
  assert.equal(groupedUse, "/pipelines?view=use&model=model-a&group=anomaly-team");
  assert.equal(routeAt("/pipelines", "?view=use&model=model-a&group=anomaly-team")
    .context.modelGroupKey, "anomaly-team");
  assert.equal(routeAt("/pipelines", "?view=use&model=model-a&group=invalid!")
    .context.modelGroupKey, undefined);
  const groupedResource = resourcePath("scoring", "result-a", {
    modelLabReturn: true, modelId: "model-a", modelGroupKey: "anomaly-team",
  });
  assert.equal(groupedResource,
    "/scoring/results/result-a?from=model-lab&model=model-a&group=anomaly-team");
  assert.equal(routeAt("/scoring/results/result-a",
    "?from=model-lab&model=model-a&group=anomaly-team").context.modelGroupKey, "anomaly-team");
  const groupedDecision = contextPath("policies", {
    scoringId: "score-a", modelLabReturn: true, modelId: "model-a",
    modelGroupKey: "anomaly-team",
  });
  assert.equal(groupedDecision,
    "/policies?scoring=score-a&from=model-lab&model=model-a&group=anomaly-team");
  assert.equal(routeAt("/policies", "?scoring=score-a&from=model-lab&model=model-a&group=anomaly-team")
    .context.modelGroupKey, "anomaly-team");

  assert.equal(routeAt("/pipelines", "?view=learn&model=model-one").context.modelId, undefined);
  assert.equal(routeAt("/pipelines", "?view=unexpected").context.pipelineView, undefined);
  assert.equal(routeAt("/pipelines", "?run=run-1&view=use").context.pipelineView, undefined);
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
    "/pipelines?view=learn");
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
    datasets: [{ id: "data-v1", name: "Example Dataset", kind: "file", location: "s3://example/source.parquet" }],
    initialDatasetId: "data-v1", onDatasetSelected: () => {},
    registryKey: "default", onRegistryGroupSelected: () => {},
    canTrain: false, canCancel: false, onOpenModel: () => {},
  }));
  assert.match(trainingForm, /<option value="data-v1" selected=""/);
  assert.match(trainingForm, /학습 결측값 처리 정책/);
  assert.match(trainingForm, /결측 허용 안 함 \(기본값\)/);
  assert.match(trainingForm, /Train 중앙값으로 대체/);
  assert.match(trainingForm, /Feature와 입력 정책을 직접 선택하는 독립 실험/);
  // 필수 Feature 선택은 초기 숨김 없이 접근할 수 있고, 한 번에 전체 선택을 강요하지 않는다.
  const { FeatureSelector } = await vite.ssrLoadModule("/src/FeatureSelector.tsx");
  const emptyFeatureSelection = renderToStaticMarkup(React.createElement(FeatureSelector, {
    columns: [{ name: "metric_a", dtype: "Float64" }, { name: "metric_b", dtype: "Int64" }],
    selected: [], disabled: false, onChange: () => {},
  }));
  assert.match(emptyFeatureSelection, /학습 Feature 검색/);
  assert.match(emptyFeatureSelection, /0(?:<!-- -->)?개 선택/);
  assert.match(emptyFeatureSelection, /2(?:<!-- -->)?개 표시/);
  assert.match(emptyFeatureSelection, /표시된 항목 선택/);
  assert.match(emptyFeatureSelection, /metric_a/);
  assert.match(emptyFeatureSelection, /metric_b/);

  const pagedDatasetForm = renderToStaticMarkup(React.createElement(MLWorkbench, {
    datasets: [], initialDatasetId: "data-not-on-first-page",
    registryKey: "default", onRegistryGroupSelected: () => {},
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
  assert.equal(routeAt("/data", "?dataset=chosen").context.datasetId, "chosen");
  for (const [tab, path] of [["understanding", "/data"], ["preview", "/data/query"],
      ["profile", "/data/profile"], ["preparation", "/data/prepare"]]) {
    const target = contextPath(tab, { datasetId: "data / one" });
    assert.equal(routeAt(path, target.slice(target.indexOf("?"))).context.datasetId, "data / one");
  }
  assert.deepEqual(nextNavigationContext(routeAt("/data", "?dataset=one"), "preview"),
    { pipelineReturn: false, datasetId: "one" });
  const jobContext = { datasetId: "one", dataJobId: "job / one", dataJobKind: "exploration" };
  const jobPath = contextPath("jobs", jobContext);
  assert.deepEqual(routeAt("/activity/jobs", jobPath.slice(jobPath.indexOf("?"))).context,
    { pipelineReturn: false, ...jobContext });
  assert.equal(nextNavigationContext(routeAt("/activity/jobs", jobPath.slice(jobPath.indexOf("?"))), "preview").dataJobId, undefined);
  const { dataJobLabel } = await vite.ssrLoadModule("/src/JobsPanel.tsx");
  assert.equal(dataJobLabel({ kind: "feature_register", spec: {} }), "Feature Service 등록");
  assert.equal(dataJobLabel({ kind: "feature_retrieve", spec: {} }), "Feature 시점 조회");
  assert.equal(dataJobLabel({ kind: "feature_retire", spec: {} }), "Feature Service 폐기");
  const { isFeastSource, entityKeyIssue, RegisteredFeatureSummary, featureRetrievalBlockReason } = await vite.ssrLoadModule("/src/FeastFeaturePanel.tsx");
  const retirementFor = status => ({ status, spec: { registration_job_id: "registration-1" } });
  for (const status of ["Queued", "Running", "Succeeded", "Failed"])
    assert.ok(featureRetrievalBlockReason("registration-1", false, retirementFor(status)));
  assert.match(featureRetrievalBlockReason("registration-1", false, retirementFor("Failed")), /폐기 상태를 확인하거나 폐기를 다시 요청/);
  assert.match(featureRetrievalBlockReason("registration-1", true, null), /폐기돼 신규 조회/);
  assert.equal(featureRetrievalBlockReason("registration-1", false, retirementFor("Cancelled")), null);
  assert.equal(featureRetrievalBlockReason("different-registration", false, retirementFor("Failed")), null);
  assert.equal(featureRetrievalBlockReason("registration-1", false, null), null);
  assert.equal(featureRetrievalBlockReason("", false, retirementFor("Failed")), null);
  assert.equal(isFeastSource({ kind: "file", name: "활동 Feature", location: "s3://bucket/source.parquet" }), true);
  assert.equal(isFeastSource({ kind: "file", name: "source.parquet", location: "s3://bucket/source.csv" }), false);
  assert.equal(isFeastSource({ kind: "iceberg", name: "source.parquet", location: "table.parquet" }), false);
  assert.match(entityKeyIssue("host_id", [{ name: "host_id", dtype: "Int32" }], [{ name: "host_id", dtype: "Int64" }]), /자료형 불일치/);
  assert.match(entityKeyIssue("host_id", [{ name: "host_id", dtype: "String" }], []), /없습니다/);
  assert.equal(entityKeyIssue("host_id", [{ name: "host_id", dtype: "String" }], [{ name: "host_id", dtype: "String" }]), "");
  const featureSummary = renderToStaticMarkup(React.createElement(RegisteredFeatureSummary, {
    registration: { id: "registration-1", spec: {}, result: { entity_column: "host_id", timestamp_column: "event_time", feature_names: ["activity"], ttl_seconds: 2592000 } },
    sourceName: "활동 Feature",
  }));
  assert.match(featureSummary, /host_id/);
  assert.match(featureSummary, /30일/);
  assert.match(featureSummary, /조회 실행 시 검증/);
  const { differingRunKeys, RunComparison } = await vite.ssrLoadModule("/src/RunComparison.tsx");
  const comparisonRuns = [
    { id: "first", params: { "sos.dataset_id": "one", "sos.n_estimators": "100" }, metrics: { "sos.score_mean": 0, same: 1 } },
    { id: "second", params: { "sos.dataset_id": "one", "sos.n_estimators": "200" }, metrics: { same: 1 } },
  ];
  assert.deepEqual(differingRunKeys(comparisonRuns, "params"), ["sos.n_estimators"]);
  assert.deepEqual(differingRunKeys(comparisonRuns, "metrics"), ["sos.score_mean"]);
  const comparisonView = renderToStaticMarkup(React.createElement(RunComparison, { runs: comparisonRuns, datasets: [] }));
  assert.match(comparisonView, /데이터 판본 기록 부족/);
  assert.match(comparisonView, /트리 개수/);
  assert.match(comparisonView, /평균 이상 점수/);
  const { pipelineFailureEvidence, PipelineFailureNotice } = await vite.ssrLoadModule("/src/PipelineWorkspace.tsx");
  const failedRun = { id: "failed-run", status: "Failed", error_code: "SOS-JOB-FAILED", stages: [{ name: "training", job: {
    id: "training-1", status: "Failed", error_code: "SOS-ML-INPUT-INVALID", error: "입력값 확인", retry_policy: "never",
  } }] };
  assert.equal(pipelineFailureEvidence(failedRun).stage, "모델 학습");
  assert.equal(pipelineFailureEvidence(failedRun).message, "입력값 확인");
  assert.equal(pipelineFailureEvidence({ ...failedRun, stages: [] }).stage, null);
  const failureView = renderToStaticMarkup(React.createElement(PipelineFailureNotice, {
    run: failedRun, operatorLinks: false, onInspectInput() {}, onRefresh() {},
  }));
  assert.match(failureView, /실패 지점 · 모델 학습/);
  assert.match(failureView, /입력 Dataset 확인/);
  assert.doesNotMatch(failureView, /href=/);
  for (const errorCode of ["SOS-MLFLOW-NOT-CONFIGURED", "SOS-JOB-FAILED"]) {
    const infrastructureFailure = renderToStaticMarkup(React.createElement(PipelineFailureNotice, {
      run: { ...failedRun, error_code: errorCode, stages: [] }, operatorLinks: false,
      onInspectInput() {}, onRefresh() {},
    }));
    assert.doesNotMatch(infrastructureFailure, /입력 Dataset 확인/);
    assert.match(infrastructureFailure, /실행 상태 다시 조회/);
  }
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
  assert.match(retryTracking, /MLflow 기록·모델 등록 실패 · 자동 재시도 대기 \(시도 2회\)/);
  assert.match(retryTracking, /SOS-MLFLOW-HTTP-503/);
  assert.match(retryTracking, /기존 Job 결과는 보존됩니다/);
  const missingModelTracking = renderToStaticMarkup(React.createElement(MLTrackingDetails, {
    status: { state: "Retry", attempts: 6,
      retry_after: "2026-10-26T12:30:00+00:00",
      run_id: "tracked-run-1",
      last_error_code: "SOS-MLFLOW-HTTP-404-registered-models_get" },
    error: "",
  }));
  assert.match(missingModelTracking, /신규 모델 생성 순서 또는 Registry 상태/);
  assert.match(missingModelTracking, /다음 자동 재시도 기준 시각/);
  assert.match(missingModelTracking, /tracked-run-1/);
  assert.match(missingModelTracking, /동일 학습을 다시 제출해 해결하려 하지 마세요/);
  const recoveredTracking = renderToStaticMarkup(React.createElement(MLTrackingDetails, {
    status: { state: "Succeeded", run_id: "mlflow-run-1" }, error: "",
  }));
  assert.match(recoveredTracking, /MLflow 기록 완료 · Run ID mlflow-run-1/);
  assert.doesNotMatch(recoveredTracking, /role="alert"|SOS-MLFLOW-HTTP-503/);

  const { PipelineAnalysis, PipelineEvidenceView, validatePipelineEvidence } = await vite.ssrLoadModule("/src/PipelineAnalysis.tsx");
  const { PipelineCandidateInvestigation } = await vite.ssrLoadModule("/src/PipelineCandidateInvestigation.tsx");
  const { SensitivityChart, formatSensitivityValue } = await vite.ssrLoadModule("/src/FeatureSensitivity.tsx");
  const { matchingExplanationJob } = await vite.ssrLoadModule("/src/ObservationSensitivity.tsx");
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
  assert.match(candidateWithContext, /학습 중앙값/);
  assert.match(candidateWithContext, /입력값 확인 · 1개 항목/);
  assert.match(candidateWithContext, /점수 기여도/);
  const groupedObservation = renderToStaticMarkup(
    React.createElement(PipelineCandidateInvestigation, {
      candidate: {
        row_index: 9, anomaly_score: 0.71,
        features: { cnt_file_create: 3, cnt_proc_exec: 4, ratio_outbound_traffic: 0.6 },
      }, scoreOrder: 2, observationMode: true,
      scoringResultId: "score-1", modelRevisionId: null,
      canRun: false, canCancel: false, onOpenResource: () => {},
    }));
  assert.match(groupedObservation, /선택한 관측값/);
  const defaultObservation = renderToStaticMarkup(
    React.createElement(PipelineCandidateInvestigation, {
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
    React.createElement(PipelineCandidateInvestigation, {
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
    React.createElement(PipelineCandidateInvestigation, {
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
  assert.doesNotMatch(groupedObservation, /pipeline-feature-details/);
  assert.match(groupedObservation, /학습 모델 판본 참조가 없어/);
  const pipelineViewOnly = renderToStaticMarkup(
    React.createElement(PipelineCandidateInvestigation, {
      candidate: { row_index: 2, anomaly_score: 0.2, features: { cnt_proc_exec: 2 } },
      scoringResultId: "score-1", modelRevisionId: null,
      canRun: false, canCancel: false, onOpenResource: () => {},
    }));
  assert.match(pipelineViewOnly, /observation-activity-picker/);
  assert.ok(pipelineViewOnly.indexOf("입력값 확인 · 1개 항목") <
    pipelineViewOnly.indexOf("점수 기여도"));
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
  const { escapeShapTooltipText } = await vite.ssrLoadModule("/src/PipelineShapExplanation.tsx");
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
  assert.doesNotMatch(emptyJourney, /이 데이터로 분석 실행/);
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
  const readyExample = renderToStaticMarkup(React.createElement(ModelLabIntroduction, {
    definition: { required_features: ["f1"], required_time_column: "ts" },
    example: { title: "예제", row_count: 240 },
    examplesLoading: false, exampleBusy: false, examplePhase: null,
    canCreateDataset: true, canRun: true, catalogError: "",
    onRetryCatalog: () => {}, onStartExample: () => {}, onOwnData: () => {},
  }));
  assert.match(readyExample, /id="pipeline-example-start"[^>]*>예제로 모델 학습 시작/);
  assert.doesNotMatch(readyExample, /id="pipeline-example-start"[^>]*disabled=""/);
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
  const applicationContext = { modelId: "model-a", pipelineReturn: true, pipelineRunId: "run-a",
    datasetId: "dataset-a", candidateRowIndex: 8, pipelineAnalysisSection: "calibration" };
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
    onOpenResource: () => {}, onBack: () => {}, onLearn: () => {}, canLearn: true,
  }));
  assert.match(pendingUse, /모델 적용/);
  assert.match(pendingUse, /사용 가능한 모델을 확인하는 중/);
  const { ModelLabHome } = await vite.ssrLoadModule("/src/ModelLabHome.tsx");
  const labHome = renderToStaticMarkup(React.createElement(ModelLabHome, {
    onUse: () => {}, onLearn: () => {},
  }));
  assert.match(labHome, /<h3>모델 적용<\/h3>/);
  assert.match(labHome, /<h3>모델 학습<\/h3>/);
  assert.match(labHome, /시간대별 활동 패턴/);
  assert.doesNotMatch(labHome, /이해를 돕는 개념도|실제 결과가 아닙니다/);
  const { PlatformOverview } = await vite.ssrLoadModule("/src/PlatformOverview.tsx");
  const home = renderToStaticMarkup(React.createElement(PlatformOverview, {
    brandName: "ACME AI Lab",
    datasets: [], onNavigate: () => {}, onOpenDataset: () => {},
    onOpenPipelineRun: () => {},
  }));
  assert.match(home, /ACME AI Lab · EXPLORE/);
  assert.doesNotMatch(home, /SOS · EXPLORE/);
  assert.match(home, /작업 시작/);
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
  const { ModelApplicationWorkspace } = await vite.ssrLoadModule("/src/ModelApplicationWorkspace.tsx");
  const useModel = renderToStaticMarkup(React.createElement(ModelApplicationWorkspace, {
    model: { ...exampleModel, training_score_summary: {} }, datasets: [], canRun: true,
    canCancel: true, onOpenResource: () => {},
  }));
  assert.match(useModel, /Dataset 분석/);
  assert.match(useModel, /값 직접 입력/);
  const deepLinkedJourney = renderToStaticMarkup(React.createElement(PipelineWorkspace, {
    ...props, initialRunId: "previous-run",
  }));
  assert.match(deepLinkedJourney, /선택한 실행을 불러오는 중입니다/);
  assert.match(deepLinkedJourney, /aria-labelledby="pipeline-run-heading"/);
  assert.doesNotMatch(deepLinkedJourney, /pipeline-run-context-header/);
  assert.doesNotMatch(deepLinkedJourney, /비지도 학습/);
  assert.doesNotMatch(deepLinkedJourney, /예제로 모델 학습 시작/);
  assert.doesNotMatch(deepLinkedJourney, /내 데이터로 학습하거나 분석 방법 자세히 보기/);
  assert.match(deepLinkedJourney, /새 모델 학습 →/);
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
  assert.match(report, /학습 및 검토 결과/);
  assert.match(report, /Test 구간의 검토 대상/);
  assert.match(report, /3건/);
  assert.match(report, /15%/);
  assert.match(report, /같은 경계/);
  assert.match(report, /정답 레이블이 없어/);
  assert.match(report, /Test 점수 구간별 전체 관측 수/);
  assert.doesNotMatch(report, /pipeline-analysis-nav|Training 점수 구간별 전체 관측 수|Calibration 점수 구간별 전체 관측 수/);
  const trainingReport = reportFor("training");
  assert.match(trainingReport, /학습 데이터·설정/);
  assert.match(trainingReport, /Training 점수 구간별 전체 관측 수/);
  const interactiveOverview = renderToStaticMarkup(React.createElement(PipelineEvidenceView, {
    run: completedRun, evidence: resultEvidence, onOpenResource: () => {},
    onSelectTestScoreRange: () => {}, onInvestigateCandidates: () => {},
  }));
  assert.match(interactiveOverview, /검토 대상 탐색 →/);
  assert.match(interactiveOverview, /aria-label="Test 점수 구간"/);
  assert.match(interactiveOverview, /value="" disabled="" selected="">구간 선택/);
  const calibrationReport = reportFor("calibration");
  assert.match(calibrationReport, /검토 기준 산정/);
  assert.match(calibrationReport, /0.5%/);
  assert.match(calibrationReport, /Calibration 점수 구간별 전체 관측 수/);
  assert.match(reportFor("test"), /실제 최소·평균·최대 이상 점수/);
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
  assert.match(renderToStaticMarkup(React.createElement(PipelineCandidateInvestigation, {
    candidate: { row_index: 5, anomaly_score: 0.8, features: { "io_bytes_per_sec": 42 } },
    scoringResultId: "score-1", canRun: true, canCancel: true, onOpenResource: () => {},
  })), /추가 실험 · 기준값 치환/);
  assert.match(renderToStaticMarkup(React.createElement(PipelineAnalysis, {
    run: { id: "run-1", stages: [], status: "Succeeded" }, onOpenResource: () => {},
  })), /분석 판본 참조가 불완전합니다/);

  console.log("ML / Pipeline navigation and Consumer entry: assertions passed");
} finally {
  await vite.close();
}
