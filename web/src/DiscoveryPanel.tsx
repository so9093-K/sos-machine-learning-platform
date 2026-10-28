import { useEffect, useMemo, useState } from "react";
import type * as echarts from "echarts";
import { DataChart, CHART_COLORS } from "./DataChart";
import { chartValue, explorationCategory, explorationValue, explorationMetricLabel } from "./exploration-format";
import { request, jsonPost, submitJob, formatError } from "./api";
import { AnalysisEvidence, analysisScopeLabel, dataSelectionLabel } from "./AnalysisEvidence";
import type { DatasetOverview, DatasetColumns, Exploration, ExplorationRequest, ExplorationJob } from "./types";
import type { ExplorationDraft } from "./ExplorationDraft";

function analysisLabel(basis: string) {
  return basis === "parquet_metadata"
    ? "Parquet 파일 메타데이터"
    : basis === "upload_validation"
      ? "등록 시 전체 검사"
      : "행 수는 아직 전체 계산하지 않았습니다";
}

/** 사람과 외부 API가 동일하게 사용하는 Dataset Discovery 계약을 표시한다. */
export function DiscoveryPanel({
  datasetId, canCreate, onCreated, onOpenJob, draft, onDraftChange,
  overview, overviewStatus, overviewError, onRetryOverview,
}: {
  datasetId: string;
  canCreate: boolean;
  onCreated: () => Promise<void>;
  onOpenJob: (jobId: string) => void;
  draft: ExplorationDraft | null;
  onDraftChange: (request: ExplorationRequest) => void;
  overview: DatasetOverview | null;
  overviewStatus: "loading" | "ready" | "error";
  overviewError: string;
  onRetryOverview: () => void;
}) {
  const [columns, setColumns] = useState<DatasetColumns | null>(null);
  const [search, setSearch] = useState("");
  const [result, setResult] = useState<Exploration | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [running, setRunning] = useState(false);
  const [fullJobId, setFullJobId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setStatus("loading"); setColumns(null);
    setResult(null); setError("");
    request<DatasetColumns>(
      `/api/datasets/${encodeURIComponent(datasetId)}/columns`,
    ).then(c => {
      if (!active) return;
      setColumns(c);
      setStatus("ready");
    }).catch(e => { if (active) { setError(formatError(e)); setStatus("error"); } });
    return () => { active = false; };
  }, [datasetId, reload]);
  const shown = useMemo(() => columns?.columns.filter(c =>
    c.name.toLowerCase().includes(search.toLowerCase())) ?? [], [columns, search]);
  const numeric = columns?.columns.filter(c => /Int|Float|Decimal/.test(c.dtype)) ?? [];
  const metric = draft?.request.metric ?? (numeric.length ? "mean" : "count");
  const measure = draft?.request.column ?? "";
  const groupBy = draft?.request.group_by ?? "";
  const candidates = metric === "count" || metric === "null_count"
    ? (columns?.columns ?? []) : numeric;
  const selectedMeasure = candidates.some(item => item.name === measure)
    ? measure : (candidates[0]?.name ?? "");
  const invalidDraft = !!draft && !!columns && (
    !columns.columns.some(c => c.name === draft.request.column) ||
    (draft.request.group_by !== null &&
      !columns.columns.some(c => c.name === draft.request.group_by)) ||
    (metric !== "count" && metric !== "null_count" &&
      !numeric.some(c => c.name === draft.request.column))
  );
  const selectedRequest: ExplorationRequest = {
    column: selectedMeasure,
    metric,
    group_by: groupBy || null,
  };
  function updateRequest(patch: Partial<ExplorationRequest>) {
    onDraftChange({ ...selectedRequest, ...patch });
    setResult(null);
    setFullJobId(null);
    setError("");
  }
  async function run() {
    if (!selectedMeasure || invalidDraft) return;
    setRunning(true); setError(""); setResult(null);
    try {
      const answer = await jsonPost<Exploration>(
        `/api/datasets/${datasetId}/explorations`, selectedRequest);
      setResult(answer);
    } catch (e) { setError(formatError(e)); }
    finally { setRunning(false); }
  }
  async function runFullJob() {
    if (!selectedMeasure || invalidDraft) return;
    setRunning(true); setError(""); setFullJobId(null);
    try {
      const job = await submitJob<ExplorationJob>("/api/explorations/jobs", {
        dataset_id: datasetId,
        exploration: selectedRequest,
      });
      setFullJobId(job.id);
      try {
        await onCreated();
      } catch (e) {
        // Job 202 접수는 성공했으므로 목록 갱신 오류를 제출 실패로 표현하지 않는다.
        setError(`작업은 정상 접수됐습니다. 작업 이력을 다시 확인하세요. ${formatError(e)}`);
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
    <h3>탐색 분석</h3>
    {status === "loading" && <p role="status" className="muted">컬럼 목록을 확인하는 중입니다.</p>}
    {overviewStatus === "loading" && <p className="muted" role="status">기본 정보 조회 중</p>}
    {overviewStatus === "error" && <div role="alert" className="alert">
      기본 정보 조회 실패: {overviewError}
      <button className="secondary" onClick={onRetryOverview}>기본 정보 다시 조회</button>
    </div>}
    {status === "error" && <button className="secondary"
      onClick={() => setReload(previous => previous + 1)}>메타데이터 다시 시도</button>}
    {overview && <p className="hint">분석 대상 · {overview.column_count.toLocaleString()}컬럼
      {" · "}{overview.row_count === null
        ? "원본 행 수 미계산"
        : overview.row_count.toLocaleString() + "행 · " + analysisLabel(overview.row_count_basis)}</p>}
    {columns && <>
      <h4>탐색 조건</h4>
      {draft?.source && <div className="exploration-origin" role="status">
        <strong>탐색 후보에서 가져온 조건 · {draft.source.title}</strong>
        <p>{draft.source.reason}</p>
        <small>구성 근거: {analysisScopeLabel(draft.source.profileScope)} · 사용자가 조건을 확인하고 실행합니다.</small>
      </div>}
      {invalidDraft && <div role="alert" className="alert">
        이전 탐색 조건이 현재 Dataset 스키마 또는 집계 방식과 일치하지 않습니다.
        <button type="button" className="secondary" onClick={() => updateRequest({
          column: selectedMeasure, metric, group_by: null,
        })} disabled={!selectedMeasure}>기본 탐색 조건 적용</button>
      </div>}
      <p className="muted">부분 데이터 집계는 정렬 기준 없이 제한 조회한 행을 대상으로 합니다. 전체 데이터를 확인하려면 별도 Job을 실행하세요.</p>
      <div className="chart-controls">
        <label>분석할 컬럼 <select aria-label="분석할 컬럼" value={selectedMeasure}
          disabled={running || candidates.length === 0}
          onChange={e => updateRequest({ column: e.target.value })}>
          {candidates.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
        </select></label>
        {candidates.length === 0 && <p role="status" className="muted">선택한 집계를 지원하는 숫자형 컬럼이 없습니다. 행 수 또는 NULL 수 집계를 선택하세요.</p>}
        <label>집계 <select aria-label="집계 방식" value={metric} disabled={running}
          onChange={e => {
            const nextMetric = e.target.value as ExplorationRequest["metric"];
            const nextCandidates = nextMetric === "count" || nextMetric === "null_count"
              ? (columns?.columns ?? []) : numeric;
            const nextColumn = nextCandidates.some(c => c.name === selectedMeasure)
              ? selectedMeasure : (nextCandidates[0]?.name ?? "");
            updateRequest({ metric: nextMetric, column: nextColumn });
          }}>
          <option value="mean">{explorationMetricLabel("mean")}</option><option value="min">{explorationMetricLabel("min")}</option>
          <option value="max">{explorationMetricLabel("max")}</option><option value="count">{explorationMetricLabel("count")}</option>
          <option value="null_count">{explorationMetricLabel("null_count")}</option>
        </select></label>
        <label>그룹 <select aria-label="집계 그룹" value={groupBy} disabled={running}
          onChange={e => updateRequest({ group_by: e.target.value || null })}>
          <option value="">그룹 없음</option>
          {columns.columns.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
        </select></label>
        <button disabled={running || !selectedMeasure || invalidDraft} onClick={() => void run()}>
          {running ? "분석 중…" : "부분 데이터 집계"}
        </button>
        {canCreate && <button className="secondary" disabled={running || !selectedMeasure || invalidDraft}
          onClick={() => void runFullJob()}>전체 데이터 집계</button>}
      </div>
      {fullJobId && <p role="status" className="muted">
        전체 집계가 접수됐습니다.
        <button type="button" className="secondary" onClick={() => onOpenJob(fullJobId)}>집계 작업 상세 →</button>
      </p>}
      {result && <>
        <AnalysisEvidence
          summary={analysisScopeLabel(result.scope) + (result.sample_limit === null
            ? " · 전체 행 대상" : " · 최대 " + result.sample_limit.toLocaleString() + "행 대상")}
          note={result.scope === "sample"
            ? "제한 조회한 행의 집계 결과로, 전체 데이터의 분포를 대표하지 않습니다."
              + (result.truncated ? " 집계한 그룹 중 일부만 표시됩니다." : "")
            : result.truncated ? "전체 데이터를 집계했지만 표시되는 그룹은 일부입니다." : undefined}
          details={[
            { label: "데이터 선택 방식", value: result.scope === "sample"
              ? dataSelectionLabel("limited_rows") : dataSelectionLabel("full_scan") },
            { label: "실제 분석 행 수", value: "현재 결과에 제공되지 않음" },
            ...(result.sample_limit === null ? [] : [{
              label: "조회 행 수 상한", value: result.sample_limit.toLocaleString() + "행",
            }]),
            { label: "그룹 표시", value: result.truncated ? "일부 그룹만 표시" : "그룹 제한으로 생략된 결과 없음" },
            { label: "집계 계산", value: result.metric === "mean"
              ? "부동소수 평균 계산 (근사 가능)" : "분석 범위와 결과 정확도는 별개" },
            { label: "Dataset 판본", value: result.version },
          ]}
        />
        <div className="table-scroll"><table><thead><tr><th>그룹</th><th>결과</th></tr></thead>
          <tbody>{result.rows.map((r, i) => <tr key={i}><td>{explorationCategory(r.category, result.group_by)}</td>
            <td>{explorationValue(r.value)}</td></tr>)}</tbody>
        </table></div>
        {chart ? <DataChart option={chart} ariaLabel="부분 데이터 집계 결과 차트" /> :
          <p className="muted">정밀 수치 문자열이나 계산 불가 값은 차트에서 제외합니다. 정확한 값은 위 표에서 확인하세요.</p>}
      </>}
      <details className="column-catalog">
        <summary>컬럼 목록 및 검색</summary>
      <h4>컬럼 검색</h4>
      <input aria-label="컬럼 이름 검색" value={search} onChange={e => setSearch(e.target.value)}
        placeholder="컬럼 이름으로 찾기" />
      <p className="muted">{shown.length}개 표시 / 전체 {columns.total}개 컬럼</p>
      <div className="column-search-results" role="list">
        {shown.map(c => <div role="listitem" key={c.name}>
          <span>{c.name}</span><small>{c.dtype}</small></div>)}
      </div>
      </details>
    </>}
    {error && <p role="alert" className="alert">{error}</p>}
  </section>;
}
