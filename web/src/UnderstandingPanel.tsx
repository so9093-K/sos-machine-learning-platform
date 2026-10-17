/** Dataset 판본의 실제 Profile과 실행 가능한 탐색을 공통 API로 표시한다. */
import { useEffect, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { explorationMetricLabel } from "./exploration-format";
import type { DatasetOverview, ExplorationRequest, Job } from "./types";

import type { components } from "./api.generated";

type Understanding = components["schemas"]["UnderstandingOut"];

export function UnderstandingPanel({
  datasetId, canCreate, onChooseCandidate, overview, overviewStatus, overviewError, onRetryOverview,
}: {
  datasetId: string;
  canCreate: boolean;
  overview: DatasetOverview | null;
  overviewStatus: "loading" | "ready" | "error";
  overviewError: string;
  onRetryOverview: () => void;
  onChooseCandidate: (
    candidate: { request: ExplorationRequest; title: string; reason: string },
    version: string, profileScope: "sample" | "full",
  ) => void;
}) {
  const [state, setState] = useState<Understanding | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [jobError, setJobError] = useState("");
  const [revision, setRevision] = useState(0);
  const [columnSearch, setColumnSearch] = useState("");
  const [columnFilter, setColumnFilter] = useState("all");

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let missingCompletedProfile = 0;
    async function refresh() {
      try {
        const data = await request<Understanding>(
          "/api/datasets/" + encodeURIComponent(datasetId) + "/understanding"
        );
        if (!active) return;
        setState(data);
        setError("");
        if (data.job_status === "Queued" || data.job_status === "Running") {
          timer = setTimeout(() => { void refresh(); }, 1500);
        } else if (data.job_status === "Succeeded" && !data.profile) {
          // 마감 시점의 교차 읽기 또는 지연된 결과 표시를 회복하되 무한 폴링하지 않는다.
          if (++missingCompletedProfile <= 3) {
            timer = setTimeout(() => { void refresh(); }, 1500);
          } else {
            setError("완료된 분석의 프로파일을 조회하지 못했습니다. 상태를 다시 조회하세요.");
          }
        }
        // 한 Dataset에서 분석 접수가 여러 번 반복되지 않도록 서버 상태를 먼저 확인한다.
        if (!data.profile && !data.job_status && canCreate) {
          const job = await submitJob<Job>(
            "/api/datasets/" + encodeURIComponent(datasetId) + "/understanding/jobs",
            { scope: "sample" }
          );
          if (!active) return;
          setState(previous => previous ? { ...previous, job_id: job.id, job_status: job.status } : null);
          timer = setTimeout(() => { void refresh(); }, 1500);
        }
      } catch (cause) {
        if (active) setError(formatError(cause));
      }
    }
    void refresh();
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [datasetId, canCreate, revision]);

  async function start(scope: "sample" | "full") {
    setBusy(true); setJobError(""); setError("");
    try {
      await submitJob<Job>(
        "/api/datasets/" + encodeURIComponent(datasetId) + "/understanding/jobs",
        { scope },
      );
      setRevision(n => n + 1);
    } catch (cause) { setJobError(formatError(cause)); }
    finally { setBusy(false); }
  }

  const profile = state?.profile;
  const columns = profile?.columns ?? [];
  const filteredColumns = columns.filter(col => {
    if (!col.name.toLowerCase().includes(columnSearch.trim().toLowerCase())) return false;
    if (columnFilter === "all") return true;
    if (columnFilter === "constant") return col.is_constant;
    if (columnFilter === "missing") return col.null_count > 0;
    return col.roles.some(role => role.role === columnFilter);
  });
  const constantCount = columns.filter(col => col.is_constant).length;
  const missingCount = columns.filter(col => col.null_count > 0).length;
  const timeCount = columns.filter(col => col.roles.some(r => r.role === "temporal")).length;
  return <section className="understanding-content" aria-label="Dataset Understanding">
    <div className="panel-head"><div><h3>데이터 프로파일</h3>
      <p>확인된 통계와 추론된 역할 후보를 구분합니다. 원본은 변경하지 않습니다.</p>
    </div></div>
    {!state && !error && <p role="status">구조와 분석 상태를 확인하는 중입니다.</p>}
    {error && <div role="alert">{error} <button onClick={() => setRevision(n => n + 1)}>다시 조회</button></div>}
    {state && <p className="hint">원본 판본 {state.version.slice(0, 12)} · 분석 상태 {
      state.job_status ?? (profile ? "완료" : "미실행")
    }</p>}
    {state?.job_status === "Failed" && <p role="alert">
      최근 분석에 실패했습니다. Dataset과 이전 완료 결과는 그대로 사용할 수 있습니다.
    </p>}
    {!profile && overviewStatus === "error" && <div role="alert">
      기본 메타데이터 조회 실패: {overviewError}
      <button className="secondary" onClick={onRetryOverview}>기본 정보 다시 조회</button>
    </div>}
    {!profile && overviewStatus === "loading" && <p role="status">기본 메타데이터 조회 중</p>}
    {overview && <>
      <div className="cards">
        <div className="stat"><span>등록된 컬럼</span><strong>{overview.column_count}</strong></div>
        <div className="stat"><span>원본 행 수</span><strong>{
          overview.row_count === null ? "미계산" : overview.row_count.toLocaleString()
        }</strong></div>
      </div>
      <p className="hint">{overview.row_count_basis === "parquet_metadata"
        ? "행 수는 Parquet 메타데이터 기준입니다."
        : "CSV의 원본 행 수는 메타데이터에서 계산하지 않습니다."}
        {" · "}{profile ? "원본 규모와 분석 결과의 범위는 구별합니다." : "분포와 역할 후보는 별도 분석에서 계산됩니다."}</p>
      {!profile && <><h4>기본 스키마</h4>
      <div className="table-scroll"><table><thead><tr>
        <th>컬럼</th><th>자료형</th>
      </tr></thead><tbody>{overview.columns.map(col => <tr key={col.name}>
        <td>{col.name}</td><td>{col.dtype}</td>
      </tr>)}</tbody></table></div></>}
    </>}
    {profile && <>
      <div className="cards">
        <div className="stat"><span>분석한 행</span><strong>{profile.row_count.toLocaleString()}</strong></div>
        <div className="stat"><span>분석 범위</span><strong>{profile.scope === "full" ? "전체" : "Reservoir 표본"}</strong></div>
        <div className="stat"><span>결측 셀</span><strong>{
          profile.columns.reduce((sum, col) => sum + col.null_count, 0).toLocaleString()
        }</strong></div>
      </div>
      <p className="hint">{profile.scope === "full" ? "전체 데이터 계산" :
        "고정 Seed Reservoir 표본 · 전체 분포를 대표한다고 보장하지 않습니다."}
        {" · "}고유값 수 {profile.scope === "full" ? "근사" : "표본 내 정확"}
        {" · "}{profile.algorithm_version}</p>
      <div className="profile-findings" aria-label="주요 데이터 특성">
        <div><strong>{constantCount.toLocaleString()}</strong>
          <span>{profile.scope === "sample" ? "표본 내 상수 후보" : "전체 상수 컬럼"}</span></div>
        <div><strong>{missingCount.toLocaleString()}</strong><span>결측 포함 컬럼</span></div>
        <div><strong>{timeCount.toLocaleString()}</strong><span>시간 역할 후보</span></div>
      </div>
      <div className="panel-head profile-head">
        <div><h4>컬럼 특성</h4><p>검색과 역할 후보 필터로 관심 컬럼을 찾을 수 있습니다.</p></div>
      </div>
      <div className="profile-filters">
        <label>컬럼 검색<input aria-label="프로파일 컬럼 검색" type="search" value={columnSearch}
          onChange={event => setColumnSearch(event.target.value)} placeholder="컬럼 이름" /></label>
        <label>특성 필터<select aria-label="특성 필터" value={columnFilter}
          onChange={event => setColumnFilter(event.target.value)}>
          <option value="all">전체</option>
          <option value="missing">결측 포함</option>
          <option value="constant">상수 후보</option>
          <option value="temporal">시간 후보</option>
          <option value="numeric_measure">수치형 후보</option>
          <option value="categorical">범주형 후보</option>
          <option value="identifier">식별자 후보</option>
          <option value="potentially_sensitive">민감정보 후보</option>
        </select></label>
        <span className="filter-count" role="status">{filteredColumns.length.toLocaleString()} / {columns.length.toLocaleString()} 컬럼</span>
      </div>
      <div className="table-scroll"><table><thead><tr>
        <th>컬럼</th><th>자료형</th><th>NULL</th><th>NaN</th><th>고유값</th><th>역할 후보 (미확정)</th>
      </tr></thead><tbody>{filteredColumns.map(col => <tr key={col.name}>
        <td>{col.name}</td><td>{col.dtype}</td>
        <td>{col.null_ratio === null ? "—" : (col.null_ratio * 100).toFixed(1) + "%"}</td>
        <td>{col.nan_count === null ? "—" : col.nan_count.toLocaleString()}</td>
        <td>{col.distinct_count.toLocaleString()}{col.distinct_exact ? "" : " ≈"}</td>
        <td>{col.is_constant ? "상수 · " : ""}
          {col.roles.map(r => r.role + " (" + r.basis + ")").join(", ") || "미확인"}</td>
      </tr>)}</tbody></table></div>
      {filteredColumns.length === 0 && <p className="muted" role="status">선택한 조건에 해당하는 컬럼이 없습니다.</p>}
      <h4>탐색 후보</h4>
      <p className="hint">데이터 특성과 지원 연산을 기준으로 구성된 항목입니다. 분석 적합성이나 업무적 의미를 확정하지 않습니다.</p>
      {profile.insights.length === 0 && <p className="muted">현재 프로파일로 구성할 수 있는 탐색 후보가 없습니다. 탐색 탭에서 분석 조건을 직접 선택할 수 있습니다.</p>}
      <div className="example-grid">{profile.insights.map((item, i) => <article
        className="example-card" key={item.kind + i}>
        <h4>{item.title}</h4><p>{item.reason}</p>
        <p className="hint">구성 근거: {profile.scope === "full" ? "전체 프로파일" : "Reservoir 표본"}
          {" · "}실행 범위: 별도의 제한된 선두 샘플</p>
        <p className="hint">분석 조건: {item.request.column} · {explorationMetricLabel(item.request.metric)}{
          item.request.group_by ? ` · 그룹 ${item.request.group_by}` : " · 전체 그룹"
        }</p>
        <button onClick={() => onChooseCandidate(item, profile.version, profile.scope)}>
          탐색 조건 보기
        </button>
      </article>)}</div>
    </>}
    {canCreate && <div className="actions">
      <button className="secondary" disabled={busy ||
        state?.job_status === "Queued" || state?.job_status === "Running"}
        onClick={() => void start("sample")}>기본 분석 다시 실행</button>
      <button disabled={busy ||
        state?.job_status === "Queued" || state?.job_status === "Running"}
        onClick={() => void start("full")}>전체 데이터 분석 실행</button>
    </div>}
    {jobError && <p role="alert">{jobError}</p>}
  </section>;
}
