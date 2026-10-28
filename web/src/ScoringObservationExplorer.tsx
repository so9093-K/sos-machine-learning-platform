/** 독립 Scoring 결과는 점수 분포와 실제 관측값을 한 문맥에서 탐구한다. */
import { useEffect, useMemo, useRef, useState } from "react";
import type { EChartsCoreOption } from "echarts";
import { formatError, request } from "./api";
import { DataChart } from "./DataChart";
import { EvidenceFacts } from "./EvidenceFacts";
import { formatObservedAt } from "./display-format";
import { PipelineCandidateInvestigation } from "./PipelineCandidateInvestigation";
import type { MLResourceKind } from "./PlatformNavigation";
import type { ScoringObservations, ScoringResult } from "./types";

function shortIdentifier(value: string, limit = 12): string {
  return value.length > limit ? value.slice(0, limit) + "…" : value;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function readable(value: unknown): string {
  const number = finiteNumber(value);
  return number === null ? "근거 없음" : number.toLocaleString("ko-KR", { maximumFractionDigits: 6 });
}

/** 결과에 보존된 정확한 히스토그램만 인정한다. 검증되지 않은 분포는 추정하지 않는다. */
function verifiedHistogram(summary: Record<string, unknown>, rowCount: number):
  { edges: number[]; counts: number[] } | null {
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
  return { edges: edges as number[], counts: counts as number[] };
}

/** 점수 범위 상위 1/4 구간에 실제로 있는 관측 수. 히스토그램 구간 경계를 그대로 쓰므로 정확한 집계다. */
function upperScoreShare(summary: Record<string, unknown>, rowCount: number):
  { lower: number; count: number; share: number } | null {
  const histogram = verifiedHistogram(summary, rowCount);
  if (!histogram || rowCount === 0) return null;
  const start = Math.floor(histogram.counts.length * 0.75);
  if (start <= 0 || start >= histogram.counts.length) return null;
  const count = histogram.counts.slice(start).reduce((sum, value) => sum + value, 0);
  return { lower: histogram.edges[start], count, share: count / rowCount };
}

function percent(share: number): string {
  if (share > 0 && share < 0.0001) return "0.01% 미만";
  return (share * 100).toLocaleString("ko-KR", { maximumFractionDigits: 2 }) + "%";
}

/** 실제 결과의 검증된 전체 히스토그램만 표시한다. 추정 분포를 만들지 않는다. */
function distributionOption(summary: Record<string, unknown>, rowCount: number,
  selectedScore: number | null): EChartsCoreOption | null {
  const verified = verifiedHistogram(summary, rowCount);
  if (!verified) return null;
  const { edges, counts } = verified;
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
        // 화면 우측 최댓값에서는 레이블이 겹치므로 선택값은 분포 상단에서 한 번만 표시한다.
        label: { show: false }, data: [{ xAxis: selectedScore }],
      } } : {}),
    }],
  };
}

/** 최초 상위 관측값의 기본 표시와 명시적 사용자 선택을 구별한다. */
export function observationSelectionLabel(userSelected: boolean): string {
  return userSelected ? "현재 선택" : "기본 표시";
}

export function ScoringObservationExplorer({ result, canRun, canCancel, onOpenResource, onTryObservation }: {
  result: ScoringResult;
  canRun: boolean;
  canCancel: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  onTryObservation?: (
    features: Record<string, number | null>, rowIndex: number, score: number,
    scoringResultId: string, modelRevisionId: string,
  ) => string | null;
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
  const userSelected = selectedRow !== null && selected?.row_index === selectedRow;
  const selectedScore = finiteNumber(selected?.anomaly_score);
  const scoreOrder = selected && loaded
    ? loaded.items.findIndex(item => item.row_index === selected.row_index) + 1 : undefined;
  const histogram = useMemo(() => distributionOption(summary, result.row_count, selectedScore),
    [summary, result.row_count, selectedScore]);
  const upper = useMemo(() => upperScoreShare(summary, result.row_count), [summary, result.row_count]);
  // 상세 분석에 넓은 공간이 필요할 때 사용자가 목록을 접는다. 자동으로 숨기지 않는다.
  const [listCollapsed, setListCollapsed] = useState(false);
  const loadedItems = loaded?.items ?? [];
  const selectedIndex = selected ? loadedItems.findIndex(item => item.row_index === selected.row_index) : -1;
  const previousItem = selectedIndex > 0 ? loadedItems[selectedIndex - 1] : null;
  const nextItem = selectedIndex >= 0 ? loadedItems[selectedIndex + 1] ?? null : null;

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
    // 기본으로 보이는 첫 행을 직접 눌러도 명시적 선택으로 상태가 바뀐다.
    setSelectedRow(rowIndex);
    if (selected?.row_index === rowIndex) return;
    // 다른 관측값으로 전환할 때 상세의 시작 부분이 시야 밖이면 결과를 보여준다.
    // 이미 요약이 보이는 데스크톱에서는 불필요한 자동 스크롤을 하지 않는다.
    window.requestAnimationFrame(() => {
      const detail = detailRef.current;
      if (!detail) return;
      const bounds = detail.getBoundingClientRect();
      const stacked = window.matchMedia("(max-width: 1080px)").matches;
      if (!stacked && bounds.top >= 0 && bounds.top < window.innerHeight - 120) return;
      detail.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
        block: "start",
      });
    });
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
    <div className="scoring-result-summary" aria-label="분석 결과 요약">
      <div><span>분석한 관측값</span><strong>{result.row_count.toLocaleString("ko-KR")}건</strong></div>
      <div><span>최고 이상 점수</span><strong>{readable(summary.score_max)}</strong></div>
      <div><span>평균 이상 점수</span><strong>{readable(summary.score_mean)}</strong></div>
    </div>
    <p className="scoring-result-interpretation">{upper
      ? <>점수 <strong>{readable(upper.lower)}</strong> 이상(점수 범위의 상위 1/4 구간)인 관측값은{" "}
          <strong>{upper.count.toLocaleString("ko-KR")}건({percent(upper.share)})</strong>입니다.{" "}
          {upper.share <= 0.05 ? "높은 점수 쪽 관측값은 소수이므로 아래 목록의 상위 관측값부터 살펴보세요."
            : "아래 목록에서 점수가 높은 관측값의 실제 활동 특성을 살펴보세요."}</>
      : "전체 분포에서 점수가 높은 관측값을 선택해 실제 활동 특성을 살펴보세요."}
      <small>판정 정책 미적용 · 점수순 위치는 이상 사건 판정이나 발생 확률을 뜻하지 않습니다.</small></p>
    <section className="scoring-distribution" aria-label="전체 이상 점수 분포">
      <div className="scoring-distribution-heading">
        <h4>전체 관측값의 점수 분포</h4>
        {selected && selectedScore !== null && <span role="status">{userSelected ? "선택한 관측값" : "기본 미리보기"} <strong>{readable(selectedScore)}</strong></span>}
      </div>
      {histogram
        ? <DataChart option={histogram} ariaLabel="점수화한 전체 관측값의 이상 점수 구간별 관측 수" />
        : <p className="hint">이 결과에는 검증 가능한 전체 점수 분포가 없습니다. 실제 요약값과 관측값 목록은 확인할 수 있습니다.</p>}
      <p className="scoring-distribution-stats">평균 점수 <strong>{readable(summary.score_mean)}</strong>
        <span aria-hidden="true"> · </span>점수 범위 <strong>{readable(summary.score_min)} ~ {readable(summary.score_max)}</strong></p>
      <p className="hint">점수가 높을수록 이 모델에서 이례적인 입력입니다.
        {histogram && selectedScore !== null ? " 점선은 현재 표시 중인 관측값의 위치이며 판정 임계값이 아닙니다." : ""}</p>
    </section>
    <div className={"scoring-observation-workspace" + (listCollapsed ? " list-collapsed" : "")}>
      {listCollapsed && selected && <div className="scoring-observation-compact-nav" role="group"
        aria-label="관측값 이동">
        <span>{observationSelectionLabel(userSelected)} · 점수순 {scoreOrder?.toLocaleString("ko-KR")}번째
          {" "}/ 조회 {loadedItems.length.toLocaleString("ko-KR")}건 · 점수 {readable(selected.anomaly_score)}</span>
        <button type="button" className="secondary" disabled={!previousItem}
          onClick={() => previousItem && chooseObservation(previousItem.row_index)}>← 이전 관측값</button>
        <button type="button" className="secondary" disabled={!nextItem}
          onClick={() => nextItem && chooseObservation(nextItem.row_index)}>다음 관측값 →</button>
        <button type="button" className="ghost" onClick={() => setListCollapsed(false)}>목록 펼치기</button>
      </div>}
      <section className="scoring-observation-list-panel" aria-label="점수가 높은 관측값 목록"
        hidden={listCollapsed}>
        <div className="scoring-observation-list-heading">
          <div className="scoring-observation-list-title">
            <h4>점수 상위 관측값</h4>
            {selected && <button type="button" className="ghost" onClick={() => setListCollapsed(true)}>
              목록 접고 상세 넓게 보기</button>}
          </div>
          <p>점수가 높은 관측값 · 전체 {result.row_count.toLocaleString("ko-KR")}건 중
            {loaded ? ` 상위 ${loaded.items.length.toLocaleString("ko-KR")}건 조회` : " 조회 대기"}</p>
          {selected && <p className="scoring-observation-current" role="status">
            {observationSelectionLabel(userSelected)} · 점수순 {scoreOrder?.toLocaleString("ko-KR")}번째 ·
            결과 행 #{selected.row_index.toLocaleString("ko-KR")} · 점수 {readable(selected.anomaly_score)}
          </p>}
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
              aria-current={selected?.row_index === item.row_index ? "true" : undefined}
              aria-pressed={userSelected && selected?.row_index === item.row_index}
              onClick={() => chooseObservation(item.row_index)}>
              <span className="scoring-observation-rank">{index + 1}</span>
              <span className="scoring-observation-identity">
                <strong title={item.observed_at ?? undefined}>{item.observed_at
                  ? formatObservedAt(item.observed_at) : "결과 행 #" + item.row_index.toLocaleString("ko-KR")}</strong>
                {(item.observation_id || item.observed_at) && <small>
                  {item.observation_id ? "관측치 ID " + item.observation_id.slice(0, 8) + "… · " : ""}
                  결과 행 #{item.row_index.toLocaleString("ko-KR")}</small>}
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
        aria-label="현재 표시 중인 관측값 상세">
        {selected
          ? <PipelineCandidateInvestigation
              key={result.id + ":" + selected.row_index}
              candidate={selected} scoreOrder={scoreOrder} scoringResultId={result.id}
              isDefaultObservation={!userSelected}
              modelRevisionId={result.model_revision_id}
              observationMode canRun={canRun} canCancel={canCancel} onOpenResource={onOpenResource}
              onTryObservation={onTryObservation ? (features, rowIndex, score) =>
                onTryObservation(features, rowIndex, score, result.id, result.model_revision_id) : undefined} />
          : <p className="hint">{loading ? "관측값 상세를 준비하고 있습니다."
            : "선택한 관측값이 없습니다. 조회 가능한 관측값을 확인해 주세요."}</p>}
      </section>
    </div>
    <details className="scoring-result-provenance">
      <summary>분석 판본과 식별자</summary>
      <EvidenceFacts compact facts={[
        { label: "Dataset", value: shortIdentifier(result.dataset_id, 8),
          title: result.dataset_id },
        { label: "Dataset 판본", value: shortIdentifier(result.dataset_version),
          title: result.dataset_version },
        { label: "모델 판본", value: shortIdentifier(result.model_revision_id, 8),
          title: result.model_revision_id },
        { label: "결과 생성", value: new Date(result.created_at).toLocaleString("ko-KR") },
      ]} />
      <dl className="scoring-provenance-identifiers">
          <div><dt>Dataset ID</dt><dd>{result.dataset_id}</dd></div>
          <div><dt>Dataset 판본</dt><dd>{result.dataset_version}</dd></div>
          <div><dt>Model Revision</dt><dd>{result.model_revision_id}</dd></div>
          <div><dt>Scoring Result</dt><dd>{result.id}</dd></div>
      </dl>
    </details>
  </section>;
}
