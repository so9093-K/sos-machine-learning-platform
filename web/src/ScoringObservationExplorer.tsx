/** 범용 Scoring Result의 실제 관측치를 명시적으로 탐색한다. Pipeline 판정 후보와 구별한다. */
import { useEffect, useRef, useState } from "react";
import { formatError, request } from "./api";
import { PipelineCandidateInvestigation } from "./PipelineCandidateInvestigation";
import type { MLResourceKind } from "./PlatformNavigation";
import type { ScoringObservations, ScoringResult } from "./types";

export function ScoringObservationExplorer({ result, canRun, canCancel, onOpenResource }: {
  result: ScoringResult;
  canRun: boolean;
  canCancel: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [visited, setVisited] = useState(false);
  const [loaded, setLoaded] = useState<ScoringObservations | null>(null);
  const [selectedRow, setSelectedRow] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const pendingMore = useRef(false);

  useEffect(() => {
    if (!visited) return;
    let active = true;
    setLoaded(null); setSelectedRow(null); setLoading(true); setError("");
    void request<ScoringObservations>(
      "/api/ml/scoring/results/" + encodeURIComponent(result.id) + "/observations?limit=25",
    ).then(page => {
      if (active && page.result_id === result.id) setLoaded(page);
    }).catch(e => { if (active) setError(formatError(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [visited, result.id, retry]);

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
    } catch (e) { setError(formatError(e)); }
    finally { pendingMore.current = false; setLoadingMore(false); }
  }
  const selected = loaded?.items.find(item => item.row_index === selectedRow);
  return <section className="panel scoring-observation-explorer" aria-label="점수 관측치 탐구">
    <div className="pipeline-section-heading"><div>
      <h4>이 Scoring 결과의 상위 점수 관측치</h4>
      <p>개별 관측치를 점수 높은 순서로 확인합니다. 점수는 확률이나 판정 결과가 아니며
        별도 정책을 적용하지 않은 순위입니다.</p>
    </div>
      <button type="button" className="secondary" aria-expanded={expanded}
        onClick={() => { setVisited(true); setExpanded(value => !value); }}>
        {expanded ? "점수 관측치 접기" : "상위 점수 관측치 살펴보기 →"}
      </button>
    </div>
    {visited && <div hidden={!expanded}>
      {loading && <p role="status">불변 점수 결과에서 관측치를 읽고 있습니다.</p>}
      {error && <div role="alert" className="pipeline-readiness-blocked">
        <p>{error}</p>
        {!loaded && <button type="button" className="secondary"
          onClick={() => setRetry(n => n + 1)}>관측치 다시 조회</button>}
      </div>}
      {loaded && <>
        <p className="hint">점수화된 전체 {result.row_count.toLocaleString("ko-KR")}행 중
          현재 점수순 {loaded.items.length.toLocaleString("ko-KR")}행을 불러왔습니다.</p>
        {loaded.items.length === 0 && <p>조회할 관측치가 없습니다.</p>}
        {selectedRow === null && loaded.items.length > 0 &&
          <button type="button" onClick={() => setSelectedRow(loaded.items[0].row_index)}>
            가장 높은 점수 관측치 살펴보기 →
          </button>}
        {loaded.items.length > 0 && <label className="pipeline-candidate-picker">
          탐구할 관측치
          <select aria-label="Scoring 결과 관측치 선택"
            value={selectedRow === null ? "" : String(selectedRow)}
            onChange={e => setSelectedRow(e.target.value === "" ? null : Number(e.target.value))}>
            <option value="">관측치를 선택하세요</option>
            {loaded.items.map(item => <option key={item.row_index} value={item.row_index}>
              #{item.row_index.toLocaleString("ko-KR")} · 이상 점수 {item.anomaly_score.toPrecision(6)}
            </option>)}
          </select>
        </label>}
        {loaded.next_cursor && <button type="button" className="secondary"
          disabled={loadingMore} onClick={() => void more()}>
          {loadingMore ? "점수 결과 불러오는 중…" : "다음 점수순 관측치 25건 불러오기"}
        </button>}
        {selected && <PipelineCandidateInvestigation
          key={result.id + ":" + selected.row_index}
          candidate={selected} scoringResultId={result.id}
          modelRevisionId={result.model_revision_id}
          canRun={canRun} canCancel={canCancel} onOpenResource={onOpenResource} />}
      </>}
    </div>}
  </section>;
}
