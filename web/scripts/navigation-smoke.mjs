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

  console.log("ML navigation context: 20 cases passed");
} finally {
  await vite.close();
}
