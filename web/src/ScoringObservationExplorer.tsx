/** 독립 Scoring 결과는 점수 분포와 실제 관측값을 한 문맥에서 탐구한다. */
import { useEffect, useMemo, useRef, useState } from "react";
import type { EChartsCoreOption } from "echarts";
import { formatError, request } from "./api";
import { DataChart } from "./DataChart";
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
function distributionOption(summary: Record<string, unknown>, rowCount: number): EChartsCoreOption | null {
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
  const summary = result.score_summary as Record<string, unknown>;
  const histogram = useMemo(() => distributionOption(summary, result.row_count),
    [summary, result.row_count]);

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

  const selected = loaded?.items.find(item => item.row_index === selectedRow) ??
    (selectedRow === null ? loaded?.items[0] : undefined);
  return <section className="scoring-observation-explorer" aria-label="모델 적용 분석 결과">
    <div className="pipeline-section-heading">
      <div>
        <span className="eyebrow">모델 적용 완료</span>
        <h3>활동 데이터 분석 결과</h3>
        <p>전체 점수 분포에서 특이한 관측값을 찾고 실제 입력 특징을 확인합니다.</p>
      </div>
    </div>
    <div className="scoring-result-summary">
      <div><span>분석한 관측값</span><strong>{result.row_count.toLocaleString("ko-KR")}건</strong></div>
      <div><span>평균 이상 점수</span><strong>{readable(summary.score_mean)}</strong></div>
      <div><span>이상 점수 범위</span><strong>{readable(summary.score_min)} ~ {readable(summary.score_max)}</strong></div>
    </div>
    <section className="scoring-distribution" aria-label="전체 이상 점수 분포">
      <h4>전체 이상 점수 분포</h4>
      {histogram
        ? <DataChart option={histogram} ariaLabel="점수화한 전체 관측값의 이상 점수 구간별 관측 수" />
        : <p className="hint">이 결과에는 검증 가능한 전체 점수 분포가 없습니다.
          위의 실제 요약값과 아래 관측값 목록을 확인할 수 있습니다.</p>}
      <p className="hint">높은 점수일수록 모델에서 더 이례적인 입력입니다.
        점수는 확률이나 확정된 이상 사건이 아닙니다. 판정 정책을 적용하지 않은 점수순 결과입니다.</p>
    </section>
    <div className="pipeline-section-heading">
      <div>
        <h4>점수가 높은 관측값</h4>
        <p>점수순으로 실제 관측값을 조회합니다. 왼쪽 항목을 선택하면 상세 내용이 바뀝니다.</p>
      </div>
      {loaded && <span className="hint">전체 {result.row_count.toLocaleString("ko-KR")}건 중
        현재 {loaded.items.length.toLocaleString("ko-KR")}건 조회</span>}
    </div>
    {loading && <p role="status">점수가 높은 관측값을 불러오는 중입니다.</p>}
    {error && <div role="alert" className="pipeline-readiness-blocked">
      <strong>관측값 조회를 완료하지 못했습니다.</strong>
      <details><summary>오류 진단 정보</summary><p>{error}</p></details>
      {!loaded && <button type="button" className="secondary"
        onClick={() => setRetry(n => n + 1)}>관측값 다시 조회</button>}
    </div>}
    {loaded && loaded.items.length === 0 && <p>조회할 관측값이 없습니다.</p>}
    {loaded && loaded.items.length > 0 && <div className="scoring-observation-workspace">
      <div className="scoring-observation-list">
        {loaded.items.map((item, index) =>
          <button key={item.row_index} type="button"
            className="surface-button scoring-observation-choice"
            aria-pressed={selected?.row_index === item.row_index}
            onClick={() => setSelectedRow(item.row_index)}>
            <span className="scoring-observation-rank">{index + 1}</span>
            <span className="scoring-observation-identity">
              <strong>{item.observed_at ?? "결과 행 #" + item.row_index.toLocaleString("ko-KR")}</strong>
              <small>결과 행 #{item.row_index.toLocaleString("ko-KR")}</small>
            </span>
            <span className="scoring-observation-score">
              <small>이상 점수</small>
              <strong>{readable(item.anomaly_score)}</strong>
            </span>
          </button>)}
        {loaded.next_cursor && <button type="button" className="secondary"
          disabled={loadingMore} onClick={() => void more()}>
          {loadingMore ? "다음 관측값 불러오는 중…" : "다음 점수순 관측값 25건"}
        </button>}
      </div>
      <div className="scoring-observation-detail">
        {selected && <PipelineCandidateInvestigation
          key={result.id + ":" + selected.row_index}
          candidate={selected} scoringResultId={result.id}
          modelRevisionId={result.model_revision_id}
          observationMode canRun={canRun} canCancel={canCancel} onOpenResource={onOpenResource} />}
      </div>
    </div>}
  </section>;
}
