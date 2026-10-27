/** 독립 Scoring 결과는 점수 분포와 실제 관측값을 한 문맥에서 탐구한다. */
import { useEffect, useMemo, useRef, useState } from "react";
import type { EChartsCoreOption } from "echarts";
import { formatError, request } from "./api";
import { DataChart } from "./DataChart";
import { formatObservedAt } from "./display-format";
import { PipelineCandidateInvestigation } from "./PipelineCandidateInvestigation";
import type { MLResourceKind } from "./PlatformNavigation";
import type { ScoringObservations, ScoringResult } from "./types";

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function readable(value: unknown): string {
  const number = finiteNumber(value);
  return number === null ? "근거 없음" : number.toLocaleString("ko-KR", { maximumFractionDigits: 6 });
}

/** 실제 결과의 검증된 전체 히스토그램만 표시한다. 추정 분포를 만들지 않는다. */
function distributionOption(summary: Record<string, unknown>, rowCount: number,
  selectedScore: number | null): EChartsCoreOption | null {
  const histogram = summary.histogram;
  if (!histogram || typeof histogram !== "object") return null;
  const data = histogram as Record<string, unknown>;
  const edges = data.bin_edges;
  const counts = data.bin_counts;
  if (data.method !== "equal_width_exact_v1" || !Array.isArray(edges) ||
      !Array.isArray(counts) || counts.length === 0 || counts.length > 128 ||
      edges.length !== counts.length + 1 || data.score_count !== rowCount ||
      !edges.every((value: unknown, index: number) =>
        typeof value === "number" && Number.isFinite(value) &&
        (index === 0 || value > edges[index - 1])) ||
      !counts.every((value: unknown) => typeof value === "number" && Number.isInteger(value) && value >= 0) ||
      counts.reduce((sum: number, value: number) => sum + value, 0) !== rowCount) return null;
  return {
    aria: { enabled: true },
    tooltip: { trigger: "item" },
    grid: { left: 58, right: 24, top: 26, bottom: 56 },
    xAxis: { type: "value", name: "이상 점수", min: edges[0], max: edges[edges.length - 1],
      axisLabel: { formatter: (value: number) => value.toPrecision(3) } },
    yAxis: { type: "value", name: "관측 수", minInterval: 1 },
    series: [{
      name: "점수 구간별 관측 수", type: "bar",
      data: counts.map((count: number, index: number) => [(edges[index] + edges[index + 1]) / 2, count]),
      ...(selectedScore !== null ? { markLine: {
        silent: true, symbol: "none", lineStyle: { type: "dashed" },
        label: { formatter: "선택 관측값" }, data: [{ xAxis: selectedScore }],
      } } : {}),
    }],
  };
}

export function ScoringObservationExplorer({ result, canRun, canCancel, onOpenResource }: {
  result: ScoringResult;
  canRun: boolean;
  canCancel: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [loaded, setLoaded] = useState<ScoringObservations | null>(null);
  const [selectedRow, setSelectedRow] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const pendingMore = useRef(false);
  const detailRef = useRef<HTMLElement>(null);
  const summary = result.score_summary as Record<string, unknown>;
  const selected = loaded?.items.find(item => item.row_index === selectedRow) ??
    (selectedRow === null ? loaded?.items[0] : undefined);
  const selectedScore = finiteNumber(selected?.anomaly_score);
  const scoreOrder = selected && loaded
    ? loaded.items.findIndex(item => item.row_index === selected.row_index) + 1 : undefined;
  const histogram = useMemo(() => distributionOption(summary, result.row_count, selectedScore),
    [summary, result.row_count, selectedScore]);

  useEffect(() => {
    let active = true;
    setLoaded(null); setSelectedRow(null); setLoading(true); setError("");
    void request<ScoringObservations>(
      "/api/ml/scoring/results/" + encodeURIComponent(result.id) + "/observations?limit=25",
    ).then(page => {
      if (!active) return;
      if (page.result_id !== result.id) throw new Error("조회 결과 판본이 일치하지 않습니다.");
      setLoaded(page);
    }).catch(cause => { if (active) setError(formatError(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [result.id, retry]);

  function chooseObservation(rowIndex: number) {
    setSelectedRow(rowIndex);
    // 나란히 보이는 넓은 화면은 위치를 유지한다. 세로 배치일 때만
    // 사용자가 선택한 상세로 이동해 갱신 결과를 확인할 수 있게 한다.
    if (window.matchMedia("(max-width: 1080px)").matches) {
      window.requestAnimationFrame(() => {
        detailRef.current?.scrollIntoView({
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
          block: "start",
        });
      });
    }
  }

  async function more() {
    if (!loaded?.next_cursor || pendingMore.current) return;
    pendingMore.current = true; setLoadingMore(true); setError("");
    try {
      const next = await request<ScoringObservations>(
        "/api/ml/scoring/results/" + encodeURIComponent(result.id) +
        "/observations?limit=25&cursor=" + encodeURIComponent(loaded.next_cursor),
      );
      if (next.result_id !== result.id) throw new Error("조회 결과 판본이 변경됐습니다.");
      setLoaded(previous => previous?.result_id === result.id ? {
        ...next, items: [...previous.items,
          ...next.items.filter(item => !previous.items.some(old => old.row_index === item.row_index))],
      } : previous);
    } catch (cause) { setError(formatError(cause)); }
    finally { pendingMore.current = false; setLoadingMore(false); }
  }

  return <section className="scoring-observation-explorer" aria-label="모델 적용 분석 결과">
    <header className="scoring-workspace-heading">
      <div>
        <span className="eyebrow">모델 적용 완료</span>
        <h3>활동 데이터 분석 결과</h3>
      </div>
      <p>실제 데이터의 전체 점수 분포와 관측값별 활동 특징을 함께 살펴봅니다.</p>
    </header>
    <div className="scoring-result-provenance">
      <span>Dataset <strong title={result.dataset_id}>{result.dataset_id.slice(0, 8)} · {result.dataset_version}</strong></span>
      <span>모델 <strong title={result.model_revision_id}>{result.model_revision_id.slice(0, 8)}</strong></span>
      <span>결과 생성 <strong>{new Date(result.created_at).toLocaleString("ko-KR")}</strong></span>
      <details>
        <summary>분석 판본과 식별자</summary>
        <p>Dataset ID: {result.dataset_id} · Model Revision: {result.model_revision_id} · Scoring Result: {result.id}</p>
      </details>
    </div>
    <div className="scoring-result-summary" aria-label="분석 결과 요약">
      <div><span>분석한 관측값</span><strong>{result.row_count.toLocaleString("ko-KR")}건</strong></div>
      <div><span>선택 관측값 점수</span><strong aria-live="polite">{selectedScore !== null
        ? readable(selectedScore) : loading ? "조회 중" : "선택된 관측값 없음"}</strong></div>
      <div><span>이상 판정 정책</span><strong>미적용</strong></div>
    </div>
    <section className="scoring-distribution" aria-label="전체 이상 점수 분포">
      <div className="scoring-distribution-heading">
        <h4>전체 관측값의 점수 분포</h4>
        {selected && selectedScore !== null && <span role="status">선택 관측값 <strong>{readable(selectedScore)}</strong></span>}
      </div>
      {histogram
        ? <DataChart option={histogram} ariaLabel="점수화한 전체 관측값의 이상 점수 구간별 관측 수" />
        : <p className="hint">이 결과에는 검증 가능한 전체 점수 분포가 없습니다. 실제 요약값과 관측값 목록은 확인할 수 있습니다.</p>}
      <p className="scoring-distribution-stats">평균 점수 <strong>{readable(summary.score_mean)}</strong>
        <span aria-hidden="true"> · </span>점수 범위 <strong>{readable(summary.score_min)} ~ {readable(summary.score_max)}</strong></p>
      <p className="hint">점수가 높을수록 이 모델에서 이례적인 입력입니다.
        {histogram && selectedScore !== null ? " 점선은 선택한 관측값의 위치입니다." : ""}
        점수는 확률이나 사건 판정이 아니며, 이 결과에는 판정 정책을 적용하지 않았습니다.</p>
    </section>
    <div className="scoring-observation-workspace">
      <section className="scoring-observation-list-panel" aria-label="점수가 높은 관측값 목록">
        <div className="scoring-observation-list-heading">
          <h4>관측값 살펴보기</h4>
          <p>점수가 높은 순 · 전체 {result.row_count.toLocaleString("ko-KR")}건 중
            {loaded ? ` ${loaded.items.length.toLocaleString("ko-KR")}건 조회` : " 조회 대기"}</p>
        </div>
        {loading && <p role="status">점수가 높은 관측값을 불러오는 중입니다.</p>}
        {error && <div role="alert" className="pipeline-readiness-blocked">
          <strong>관측값 조회를 완료하지 못했습니다.</strong>
          <details><summary>오류 진단 정보</summary><p>{error}</p></details>
          {!loaded && <button type="button" className="secondary"
            onClick={() => setRetry(n => n + 1)}>관측값 다시 조회</button>}
        </div>}
        {loaded && loaded.items.length === 0 && <p>조회할 관측값이 없습니다.</p>}
        <div className="scoring-observation-list">
          {loaded?.items.map((item, index) =>
            <button key={item.row_index} type="button"
              className="surface-button scoring-observation-choice"
              aria-pressed={selected?.row_index === item.row_index}
              onClick={() => chooseObservation(item.row_index)}>
              <span className="scoring-observation-rank">{index + 1}</span>
              <span className="scoring-observation-identity">
                <strong title={item.observed_at ?? undefined}>{item.observed_at
                  ? formatObservedAt(item.observed_at) : "결과 행 #" + item.row_index.toLocaleString("ko-KR")}</strong>
                <small>{item.observation_id ? "관측치 ID " + item.observation_id.slice(0, 8) + "… · " : ""}
                  결과 행 #{item.row_index.toLocaleString("ko-KR")}</small>
              </span>
              <span className="scoring-observation-score">
                <small>점수</small>
                <strong>{readable(item.anomaly_score)}</strong>
              </span>
            </button>)}
          {loaded?.next_cursor && <button type="button" className="secondary"
            disabled={loadingMore} onClick={() => void more()}>
            {loadingMore ? "다음 관측값 불러오는 중…" : "다음 관측값 25건 불러오기"}
          </button>}
        </div>
      </section>
      <section className="scoring-observation-detail" ref={detailRef} tabIndex={-1}
        aria-label="선택한 관측값 상세">
        {selected
          ? <PipelineCandidateInvestigation
              key={result.id + ":" + selected.row_index}
              candidate={selected} scoreOrder={scoreOrder} scoringResultId={result.id}
              modelRevisionId={result.model_revision_id}
              observationMode canRun={canRun} canCancel={canCancel} onOpenResource={onOpenResource} />
          : <p className="hint">{loading ? "관측값 상세를 준비하고 있습니다."
            : "선택한 관측값이 없습니다. 조회 가능한 관측값을 확인해 주세요."}</p>}
      </section>
    </div>
  </section>;
}
