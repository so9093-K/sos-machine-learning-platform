import assert from "node:assert/strict";
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
  const { routeAt, contextPath, nextNavigationContext, resourcePath } =
    await vite.ssrLoadModule("/src/PlatformNavigation.tsx");

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
  assert.equal(checkModelDatasetInput({ ...contract, runtime: {} }, []).ready, false);
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
