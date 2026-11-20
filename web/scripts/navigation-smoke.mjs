import assert from "node:assert/strict";
import fs from "node:fs";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";

// 기존 Vite 변환으로 URL 문맥·결과 식별·입력 검증만 확인한다.
const vite = await createServer({
  configFile: false,
  plugins: [react()],
  optimizeDeps: { noDiscovery: true, include: [] },
  server: { middlewareMode: true },
  appType: "custom",
});

try {
  const { routeAt, routeFromPath, contextPath, nextNavigationContext, resourcePath } =
    await vite.ssrLoadModule("/src/PlatformNavigation.tsx");

  assert.equal(routeAt("/training", "").tab, "overview");
  assert.equal(routeAt("/research", "").tab, "overview");
  // 제거한 SOS Pipeline 화면의 이전 주소는 홈으로 돌아간다.
  assert.equal(routeAt("/pipelines", "").tab, "overview");
  assert.equal(routeAt("/pipelines/runs/run-one", "").tab, "overview");
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
  // 주제 화면 주소는 주제 ID를 하이픈으로 표기한다. 기존 활동 이상 탐지 주소와 주제 미지정 이동은 같은 화면이다.
  assert.deepEqual(routeAt("/topics/activity-anomaly", "").context, { topicId: "activity_anomaly" });
  assert.equal(contextPath("topic", {}), "/topics/activity-anomaly");
  const networkTopic = routeAt("/topics/network-flow-anomaly", "?model=m-1&dataset=d-1");
  assert.equal(networkTopic.tab, "topic");
  assert.deepEqual(networkTopic.context, { topicId: "network_flow_anomaly", modelId: "m-1", datasetId: "d-1" });
  assert.equal(contextPath("topic", networkTopic.context), "/topics/network-flow-anomaly?model=m-1&dataset=d-1");
  assert.equal(nextNavigationContext(networkTopic, "topic").topicId, "network_flow_anomaly");
  assert.equal(nextNavigationContext(networkTopic, "scoring").topicId, undefined);
  // 주제 화면에서 Dataset·모델을 골라도(같은 화면의 문맥 선택) 경로의 주제를 잃지 않는다.
  assert.deepEqual(routeFromPath(contextPath("topic", { ...networkTopic.context, datasetId: "d-2" })).context,
    { topicId: "network_flow_anomaly", modelId: "m-1", datasetId: "d-2" });
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
  assert.equal(contextPath("topic", nextNavigationContext(sourceData, "topic")),
    "/topics/activity-anomaly?dataset=data+one");
  const { checkModelDatasetInput } = await vite.ssrLoadModule("/src/ModelDatasetCompatibility.tsx");
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
  // 불일치는 다음 행동별로 나눈다. 데이터 준비는 실수형(Float64) 변환만 만든다.
  const floats = { features: ["a", "b", "c", "d"], runtime: { feature_dtypes: ["Float64", "Float64", "Float64", "Int64"] } };
  const classified = checkModelDatasetInput(floats, [
    { name: "a", dtype: "Int64" }, { name: "b", dtype: "String" },
    { name: "c", dtype: "Boolean" }, { name: "d", dtype: "Float64" },
  ]);
  assert.deepEqual(classified.castable.map(item => item.name), ["a", "b"]);
  assert.deepEqual(classified.mismatches.map(item => item.name), ["a", "b", "c", "d"]);
  // 이름 후보는 정규화 이름이 같은 컬럼이 하나뿐일 때만 제시하고, 다른 필수 Feature 컬럼은 후보가 아니다.
  const named = { features: ["io_bytes", "Net Rate", "x_y"], runtime: { feature_dtypes: ["Float64", "Float64", "Float64"] } };
  const namedColumns = [
    { name: " IO-Bytes ", dtype: "Int64" }, { name: "net_rate", dtype: "Float64" },
    { name: "NET-RATE", dtype: "Float64" }, { name: "X Y", dtype: "Float64" },
  ];
  assert.deepEqual(checkModelDatasetInput(named, namedColumns).renamed.map(item => [item.actual, item.name]),
    [[" IO-Bytes ", "io_bytes"], ["X Y", "x_y"]]);
  assert.deepEqual(checkModelDatasetInput({ features: ["a_b", "A B"], runtime: { feature_dtypes: ["Float64", "Float64"] } },
    [{ name: "A B", dtype: "Float64" }]).renamed, []);
  // 데이터 준비 초안: 이름 변경을 먼저 두고, 이름·자료형이 모두 다르면 바꾼 이름으로 변환한다. 이미 있는 단계는 다시 넣지 않는다.
  const { pendingInputSteps } = await vite.ssrLoadModule("/src/ModelInputPreparation.tsx");
  assert.deepEqual(pendingInputSteps(named, namedColumns, []), [
    { op: "rename", column: " IO-Bytes ", alias: "io_bytes" },
    { op: "rename", column: "X Y", alias: "x_y" },
    { op: "cast", column: "io_bytes", dtype: "Float64" },
  ]);
  assert.deepEqual(pendingInputSteps(floats, [
    { name: "a", dtype: "Int64" }, { name: "b", dtype: "String" }, { name: "c", dtype: "Float64" },
    { name: "d", dtype: "Int64" },
  ], [{ op: "cast", column: "a", dtype: "Float64" }]), [{ op: "cast", column: "b", dtype: "Float64" }]);
  const { createStep } = await vite.ssrLoadModule("/src/feature-utils.ts");
  assert.deepEqual(createStep("null_non_finite", "Flow Bytes/s", ""), { op: "null_non_finite", column: "Flow Bytes/s" });
  // 주제 화면의 Feature 묶음은 SOS 주제 정의의 입력 Feature를 빠짐·중복 없이 덮는다.
  const { topicFeatureGroups } = await vite.ssrLoadModule("/src/FeatureGroups.ts");
  const topicsSource = fs.readFileSync(new URL("../../src/sos_platform/analysis_topics.py", import.meta.url), "utf8");
  for (const [topicId, constant] of [["activity_anomaly", "ACTIVITY_FEATURES"], ["network_flow_anomaly", "NETWORK_FLOW_FEATURES"]]) {
    const declared = [...topicsSource.match(new RegExp(constant + " = \\(([^)]*)\\)"))[1].matchAll(/"([^"]+)"/g)].map(m => m[1]);
    const grouped = topicFeatureGroups[topicId].flatMap(group => group.features);
    assert.equal(new Set(grouped).size, grouped.length, topicId + " 묶음 중복");
    assert.deepEqual([...grouped].sort(), [...declared].sort(), topicId + " 묶음과 주제 Feature 불일치");
  }
  assert.deepEqual(createStep("rename", " a ", "a"), { op: "rename", column: " a ", alias: "a" });
  assert.throws(() => createStep("rename", "a", "a"));
  assert.throws(() => createStep("rename", "a", "  "));
  // 모델 문맥은 명시적인 데이터 준비 이동에서만 만들고, 데이터 화면·작업 이력을 거쳐 적용 화면으로 돌아간다.
  const preparePath = contextPath("preparation", { datasetId: "source", modelId: "model / a" });
  assert.equal(preparePath, "/data/prepare?dataset=source&model=model+%2F+a");
  const preparing = routeAt("/data/prepare", preparePath.slice(preparePath.indexOf("?")));
  assert.deepEqual(preparing.context, { datasetId: "source", modelId: "model / a" });
  const preparingJobPath = contextPath("jobs", { datasetId: "source", dataJobId: "job-1", dataJobKind: "data",
    modelId: "model / a" });
  const preparingJob = routeAt("/activity/jobs", preparingJobPath.slice(preparingJobPath.indexOf("?")));
  assert.equal(preparingJob.context.modelId, "model / a");
  assert.deepEqual(nextNavigationContext(preparing, "preview"), { datasetId: "source", modelId: "model / a" });
  const transformed = routeAt("/data/query", "?dataset=output&model=model-a");
  assert.deepEqual(nextNavigationContext(transformed, "topic"), { modelId: "model-a", datasetId: "output" });
  assert.deepEqual(nextNavigationContext(transformed, "scoring"), { modelId: "model-a", datasetId: "output" });
  assert.deepEqual(nextNavigationContext(transformed, "ml"), { datasetId: "output" });
  assert.deepEqual(nextNavigationContext(routeAt("/topics/activity-anomaly", "?model=model-a&dataset=d"),
    "preparation"), { datasetId: "d" });
  const jobContext = { datasetId: "one", dataJobId: "job / one", dataJobKind: "exploration" };
  const jobPath = contextPath("jobs", jobContext);
  assert.deepEqual(routeAt("/activity/jobs", jobPath.slice(jobPath.indexOf("?"))).context, jobContext);
  assert.equal(nextNavigationContext(routeAt("/activity/jobs", jobPath.slice(jobPath.indexOf("?"))), "preview").dataJobId, undefined);
  // 다른 Scoring·관측 행·입력 Job의 설명을 현재 결과로 표시하지 않는다.
  const { explanationSourceMatches } = await vite.ssrLoadModule("/src/MLResourcePages.tsx");
  const shapResult = {
    scoring_result_id: "score-v1", model_revision_id: "model-v1", row_index: 7,
    output: { scoring_result_id: "score-v1", model_revision_id: "model-v1", row_index: 7 },
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
  const { escapeShapTooltipText } = await vite.ssrLoadModule("/src/ShapExplanation.tsx");
  assert.equal(escapeShapTooltipText('<img src=x onerror="test()">&'),
    '&lt;img src=x onerror=&quot;test()&quot;&gt;&amp;');
  // 입력을 바꾸거나 관측값을 가져온 뒤 이전 계산 결과를 재사용하지 않는다.
  const { playgroundInputMatchesResult, playgroundJobVisibleAfterImport, playgroundNumberValid } =
    await vite.ssrLoadModule("/src/ModelLabPlayground.tsx");
  const imported = { id: "source-1", modelRevisionId: "model-1", scoringResultId: "sc-1",
    rowIndex: 12, originalScore: 0.7, fields: { a: "2" } };
  assert.equal(playgroundJobVisibleAfterImport(imported, "job-old", "job-old"), false);
  assert.equal(playgroundJobVisibleAfterImport(imported, null, null), false);
  assert.equal(playgroundJobVisibleAfterImport(imported, "job-old", "job-new"), true);
  assert.equal(playgroundJobVisibleAfterImport(null, "job-old", "job-old"), true);
  assert.equal(playgroundInputMatchesResult({ a: "1e3", b: "0.25" },
    { a: 1000, b: 0.25 }, ["a", "b"]), true);
  assert.equal(playgroundInputMatchesResult({ a: "1001", b: "0.25" },
    { a: 1000, b: 0.25 }, ["a", "b"]), false);
  assert.equal(playgroundInputMatchesResult({ a: "", b: "0.25" },
    { a: 1000, b: 0.25 }, ["a", "b"]), false);
  assert.equal(playgroundInputMatchesResult({ a: "1000", b: "0.25" },
    { a: 1000, b: 0.25, c: 1 }, ["a", "b"]), false);
  assert.ok(playgroundNumberValid("12", "Int64"));
  assert.ok(!playgroundNumberValid("12.3", "Int64"));
  assert.ok(!playgroundNumberValid("128", "Int8"));
  assert.ok(!playgroundNumberValid("-1", "UInt8"));
  assert.ok(!playgroundNumberValid("1e39", "Float32"));
  assert.ok(playgroundNumberValid("1.75", "Float32"));
  // 주제 적합성은 입력 Feature 이름과 출력 계약으로 판정한다. 알고리즘·Pipeline은 보지 않는다.
  const { isTopicCompatible } = await vite.ssrLoadModule("/src/ModelLabTaxonomy.ts");
  const topic = { required_features: ["a", "b"], output_type: "anomaly_score_v1" };
  assert.equal(isTopicCompatible({ features: ["a", "b"], output_type: "anomaly_score_v1" }, topic), true);
  assert.equal(isTopicCompatible({ features: ["b", "a"], output_type: "anomaly_score_v1" }, topic), true);
  assert.equal(isTopicCompatible({ features: ["a", "b"], output_type: "class_probability_v1" }, topic), false);
  assert.equal(isTopicCompatible({ features: ["a"], output_type: "anomaly_score_v1" }, topic), false);
  assert.equal(isTopicCompatible({ features: ["a", "c"], output_type: "anomaly_score_v1" }, topic), false);
  console.log("Navigation, result identity and input contracts: assertions passed");
} finally {
  await vite.close();
}
