import { useEffect, useMemo, useState } from "react";
import type * as echarts from "echarts";
import { DataChart, CHART_COLORS } from "./DataChart";
import { chartValue, explorationCategory, explorationValue } from "./exploration-format";
import { request, jsonPost, formatError } from "./api";
import type { DatasetOverview, DatasetColumns, Exploration, ExplorationRequest, ExplorationJob } from "./types";

function analysisLabel(basis: string) {
  return basis === "parquet_metadata"
    ? "Parquet 파일 메타데이터"
    : "행 수는 아직 전체 계산하지 않았습니다";
}

/** 사람과 외부 API가 동일하게 사용하는 Dataset Discovery 계약을 표시한다. */
export function DiscoveryPanel({
  datasetId, canCreate, onCreated,
}: { datasetId: string; canCreate: boolean; onCreated: () => Promise<void> }) {
  const [overview, setOverview] = useState<DatasetOverview | null>(null);
  const [columns, setColumns] = useState<DatasetColumns | null>(null);
  const [search, setSearch] = useState("");
  const [measure, setMeasure] = useState("");
  const [groupBy, setGroupBy] = useState("");
  const [metric, setMetric] = useState<ExplorationRequest["metric"]>("mean");
  const [result, setResult] = useState<Exploration | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [running, setRunning] = useState(false);
  const [fullJobId, setFullJobId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setStatus("loading"); setOverview(null); setColumns(null);
    setResult(null); setError("");
    Promise.all([
      request<DatasetOverview>(`/api/datasets/${datasetId}/overview`),
      request<DatasetColumns>(`/api/datasets/${datasetId}/columns`),
    ]).then(([o, c]) => {
      if (!active) return;
      setOverview(o); setColumns(c);
      const firstNumeric = c.columns.find(x => /Int|Float|Decimal/.test(x.dtype));
      setMeasure(previous => c.columns.some(item => item.name === previous)
        ? previous : (firstNumeric?.name ?? c.columns[0]?.name ?? ""));
      if (!firstNumeric) setMetric("count");
      setGroupBy(previous => c.columns.some(item => item.name === previous) ? previous : "");
      setStatus("ready");
    }).catch(e => { if (active) { setError(formatError(e)); setStatus("error"); } });
    return () => { active = false; };
  }, [datasetId, reload]);
  const shown = useMemo(() => columns?.columns.filter(c =>
    c.name.toLowerCase().includes(search.toLowerCase())) ?? [], [columns, search]);
  const numeric = columns?.columns.filter(c => /Int|Float|Decimal/.test(c.dtype)) ?? [];
  const candidates = metric === "count" || metric === "null_count"
    ? (columns?.columns ?? []) : numeric;
  const selectedMeasure = candidates.some(item => item.name === measure)
    ? measure : (candidates[0]?.name ?? "");
  async function run() {
    if (!selectedMeasure) return;
    setRunning(true); setError(""); setResult(null);
    try {
      const body: ExplorationRequest = {
        column: selectedMeasure, metric, group_by: groupBy || null,
      };
      const answer = await jsonPost<Exploration>(
        `/api/datasets/${datasetId}/explorations`, body);
      setResult(answer);
    } catch (e) { setError(formatError(e)); }
    finally { setRunning(false); }
  }
  async function runFullJob() {
    if (!selectedMeasure) return;
    setRunning(true); setError(""); setFullJobId(null);
    try {
      const job = await submitJob<ExplorationJob>("/api/explorations/jobs", {
        dataset_id: datasetId,
        exploration: { column: selectedMeasure, metric, group_by: groupBy || null },
      });
      setFullJobId(job.id);
      try {
        await onCreated();
      } catch (e) {
        // Job 202 접수는 성공했으므로 목록 갱신 오류를 제출 실패로 표현하지 않는다.
        setError(`작업은 정상 접수됐습니다. Jobs 목록을 다시 확인하세요. ${formatError(e)}`);
      }
    } catch (e) { setError(formatError(e)); }
    finally { setRunning(false); }
  }
  const chart = useMemo<echarts.EChartsCoreOption | null>(() => {
    if (!result?.rows.some(row => chartValue(row.value) !== null)) return null;
    return {
      backgroundColor: "transparent", textStyle: { color: CHART_COLORS.text },
      tooltip: { trigger: "axis" },
      grid: { left: 52, right: 15, top: 22, bottom: 55 },
      xAxis: { type: "category", data: result.rows.map(x => explorationCategory(x.category, result.group_by)),
        axisLabel: { color: CHART_COLORS.label, hideOverlap: true, rotate: 20 } },
      yAxis: { type: "value", axisLabel: { color: CHART_COLORS.label } },
      series: [{ type: "bar", data: result.rows.map(x => chartValue(x.value)),
        itemStyle: { color: CHART_COLORS.bar } }],
    };
  }, [result]);
  return <section className="discovery-panel" aria-label="Dataset Discovery">
    <h4>데이터 발견</h4>
    {status === "loading" && <p role="status" className="muted">구조와 메타데이터를 확인하는 중입니다.</p>}
    {status === "error" && <button className="secondary"
      onClick={() => setReload(previous => previous + 1)}>메타데이터 다시 시도</button>}
    {overview && <div className="cards">
      <div className="stat"><span>전체 행</span><strong>{overview.row_count?.toLocaleString() ?? "미계산"}</strong>
        <small>{analysisLabel(overview.row_count_basis)}</small></div>
      <div className="stat"><span>컬럼 수</span><strong>{overview.column_count}</strong></div>
      <div className="stat"><span>원본 크기</span><strong>{overview.size_bytes == null
        ? "외부 Snapshot" : `${(overview.size_bytes / (1024 * 1024)).toFixed(2)} MiB`}</strong></div>
    </div>}
    {columns && <>
      <h4>컬럼 검색</h4>
      <input aria-label="컬럼 이름 검색" value={search} onChange={e => setSearch(e.target.value)}
        placeholder="컬럼 이름으로 찾기" />
      <p className="muted">{shown.length}개 표시 / 전체 {columns.total}개 컬럼</p>
      <div className="column-search-results" role="list">
        {shown.map(c => <div role="listitem" key={c.name}>
          <span>{c.name}</span><small>{c.dtype}</small></div>)}
      </div>
      <h4>분석 집계</h4>
      <p className="muted">대화형 탐색은 제한된 선두 샘플을 사용하고, 정확한 전체 집계는 별도 Worker Job으로 실행합니다.</p>
      <div className="chart-controls">
        <label>분석할 컬럼 <select aria-label="분석할 컬럼" value={selectedMeasure}
          disabled={running || candidates.length === 0}
          onChange={e => { setMeasure(e.target.value); setResult(null); }}>
          {candidates.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
        </select></label>
        {candidates.length === 0 && <p role="status" className="muted">선택한 집계를 지원하는 숫자형 컬럼이 없습니다. 행 수 또는 NULL 수 집계를 선택하세요.</p>}
        <label>집계 <select aria-label="집계 방식" value={metric} disabled={running}
          onChange={e => { setMetric(e.target.value as ExplorationRequest["metric"]); setResult(null); }}>
          <option value="mean">평균</option><option value="min">최솟값</option>
          <option value="max">최댓값</option><option value="count">행 수</option>
          <option value="null_count">NULL 수</option>
        </select></label>
        <label>그룹 <select aria-label="집계 그룹" value={groupBy} disabled={running}
          onChange={e => { setGroupBy(e.target.value); setResult(null); }}>
          <option value="">전체</option>
          {columns.columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
        </select></label>
        <button disabled={running || !selectedMeasure} onClick={() => void run()}>
          {running ? "분석 중…" : "샘플 집계"}
        </button>
        {canCreate && <button className="secondary" disabled={running || !selectedMeasure}
          onClick={() => void runFullJob()}>전체 집계 Job 실행</button>}
      </div>
      {fullJobId && <p role="status" className="muted">
        전체 집계 Job을 제출했습니다. Jobs에서 상태와 결과를 확인하세요. 작업 ID: {fullJobId}
      </p>}
      {result && <>
        <p className="muted">샘플 최대 {result.sample_limit?.toLocaleString()}행 · Dataset 판본 {result.version.slice(0, 12)}
          {result.truncated ? " · 표시 그룹 수 제한 적용" : ""}</p>
        <div className="table-scroll"><table><thead><tr><th>그룹</th><th>결과</th></tr></thead>
          <tbody>{result.rows.map((r, i) => <tr key={i}><td>{explorationCategory(r.category, result.group_by)}</td>
            <td>{explorationValue(r.value)}</td></tr>)}</tbody>
        </table></div>
        {chart ? <DataChart option={chart} ariaLabel="샘플 집계 결과 차트" /> :
          <p className="muted">정밀 수치 문자열이나 계산 불가 값은 차트에서 제외합니다. 정확한 값은 위 표에서 확인하세요.</p>}
      </>}
    </>}
    {error && <p role="alert" className="alert">{error}</p>}
  </section>;
}
