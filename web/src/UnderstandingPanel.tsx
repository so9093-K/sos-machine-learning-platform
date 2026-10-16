/** Dataset 판본의 실제 Profile과 실행 가능한 탐색을 공통 API로 표시한다. */
import { useEffect, useState } from "react";
import { formatError, jsonPost, request, submitJob } from "./api";
import type { Exploration, ExplorationRequest, Job } from "./types";

import type { components } from "./api.generated";

type Understanding = components["schemas"]["UnderstandingOut"];

export function UnderstandingPanel({ datasetId, canCreate }: {
  datasetId: string; canCreate: boolean;
}) {
  const [state, setState] = useState<Understanding | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Exploration | null>(null);
  const [jobError, setJobError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
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

  async function explore(spec: ExplorationRequest) {
    setBusy(true); setJobError(""); setResult(null);
    try {
      const answer = await jsonPost<Exploration>(
        "/api/datasets/" + encodeURIComponent(datasetId) + "/explorations", spec,
      );
      setResult(answer);
    } catch (cause) { setJobError(formatError(cause)); }
    finally { setBusy(false); }
  }

  const profile = state?.profile;
  return <section className="panel" aria-label="Dataset Understanding">
    <div className="panel-head"><div><h3>데이터 이해</h3>
      <p>확인된 통계와 추론된 역할 후보를 구분합니다. 원본은 변경하지 않습니다.</p>
    </div></div>
    {!state && !error && <p role="status">구조와 분석 상태를 확인하는 중입니다.</p>}
    {error && <div role="alert">{error} <button onClick={() => setRevision(n => n + 1)}>다시 조회</button></div>}
    {state && <p className="hint">원본 판본 {state.version.slice(0, 12)} · 분석 상태 {
      state.job_status ?? (profile ? "완료" : "미실행")
    }</p>}
    {state?.job_status === "Failed" && <p role="alert">
      기본 분석에 실패했습니다. Dataset은 그대로 사용할 수 있습니다.
    </p>}
    {profile && <>
      <div className="cards">
        <div className="stat"><span>분석한 행</span><strong>{profile.row_count.toLocaleString()}</strong></div>
        <div className="stat"><span>컬럼</span><strong>{profile.columns.length}</strong></div>
        <div className="stat"><span>결측 셀</span><strong>{
          profile.columns.reduce((sum, col) => sum + col.null_count, 0).toLocaleString()
        }</strong></div>
      </div>
      <p className="hint">{profile.scope === "full" ? "전체 데이터 계산" :
        "고정 Seed Reservoir 표본 · 전체 분포를 대표한다고 보장하지 않습니다."}
        {" · "}고유값 수 {profile.scope === "full" ? "근사" : "표본 내 정확"}
        {" · "}{profile.algorithm_version}</p>
      <h4>발견된 컬럼 특성</h4>
      <div className="table-scroll"><table><thead><tr>
        <th>컬럼</th><th>자료형</th><th>NULL</th><th>NaN</th><th>고유값</th><th>역할 후보 (미확정)</th>
      </tr></thead><tbody>{profile.columns.map(col => <tr key={col.name}>
        <td>{col.name}</td><td>{col.dtype}</td>
        <td>{col.null_ratio === null ? "—" : (col.null_ratio * 100).toFixed(1) + "%"}</td>
        <td>{col.nan_count === null ? "—" : col.nan_count.toLocaleString()}</td>
        <td>{col.distinct_count.toLocaleString()}{col.distinct_exact ? "" : " ≈"}</td>
        <td>{col.is_constant ? "상수 · " : ""}
          {col.roles.map(r => r.role + " (" + r.basis + ")").join(", ") || "미확인"}</td>
      </tr>)}</tbody></table></div>
      <h4>시작할 수 있는 탐색</h4>
      {profile.insights.length === 0 && <p className="muted">현재 확인된 정보만으로 실행 가능한 분석을 제안하지 않습니다. Preview에서 구조를 확인하세요.</p>}
      <div className="example-grid">{profile.insights.map((item, i) => <article
        className="example-card" key={item.kind + i}>
        <h4>{item.title}</h4><p>{item.reason}</p>
        <p className="hint">추천 근거: {profile.scope === "full" ? "전체 입력" : "Reservoir 표본"}
          {" · "}실행: 선두 최대 10,000행 기반의 별도 샘플 · 선택 후 실행</p>
        <button disabled={busy} onClick={() => void explore(item.request)}>탐색 실행</button>
      </article>)}</div>
      {result && <div className="result" role="status">
        <h4>탐색 결과 · {result.scope === "sample" ? "샘플" : "전체"}</h4>
        <p className="hint">입력 판본 {result.version.slice(0, 12)} · 표시 그룹 제한 {result.truncated ? "있음" : "없음"}</p>
        <div className="table-scroll"><table><thead><tr><th>그룹</th><th>값</th></tr></thead>
          <tbody>{result.rows.map((row, i) => <tr key={i}>
            <td>{row.category === null ? "전체/NULL" : String(row.category)}</td>
            <td>{row.value === null ? "계산 불가" : String(row.value)}</td>
          </tr>)}</tbody></table></div>
      </div>}
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
