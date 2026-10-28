import { useEffect, useRef, useState } from "react";
import { request, formatError } from "./api";
import { explorationCategory, explorationValue, explorationMetricLabel } from "./exploration-format";
import { AnalysisEvidence, analysisScopeLabel, dataSelectionLabel } from "./AnalysisEvidence";
import type { Dataset, Job, JobSummary, ExplorationJob, ExplorationJobSummary } from "./types";
import { formatRecordedAt, jobStatusLabel } from "./display-format";

type AnyJobSummary = JobSummary | ExplorationJobSummary;
type AnyJob = Job | ExplorationJob;
type JobKind = "data" | "exploration";
const TERMINAL = new Set(["Succeeded", "Failed", "Cancelled"]);

export function dataJobLabel(job: Pick<AnyJobSummary, "kind" | "spec">): string {
  if (job.kind === "profile") return job.spec.mode === "understanding"
    ? job.spec.scope === "full" ? "데이터 이해 · 전체 행" : "데이터 이해 · 표본" : "전체 데이터 프로파일";
  return ({ iceberg_import: "Iceberg 적재", exploration: "전체 집계", transform: "데이터 변환",
    feature_register: "Feature Service 등록", feature_retrieve: "Feature 시점 조회",
    feature_retire: "Feature Service 폐기", ml_playground: "단일 입력 점수 계산" } as Record<string, string>)[job.kind] ?? job.kind;
}

/** Discovery 전체 분석 결과는 일반 사용자에게 표로 우선 제공한다. */
function DiscoveryJobResult({ result }: { result: Record<string, unknown> }) {
  const [page, setPage] = useState(0);
  if (!Array.isArray(result.rows)) return null;
  const rows = result.rows as Array<{ category: unknown; value: unknown }>;
  const currentPage = Math.min(page, Math.max(0, Math.ceil(rows.length / 12) - 1));
  const metric = String(result.metric);
  const metricLabel = ["mean", "min", "max", "count", "null_count"].includes(metric)
    ? explorationMetricLabel(metric as Parameters<typeof explorationMetricLabel>[0])
    : metric;
  return <section className="discovery-job-result" aria-label="전체 집계 결과">
    <h4>전체 집계 결과</h4>
    <p className="muted">전체 입력 · 컬럼 {String(result.column)} · 집계 {metricLabel}
      {result.group_by ? ` · 그룹 ${String(result.group_by)}` : " · 그룹 없음"}</p>
    <AnalysisEvidence
      summary={analysisScopeLabel(String(result.scope)) + " · " + metricLabel}
      note={result.truncated === true
        ? "전체 데이터를 집계했지만 표시되는 그룹은 일부입니다."
        : undefined}
      details={[
        { label: "데이터 선택 방식", value: result.scope === "full"
          ? dataSelectionLabel("full_scan") : "선택 방식 미확인" },
        { label: "실제 분석 행 수", value: "현재 결과에 제공되지 않음" },
        { label: "그룹 표시", value: result.truncated === true
          ? "일부 그룹만 표시" : result.truncated === false
            ? "그룹 제한으로 생략된 결과 없음" : "표시 범위 미확인" },
        { label: "집계 계산", value: result.metric === "mean"
          ? "부동소수 평균 계산 (근사 가능)" : "개별 지표의 정확도는 별도 확인" },
        { label: "Dataset 판본", value: typeof result.version === "string"
          ? result.version : "미확인" },
      ]}
    />
    <div className="table-scroll"><table><thead><tr><th>그룹</th><th>집계값</th></tr></thead>
      <tbody>{rows.slice(currentPage * 12, (currentPage + 1) * 12).map((row, index) => <tr key={index}>
        <td>{explorationCategory(row.category, typeof result.group_by === "string" ? result.group_by : null)}</td>
        <td>{explorationValue(row.value as number | string | null)}</td>
      </tr>)}</tbody></table></div>
    {rows.length > 12 && <div className="feature-result-pages">
      <span>{rows.length}개 그룹 중 {currentPage * 12 + 1}–{Math.min(rows.length, (currentPage + 1) * 12)}개</span>
      <button type="button" className="secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>이전 그룹</button>
      <button type="button" className="secondary" disabled={(currentPage + 1) * 12 >= rows.length} onClick={() => setPage(currentPage + 1)}>다음 그룹</button>
    </div>}
    {rows.length === 0 && <p className="muted">표시 가능한 그룹이 없습니다.</p>}
  </section>;
}
type Props = {
  jobs: AnyJobSummary[]; datasets: Dataset[]; busy: boolean; canCancel: boolean;
  selectedJobId?: string; selectedJobKind?: JobKind;
  loading?: boolean; readError?: boolean;
  nextCursor: string | null; discoveryNextCursor: string | null;
  onRefresh: () => void; onLoadMore: () => void; onLoadMoreDiscovery: () => void;
  onSelectJob: (id: string, kind: JobKind) => void; onOpen: (id: string) => void;
};

/** 목록은 짧게 탐색하고 선택한 작업의 상태·결과는 직접 주소에서도 독립적으로 조회한다. */
export function JobsPanel({ jobs, datasets, busy, canCancel, selectedJobId, selectedJobKind = "data",
  loading = false, readError = false, nextCursor, discoveryNextCursor,
  onRefresh, onLoadMore, onLoadMoreDiscovery, onSelectJob, onOpen }: Props) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [retry, setRetry] = useState(0);
  const [cancelling, setCancelling] = useState(false);
  const key = selectedJobKind + ":" + (selectedJobId ?? "");
  const endpoint = selectedJobKind === "exploration" ? "/api/explorations/jobs" : "/api/jobs";
  const [detail, setDetail] = useState<{ key: string; job: AnyJob | null; error: string } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const job = detail?.key === key ? detail.job : null;
  const error = detail?.key === key ? detail.error : "";
  useEffect(() => {
    if (!selectedJobId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setDetail(previous => previous?.key === key ? { ...previous, error: "" } : { key, job: null, error: "" });
    async function load() {
      try {
        const next = await request<AnyJob>(endpoint + "/" + encodeURIComponent(selectedJobId!));
        if (!active) return;
        if (next.id !== selectedJobId || (next.kind === "exploration") !== (selectedJobKind === "exploration"))
          throw new Error("조회한 작업과 선택한 작업의 종류·식별자가 일치하지 않습니다.");
        setDetail({ key, job: next, error: "" });
        if (!TERMINAL.has(next.status)) timer = setTimeout(() => void load(), 2000);
      } catch (cause) {
        if (active) setDetail(previous => ({ key, job: previous?.key === key ? previous.job : null, error: formatError(cause) }));
      }
    }
    void load();
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [key, retry]);
  useEffect(() => {
    if (!selectedJobId) return;
    const frame = requestAnimationFrame(() => {
      heading.current?.focus({ preventScroll: true });
      const bounds = heading.current?.getBoundingClientRect();
      if (bounds && (bounds.top < 0 || bounds.top > innerHeight - 150))
        heading.current?.scrollIntoView({ block: "start", behavior: "auto" });
    });
    return () => cancelAnimationFrame(frame);
  }, [key]);
  async function cancel() {
    if (!job || !canCancel || TERMINAL.has(job.status) || job.cancel_requested || cancelling || job.kind === "feature_retire") return;
    setCancelling(true);
    try {
      const updated = await request<AnyJob>(endpoint + "/" + encodeURIComponent(job.id) + "/cancel", { method: "POST" });
      setDetail(previous => previous?.key === key ? { key, job: updated, error: "" } : previous);
      setRetry(value => value + 1); onRefresh();
    } catch (cause) { setDetail(previous => previous?.key === key ? { ...previous, error: formatError(cause) } : previous); }
    finally { setCancelling(false); }
  }
  const datasetName = (id: string | null) => datasets.find(item => item.id === id)?.name ?? "현재 목록 밖의 Dataset";
  const filtered = jobs.filter(item => [dataJobLabel(item), datasetName(item.dataset_id), jobStatusLabel(item.status), item.id]
    .some(value => value.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())));
  const currentPage = Math.min(page, Math.max(0, Math.ceil(filtered.length / 8) - 1));
  return <section className="panel" aria-label="실행 작업">
    <div className="panel-head"><div><h3>데이터·탐색 작업</h3><p>작업 선택 후 실행 상태와 결과를 확인합니다.</p></div>
      <button type="button" className="secondary" disabled={busy || loading} onClick={() => { onRefresh(); setRetry(value => value + 1); }}>새로고침</button></div>
    <div className="data-job-workspace">
      <div className="data-job-list">
        <label className="data-job-search">작업 검색<input type="search" aria-label="데이터 작업 검색" value={search}
          placeholder="작업·데이터·상태" onChange={event => { setSearch(event.target.value); setPage(0); }} /></label>
        {loading && <p role="status">작업 목록을 불러오는 중입니다.</p>}
        {!loading && !readError && jobs.length === 0 && <p className="muted">제출한 데이터·탐색 작업이 없습니다.</p>}
        {!loading && jobs.length > 0 && filtered.length === 0 && <p className="muted">검색 조건에 맞는 작업이 없습니다.</p>}
        {filtered.slice(currentPage * 8, (currentPage + 1) * 8).map(item => <button type="button" key={item.id}
          className="secondary data-job-choice" aria-pressed={item.id === selectedJobId}
          onClick={() => onSelectJob(item.id, item.kind === "exploration" ? "exploration" : "data")}>
          <strong>{dataJobLabel(item)}</strong><span className={`status ${item.status.toLowerCase()}`}>{jobStatusLabel(item.status)}</span>
          <span>{datasetName(item.dataset_id)}</span><small>{formatRecordedAt(item.created_at)}</small>
        </button>)}
        {filtered.length > 8 && <div className="feature-result-pages">
          <span>{filtered.length}건 중 {currentPage * 8 + 1}–{Math.min(filtered.length, (currentPage + 1) * 8)}건</span>
          <button type="button" className="secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>이전 작업</button>
          <button type="button" className="secondary" disabled={(currentPage + 1) * 8 >= filtered.length} onClick={() => setPage(currentPage + 1)}>다음 작업</button>
        </div>}
        {(nextCursor || discoveryNextCursor) && <div className="actions">
          {nextCursor && <button type="button" className="secondary" disabled={busy} onClick={onLoadMore}>이전 데이터 작업 조회</button>}
          {discoveryNextCursor && <button type="button" className="secondary" disabled={busy} onClick={onLoadMoreDiscovery}>이전 집계 작업 조회</button>}
        </div>}
      </div>
      <section className="data-job-detail" aria-label="선택한 작업 상세">
        <h4 ref={heading} tabIndex={-1}>선택 작업{job ? " · " + dataJobLabel(job) : ""}</h4>
        {!selectedJobId && <p className="muted">목록에서 작업을 선택하세요.</p>}
        {selectedJobId && !job && !error && <p role="status">선택한 작업을 불러오는 중입니다.</p>}
        {error && <div role="alert"><p>작업 상태 조회 실패 · {error}</p>
          {job && <p className="hint">아래는 마지막으로 조회한 상태입니다.</p>}
          <button type="button" className="secondary" onClick={() => setRetry(value => value + 1)}>선택 작업 다시 조회</button></div>}
        {job && <>
          <p className={`status ${job.status.toLowerCase()}`} role="status">{jobStatusLabel(job.status)}{job.cancel_requested ? " · 취소 요청됨" : ""}</p>
          <p className="hint">{datasetName(job.dataset_id)} · {formatRecordedAt(job.created_at)}</p>
          {job.error && <div role="alert"><p>{job.error}</p>
            {job.error_code && <small>{job.error_code}</small>}
            {job.retry_policy === "manual_verification_required" && <p>커밋 상태 확인 후 재실행이 필요합니다.</p>}
          </div>}
          {job.status === "Succeeded" && job.result && <>
            {"row_count" in job.result && typeof job.result.row_count === "number" && <p>분석한 행 · {job.result.row_count.toLocaleString("ko-KR")}</p>}
            {job.kind === "exploration" && <DiscoveryJobResult key={job.id} result={job.result} />}
            {typeof job.result.dataset_id === "string" && <button type="button" onClick={() => onOpen(job.result!.dataset_id as string)}>
              {job.kind === "profile" || job.kind === "exploration" ? "분석 원본 데이터 조회 →" : "결과 데이터 조회 →"}</button>}
            {job.kind === "feature_register" && typeof job.result.feature_service === "string" && <p>등록 Feature Service · {job.result.feature_service}</p>}
            {job.kind === "feature_retire" && <p>Feature Service 등록 폐기 완료</p>}
          </>}
          {canCancel && !TERMINAL.has(job.status) && job.kind !== "feature_retire" &&
            !(job.kind === "iceberg_import" && job.status === "Running") &&
            <button type="button" className="secondary" disabled={cancelling || job.cancel_requested} onClick={() => void cancel()}>취소 요청</button>}
          {job.kind === "feature_retire" && !TERMINAL.has(job.status) && <p className="hint">등록 폐기는 취소할 수 없습니다. 작업 상태를 확인하세요.</p>}
          <details><summary>작업 설정·결과·실행 근거</summary>
            <dl className="evidence-facts"><div><dt>작업 ID</dt><dd>{job.id}</dd></div></dl>
            <pre>{JSON.stringify({ spec: job.spec, result: job.result, attempts: job.attempts,
              error_code: job.error_code, failure_stage: job.failure_stage, request_id: job.request_id }, null, 2)}</pre>
          </details>
        </>}
      </section>
    </div>
  </section>;
}
