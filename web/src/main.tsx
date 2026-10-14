import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import * as echarts from "echarts";
import { createStep, histogram, needsTimeOrder, type FeatureStep } from "./feature-utils";
import { request, jsonPost } from "./api";
import { ExampleGallery } from "./ExampleGallery";
import { JobsPanel } from "./JobsPanel";
import type { Dataset, Preview, Profile, Job, Example } from "./types";
import "./style.css";

type Step = FeatureStep;
type Tab = "preview" | "profile" | "features" | "jobs";

function readableVersion(d: Dataset) {
  return d.kind === "iceberg" ? `Snapshot ${d.version}` : `SHA256 ${d.version.slice(0, 12)}`;
}
function Chart({ option }: { option: echarts.EChartsCoreOption }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [option]);
  return <div ref={ref} className="chart" role="img" aria-label="선택된 데이터의 분석 차트" />;
}

function Workspace() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [examples, setExamples] = useState<Example[]>([]);
  const [examplesLoading, setExamplesLoading] = useState(true);
  const [previewStatus, setPreviewStatus] = useState<"loading" | "ready" | "error">("loading");
  const [profileStatus, setProfileStatus] = useState<"loading" | "ready" | "error">("loading");
  const [reloadRevision, setReloadRevision] = useState(0);
  const [tab, setTab] = useState<Tab>("preview");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tableName, setTableName] = useState("");
  const [steps, setSteps] = useState<Step[]>([]);
  const [op, setOp] = useState("filter_not_null");
  const [column, setColumn] = useState("");
  const [value, setValue] = useState("");
  const [orderBy, setOrderBy] = useState("");
  const [groupBy, setGroupBy] = useState("");
  const [featurePreview, setFeaturePreview] = useState<Preview | null>(null);
  const dataset = datasets.find(d => d.id === selected);
  const columnNames = preview ? Object.keys(preview.schema) : [];
  const numeric = profile?.columns.filter(c => /Int|Float|Decimal/.test(c.dtype)) ?? [];
  const [visualColumn, setVisualColumn] = useState("");
  const [timeColumn, setTimeColumn] = useState("");

  async function refreshDatasets() {
    const items = await request<Dataset[]>("/api/datasets");
    setDatasets(items);
    setSelected(previous => items.some(d => d.id === previous) ? previous : (items[0]?.id ?? ""));
  }
  async function refreshJobs() {
    const results = await request<Job[]>("/api/jobs");
    setJobs(results);
  }
  useEffect(() => {
    refreshDatasets().catch(e => setError(String(e)));
    refreshJobs().catch(e => setError(String(e)));
    request<Example[]>("/api/examples").then(setExamples)
      .catch(e => setError(String(e)))
      .finally(() => setExamplesLoading(false));
  }, []);
  useEffect(() => {
    if (!selected) {
      setPreview(null); setProfile(null);
      return;
    }
    let active = true;
    setPreview(null); setProfile(null); setFeaturePreview(null); setSteps([]);
    setColumn(""); setVisualColumn(""); setTimeColumn(""); setOrderBy(""); setGroupBy("");
    setPreviewStatus("loading"); setProfileStatus("loading");
    request<Preview>(`/api/datasets/${selected}/preview?limit=100`)
      .then(data => {
        if (!active) return;
        setPreview(data); setPreviewStatus("ready");
        const columns = Object.keys(data.schema);
        setColumn(columns[0] ?? ""); setVisualColumn(columns.find(
          name => /Int|Float|Decimal/.test(data.schema[name])) ?? columns[0] ?? "");
        setTimeColumn(columns[0] ?? "");
      })
      .catch(error => {
        if (!active) return;
        setPreviewStatus("error"); setError(String(error));
      });
    request<Profile>(`/api/datasets/${selected}/profile`)
      .then(data => { if (active) { setProfile(data); setProfileStatus("ready"); } })
      .catch(error => {
        if (!active) return;
        setProfileStatus("error"); setError(String(error));
      });
    return () => { active = false; };
  }, [selected, reloadRevision]);
  useEffect(() => {
    if (jobs.some(j => j.status === "Succeeded" && j.result?.dataset_id &&
      !datasets.some(d => d.id === j.result?.dataset_id))) {
      refreshDatasets().catch(e => setError(String(e)));
    }
  }, [jobs, datasets]);
  useEffect(() => {
    const handle = window.setInterval(() => {
      if (jobs.some(j => !["Succeeded", "Failed", "Cancelled"].includes(j.status))) {
        refreshJobs().catch(e => setError(String(e)));
      }
    }, 2000);
    return () => window.clearInterval(handle);
  }, [jobs]);

  async function withFeedback(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  function addStep() {
    try {
      setSteps(existing => [...existing, createStep(op, column, value)]);
      setFeaturePreview(null); setError("");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  const transform = {
    steps,
    ...(orderBy ? { order_by: orderBy } : {}),
    ...(groupBy ? { group_by: [groupBy] } : {}),
  };
  async function previewFeatures() {
    if (!selected || steps.length === 0) return;
    if (needsTimeOrder(steps) && !orderBy) {
      setError("Lag와 Rolling 변환에는 정렬 컬럼을 지정해야 합니다."); return;
    }
    await withFeedback(async () => {
      const result = await jsonPost<Preview>("/api/features/preview",
        { dataset_id: selected, transform });
      setFeaturePreview(result);
    });
  }
  async function submit(kind: "profile" | "transform") {
    if (!selected) return;
    if (kind === "transform" && needsTimeOrder(steps) && !orderBy) {
      setError("Lag와 Rolling 변환에는 정렬 컬럼을 지정해야 합니다."); return;
    }
    await withFeedback(async () => {
      await jsonPost("/api/jobs",
        { dataset_id: selected, kind, ...(kind === "transform" ? { transform } : {}) });
      await refreshJobs();
      setTab("jobs");
      setNotice("작업이 등록되었습니다. 별도 Worker가 작업을 실행합니다.");
    });
  }
  async function selectExample(exampleId: string) {
    await withFeedback(async () => {
      const data = await jsonPost<Dataset>(`/api/examples/${exampleId}/use`, {});
      await refreshDatasets();
      setSelected(data.id); setTab("preview");
      setNotice("예제가 일반 데이터셋으로 등록됐습니다. EDA와 피처 변환을 시작할 수 있습니다.");
    });
  }
  async function openJobDataset(datasetId: string) {
    await refreshDatasets();
    setSelected(datasetId); setTab("preview");
  }
  async function upload(file: File) {
    await withFeedback(async () => {
      const form = new FormData(); form.append("file", file);
      const data = await request<Dataset>("/api/datasets/files", { method: "POST", body: form });
      await refreshDatasets(); setSelected(data.id); setTab("preview");
      setNotice("파일을 불변 데이터셋으로 등록했습니다.");
    });
  }
  async function connectExisting() {
    await withFeedback(async () => {
      const data = await jsonPost<Dataset>("/api/datasets/iceberg",
        { table_name: tableName });
      await refreshDatasets(); setSelected(data.id); setTab("preview");
      setNotice("기존 Iceberg 테이블의 스냅샷을 연결했습니다.");
    });
  }
  async function importIceberg() {
    if (!selected) return;
    await withFeedback(async () => {
      await jsonPost<Job>("/api/iceberg/import",
        { dataset_id: selected, table_name: tableName });
      await refreshJobs(); setTab("jobs");
      setNotice("Iceberg 적재 Job이 등록되었습니다. 완료 후 데이터셋 목록에서 확인할 수 있습니다.");
    });
  }
  async function cancelJob(id: string) {
    await withFeedback(async () => {
      await jsonPost(`/api/jobs/${id}/cancel`, {});
      await refreshJobs();
    });
  }
  const distribution = useMemo(() => histogram(preview?.rows ?? [], visualColumn),
    [preview, visualColumn]);
  const distributionOption = useMemo<echarts.EChartsCoreOption | null>(() => {
    if (!distribution) return null;
    return {
      backgroundColor: "transparent", textStyle: { color: "#cbd5e1" },
      tooltip: { trigger: "axis" },
      grid: { left: 50, right: 15, top: 20, bottom: 48 },
      xAxis: { type: "category", data: distribution.labels,
        axisLabel: { color: "#94a3b8", rotate: 20, hideOverlap: true } },
      yAxis: { type: "value", axisLabel: { color: "#94a3b8" },
        splitLine: { lineStyle: { color: "#334155" } } },
      series: [{ type: "bar", data: distribution.counts,
        itemStyle: { color: "#7d92e7" } }],
    };
  }, [distribution]);
  const chartOption = useMemo<echarts.EChartsCoreOption | null>(() => {
    if (!preview?.rows.length || !visualColumn) return null;
    const values = preview.rows.map(r => Number(r[visualColumn]))
      .filter(n => Number.isFinite(n));
    if (!values.length) return null;
    return {
      backgroundColor: "transparent",
      textStyle: { color: "#cbd5e1" },
      tooltip: { trigger: "axis" },
      grid: { left: 48, right: 20, top: 25, bottom: 42 },
      xAxis: { type: "category", data: preview.rows.map(r => String(r[timeColumn] ?? "")),
        axisLabel: { color: "#94a3b8", hideOverlap: true } },
      yAxis: { type: "value", axisLabel: { color: "#94a3b8" }, splitLine: {
        lineStyle: { color: "#334155" } } },
      series: [{ type: "line", showSymbol: false, name: visualColumn, data: preview.rows.map(
        r => typeof r[visualColumn] === "number" ? r[visualColumn] : null
      ), lineStyle: { color: "#69b9ff" }, itemStyle: { color: "#69b9ff" } }],
    };
  }, [preview, visualColumn, timeColumn]);

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brandmark">S</div><div>
        <strong>SOS Platform</strong><small>Research · Execute · Evolve</small>
      </div></div>
      <div className="section-title">DATA SOURCES</div>
      <label className="upload" aria-label="CSV 또는 Parquet 파일 업로드">
        + CSV / Parquet 가져오기
        <input type="file" accept=".csv,.parquet" onChange={event => {
          const file = event.target.files?.[0]; if (file) void upload(file);
          event.target.value = "";
        }} />
      </label>
      <div className="connect-existing">
        <label>기존 Iceberg 테이블
          <input aria-label="Iceberg 테이블 이름" value={tableName}
            onChange={e => setTableName(e.target.value)} placeholder="sensor_data" />
        </label>
        <button className="secondary" disabled={busy}
          onClick={() => void connectExisting()}>스냅샷 연결</button>
      </div>
      <button className="job-navigation secondary" onClick={() => setTab("jobs")}>
        전체 Jobs 보기 {jobs.length > 0 ? `· ${jobs.length}` : ""}
      </button>
      <nav aria-label="데이터셋 목록">
        {datasets.length === 0 && <p className="muted">등록된 데이터가 없습니다.</p>}
        {datasets.map(d => <button key={d.id} className={`dataset ${selected === d.id ? "active" : ""}`}
          onClick={() => { setSelected(d.id); setTab("preview"); }}>
          <span className="dataset-name">{d.name}</span>
          <span className="dataset-kind">{d.kind === "iceberg" ? "◆ Iceberg" : "◫ File"}</span>
        </button>)}
      </nav>
      <div className="sidebar-bottom"><a href="/docs" target="_blank" rel="noreferrer">↗ Scalar API Reference</a></div>
    </aside>
    <main className="main">
      <header><div><span className="eyebrow">DATA WORKSPACE</span>
        <h1>데이터를 이해하고, 준비하고, 실행하세요.</h1>
        <p>데이터 버전과 변환 조건을 남겨 동일한 연구를 다시 실행할 수 있습니다.</p></div>
        <span className="environment">Data Workspace · Python Worker</span>
      </header>
      {error && <div role="alert" className="alert error"><strong>작업을 완료하지 못했습니다.</strong> {error}
        <button onClick={() => setError("")} aria-label="오류 닫기">×</button></div>}
      {notice && <div role="status" className="alert success">{notice}
        <button onClick={() => setNotice("")} aria-label="알림 닫기">×</button></div>}
      {!dataset && tab !== "jobs" && <div className="empty">
        <h2>첫 데이터를 탐색해 보세요</h2>
        <p>파일을 준비하지 않아도 합성 데이터로 EDA와 피처 실행을 경험할 수 있습니다.</p>
        <ExampleGallery examples={examples} loading={examplesLoading} busy={busy}
          onUse={id => void selectExample(id)} />
      </div>}
      {dataset && tab !== "jobs" && <>
        <div className="dataset-hero">
          <div><span className="eyebrow">SELECTED DATASET</span><h2>{dataset.name}</h2>
            <p>{dataset.kind.toUpperCase()} · {readableVersion(dataset)}</p></div>
          <div className="hero-actions"><input aria-label="Iceberg 테이블명" value={tableName}
            onChange={e => setTableName(e.target.value)} placeholder="Iceberg table" />
            <button disabled={busy} className="secondary" onClick={() => void importIceberg()}>
              Iceberg에 추가
            </button></div>
        </div>
        <div className="tabbar" role="tablist" aria-label="데이터 작업 영역">
          {(["preview", "profile", "features"] as Tab[]).map(t =>
            <button key={t} role="tab" aria-selected={tab === t}
              className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>
              {{ preview: "Preview", profile: "EDA & Profile", features: "Features", jobs: "Jobs" }[t]}
            </button>)}
        </div>
        {tab === "preview" && <section className="panel">
          <div className="panel-head"><div><h3>데이터 미리보기</h3><p>최대 100행 · 전체 데이터가 아닌 일부입니다.</p></div>
            <span className="chip">{preview?.returned_rows ?? "…"} rows shown</span></div>
          {preview && <><div className="table-scroll"><table><thead><tr>
            {columnNames.map(c => <th key={c}>{c}<small>{preview.schema[c]}</small></th>)}
          </tr></thead><tbody>{preview.rows.map((r, i) => <tr key={i}>
            {columnNames.map(c => <td key={c}>{r[c] == null
              ? <span className="null">NULL</span> : String(r[c])}</td>)}
          </tr>)}</tbody></table></div>{preview.rows.length === 0 && <p className="muted">행이 없는 데이터셋입니다.</p>}</>}
          {previewStatus === "loading" && <p className="muted" role="status">미리보기를 불러오는 중입니다.</p>}
          {previewStatus === "error" && <button className="secondary"
            onClick={() => setReloadRevision(v => v + 1)}>미리보기 다시 시도</button>}
          <div className="hint">스키마와 파일·스냅샷 버전은 실행 입력으로 고정됩니다.</div>
        </section>}
        {tab === "profile" && <section className="panel">
          <div className="panel-head"><div><h3>탐색적 데이터 분석</h3>
            <p>미리보기 통계는 상위 1,000행 기준입니다. 전체 분석은 Job으로 요청할 수 있습니다.</p>
          </div><button disabled={busy} onClick={() => void submit("profile")}>전체 Profile 실행 ↗</button></div>
          {profileStatus === "loading" && <p role="status" className="muted">EDA 통계를 계산하고 있습니다.</p>}
          {profileStatus === "error" && <button className="secondary"
            onClick={() => setReloadRevision(v => v + 1)}>EDA 다시 시도</button>}
          {profile && <>
            <div className="cards">
              <div className="stat"><span>분석한 행</span><strong>{profile.row_count.toLocaleString()}</strong></div>
              <div className="stat"><span>컬럼</span><strong>{profile.columns.length}</strong></div>
              <div className="stat"><span>결측 셀</span><strong>{profile.columns.reduce(
                (s, c) => s + c.nulls, 0).toLocaleString()}</strong></div>
            </div>
            <h4>컬럼 프로파일</h4><div className="table-scroll"><table><thead><tr>
              <th>컬럼</th><th>dtype</th><th>NULL</th><th>NaN</th><th>고유 값</th><th>평균</th>
            </tr></thead><tbody>{profile.columns.map(c => <tr key={c.name}>
              <td>{c.name}</td><td>{c.dtype}</td><td>{c.nulls}</td>
              <td>{c.nan_count ?? "—"}</td><td>{c.unique}</td>
              <td>{typeof c.mean === "number" ? c.mean.toFixed(3) : "—"}</td>
            </tr>)}</tbody></table></div>
            <div className="chart-controls"><h4>시계열 / 순서에 따른 데이터</h4>
              <label>값 <select value={visualColumn} onChange={e => setVisualColumn(e.target.value)}>
                {numeric.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select></label>
              <label>가로축 <select value={timeColumn} onChange={e => setTimeColumn(e.target.value)}>
                {columnNames.map(c => <option key={c} value={c}>{c}</option>)}
              </select></label></div>
            <div className="charts">
              <div><h4>값의 변화</h4>{chartOption ? <Chart option={chartOption} /> :
                <p className="muted">표시할 숫자형 데이터가 없습니다.</p>}</div>
              <div><h4>분포 (Histogram)</h4>{distributionOption ? <Chart option={distributionOption} /> :
                <p className="muted">분포를 계산할 숫자형 데이터가 없습니다.</p>}</div>
            </div>
            <p className="hint">차트와 히스토그램은 최대 100행의 미리보기 값입니다.</p>
          </>}
        </section>}
        {tab === "features" && <section className="panel">
          <div className="panel-head"><div><h3>Feature Builder</h3>
            <p>원본 데이터는 유지하고, 변환 단계를 저장 가능한 작업 설정으로 구성합니다.</p>
          </div></div>
          <div className="form-row">
            <label>변환 <select value={op} onChange={e => setOp(e.target.value)}>
              <option value="filter_not_null">결측 행 제외</option>
              <option value="fill_null">결측값 대체</option>
              <option value="cast">Float64 변환</option>
              <option value="lag">Lag 1</option>
              <option value="rolling_mean">Rolling Mean 3</option>
            </select></label>
            <label>컬럼 <select value={column} onChange={e => setColumn(e.target.value)}>
              {columnNames.map(c => <option key={c} value={c}>{c}</option>)}
            </select></label>
            {op === "fill_null" && <label>대체 값 <input value={value}
              onChange={e => setValue(e.target.value)} placeholder="0" /></label>}
            <button className="secondary" onClick={addStep} disabled={!column}>+ 단계 추가</button>
          </div>
          <div className="form-row">
            <label>시간·정렬 컬럼 <select value={orderBy} onChange={e => setOrderBy(e.target.value)}>
              <option value="">정렬하지 않음</option>
              {columnNames.map(c => <option key={c} value={c}>{c}</option>)}
            </select></label>
            <label>그룹 컬럼 <select value={groupBy} onChange={e => setGroupBy(e.target.value)}>
              <option value="">전체를 하나로 처리</option>
              {columnNames.filter(c => c !== orderBy).map(c => <option key={c} value={c}>{c}</option>)}
            </select></label>
            <p className="hint">Lag / Rolling은 시간 정렬 후 그룹별 계산합니다. Rolling Mean에는 현재 행이 포함됩니다.</p>
          </div>
          <div className="steps">{steps.length === 0 && <p className="muted">아직 변환 단계가 없습니다.</p>}
            {steps.map((step, i) => <div className="step" key={i}>
              <strong>{i + 1}. {step.op}</strong><span>{step.column}</span>
              <button className="ghost" aria-label={`${i + 1}번째 단계 삭제`}
                onClick={() => { setSteps(prev => prev.filter((_, j) => j !== i)); setFeaturePreview(null); }}>삭제</button>
            </div>)}</div>
          <div className="actions"><button disabled={busy || !steps.length} className="secondary"
            onClick={() => void previewFeatures()}>샘플 변환 확인</button>
            <button disabled={busy || !steps.length} onClick={() => void submit("transform")}>
              전체 변환 Job 실행 →
            </button></div>
          {featurePreview && <div className="result"><h4>변환 미리보기 · 샘플 기반</h4>
            <div className="table-scroll"><table><thead><tr>{Object.entries(featurePreview.schema).map(
              ([c, t]) => <th key={c}>{c}<small>{t}</small></th>
            )}</tr></thead><tbody>{featurePreview.rows.slice(0, 15).map((r, i) => <tr key={i}>
              {Object.keys(featurePreview.schema).map(c => <td key={c}>{r[c] == null ? "—" : String(r[c])}</td>)}
            </tr>)}</tbody></table></div></div>}
        </section>}

      </>}
      {tab === "jobs" && <JobsPanel jobs={jobs} busy={busy}
        onRefresh={() => void withFeedback(refreshJobs)}
        onCancel={id => void cancelJob(id)}
        onOpen={id => void openJobDataset(id)} />}
      <footer>Explore · Experiment · Understand · Evolve <span>개발 환경 · 외부 공개 서비스 아님</span></footer>
    </main>
  </div>;
}
createRoot(document.getElementById("root")!).render(<React.StrictMode><Workspace /></React.StrictMode>);
