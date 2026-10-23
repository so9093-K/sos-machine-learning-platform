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
    ModelAreaNavigation } = await vite.ssrLoadModule("/src/PlatformNavigation.tsx");

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
    canTrain: false, canCancel: false, view: "training",
    onViewChange: () => {}, onOpenModel: () => {},
  }));
  assert.match(trainingForm, /<option value="data-v1" selected=""/);

  const pagedDatasetForm = renderToStaticMarkup(React.createElement(MLWorkbench, {
    datasets: [], initialDatasetId: "data-not-on-first-page",
    onDatasetSelected: () => {}, canTrain: false, canCancel: false,
    view: "training", onViewChange: () => {}, onOpenModel: () => {},
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
  assert.deepEqual(nextNavigationContext(
    routeAt("/data", "?from=pipeline"), "ml"), {});
  assert.equal(contextPath("pipelines", { datasetId: "dataset-from-data" }),
    "/pipelines?dataset=dataset-from-data");

  // 서버 계산 없이 첫 Pipeline 소비 화면의 입구·준비 안내를 렌더링한다.
  const { PipelineWorkspace } = await vite.ssrLoadModule("/src/PipelineWorkspace.tsx");
  const props = {
    datasets: [], initialDatasetId: "", initialRunId: "",
    hasMoreDatasets: false, onLoadMoreDatasets: async () => {},
    onSelectDataset: () => {}, onSelectRun: () => {},
    canRun: true, canCancel: true, onNavigate: () => {}, onOpenResource: () => {},
  };
  const emptyJourney = renderToStaticMarkup(React.createElement(PipelineWorkspace, props));
  assert.match(emptyJourney, /내 데이터로 시작하기/);
  assert.match(emptyJourney, /자동 생성하는 단계는 현재 등록되어 있지 않습니다/);
  assert.match(emptyJourney, /아직 선택된 실행이 없습니다/);
  assert.doesNotMatch(emptyJourney, /이 데이터로 분석 실행하기/);
  const selectedJourney = renderToStaticMarkup(React.createElement(PipelineWorkspace, {
    ...props, datasets: [{ id: "dataset-1", name: "활동 데이터", version: "revision-1", kind: "file" }],
    initialDatasetId: "dataset-1",
  }));
  assert.match(selectedJourney, /<option value="dataset-1" selected=""/);
  assert.match(selectedJourney, /입력 조건 확인/);

  console.log("ML / Pipeline navigation and Consumer entry: assertions passed");
} finally {
  await vite.close();
}
