import { useEffect, useRef, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { jobStatusLabel } from "./display-format";
import type { Dataset, Job, PublicSource } from "./types";

type PartState = { job: Job | null; error: string };
const TERMINAL = new Set(["Succeeded", "Failed", "Cancelled"]);

const mebibytes = (size: number) => Math.round(size / (1024 * 1024));

/** 가져오기 결과의 출처·준비 근거를 사용자가 읽는 문장으로 요약한다. */
function importSummary(result: Record<string, unknown> | null | undefined): string {
  if (!result) return "";
  const rows = typeof result.rows === "number" ? result.rows.toLocaleString("ko-KR") + "행" : "";
  const excluded = Object.values((result.excluded_attempted ?? {}) as Record<string, number>)
    .reduce((sum, value) => sum + value, 0);
  const missing = Object.values((result.nonfinite_to_missing ?? {}) as Record<string, number>)
    .reduce((sum, value) => sum + value, 0);
  return [rows, excluded ? `공격 시도 ${excluded.toLocaleString("ko-KR")}행 제외` : "",
    missing ? `무한값 ${missing.toLocaleString("ko-KR")}개 결측` : ""].filter(Boolean).join(" · ");
}

/** 검증한 공개 데이터의 부분을 이용 조건 확인 후 Dataset으로 가져온다.
 *
 *  임의 URL을 받지 않으며, 가져오기는 명시적 Job이다. 가져온 뒤에는 Dataset을 선택만 하고
 *  분석은 자동으로 시작하지 않는다.
 */
export function PublicDataImport({ topicId, onImported }: {
  /** 지정하면 이 주제의 원본만 보인다. */
  topicId?: string;
  onImported: (dataset: Dataset) => void;
}) {
  const [sources, setSources] = useState<PublicSource[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [retry, setRetry] = useState(0);
  const [accepted, setAccepted] = useState<Record<string, string>>({});
  const [parts, setParts] = useState<Record<string, PartState>>({});
  const imported = useRef(new Set<string>());
  const importedHandler = useRef(onImported);
  importedHandler.current = onImported;
  useEffect(() => {
    let active = true;
    setLoadError("");
    void request<PublicSource[]>("/api/public-datasets")
      .then(items => { if (active) setSources(items); })
      .catch(cause => { if (active) setLoadError(formatError(cause)); });
    return () => { active = false; };
  }, [retry]);
  // 진행 중인 가져오기만 상태를 다시 확인한다. 완료되면 결과 Dataset을 한 번만 알린다.
  const running = Object.entries(parts).filter(([, state]) => state.job && !TERMINAL.has(state.job.status));
  useEffect(() => {
    if (!running.length) return;
    const timer = window.setTimeout(() => {
      for (const [key, state] of running) {
        void request<Job>(`/api/jobs/${encodeURIComponent(state.job!.id)}`)
          .then(job => setParts(current => ({ ...current, [key]: { job, error: "" } })))
          .catch(cause => setParts(current => ({ ...current, [key]: { ...current[key], error: formatError(cause) } })));
      }
    }, 2500);
    return () => window.clearTimeout(timer);
  }, [parts]);
  useEffect(() => {
    for (const state of Object.values(parts)) {
      const datasetId = state.job?.status === "Succeeded" ? state.job.result?.dataset_id : null;
      if (typeof datasetId !== "string" || imported.current.has(datasetId)) continue;
      imported.current.add(datasetId);
      void request<Dataset>(`/api/datasets/${encodeURIComponent(datasetId)}`)
        .then(dataset => importedHandler.current(dataset))
        .catch(() => imported.current.delete(datasetId));
    }
  }, [parts]);

  async function start(source: PublicSource, partId: string) {
    const key = `${source.id}:${partId}`;
    setParts(current => ({ ...current, [key]: { job: null, error: "" } }));
    try {
      const job = await submitJob<Job>(`/api/public-datasets/${encodeURIComponent(source.id)}/imports`,
        { part: partId, accept_terms_version: accepted[source.id] });
      setParts(current => ({ ...current, [key]: { job, error: "" } }));
    } catch (cause) {
      setParts(current => ({ ...current, [key]: { job: null, error: formatError(cause) } }));
    }
  }

  const shown = (sources ?? []).filter(source => !topicId || source.topic === topicId);
  if (loadError) return <div role="alert" className="guide-state">
    <p>공개 데이터 목록을 불러오지 못했습니다. {loadError}</p>
    <button type="button" className="secondary" onClick={() => setRetry(value => value + 1)}>다시 조회</button>
  </div>;
  if (sources === null) return <p role="status" className="muted">공개 데이터 목록을 확인하는 중입니다.</p>;
  if (!shown.length) return <p className="muted">가져올 수 있는 공개 데이터가 없습니다.</p>;
  return <div className="public-data-import">
    {shown.map(source => {
      const consent = accepted[source.id] === source.terms_version;
      return <section key={source.id} className="public-source" aria-label={source.title}>
        <strong>{source.title}</strong>
        <p className="hint">{source.summary}</p>
        <p className="hint">원본 {mebibytes(source.size_bytes)}MB · 받은 원본은 검증한 SHA-256과 같을 때만 쓰며,
          처음 가져올 때 원본을 받으므로 시간이 걸립니다. 결과는 출처가 기록된 일반 Dataset입니다.</p>
        <details className="input-fix-more">
          <summary>이용 조건·인용 (판본 {source.terms_version})</summary>
          <p>{source.terms}</p>
          <ul className="input-fix-list">{source.citations.map(item => <li key={item}>{item}</li>)}</ul>
          <p className="hint">원본 위치: <code>{source.url}</code></p>
        </details>
        <label className="public-source-consent">
          <input type="checkbox" checked={consent}
            onChange={event => setAccepted(current => ({ ...current,
              [source.id]: event.target.checked ? source.terms_version : "" }))} />
          이용 조건과 인용 요구 확인
        </label>
        <ul className="public-source-parts">{source.parts.map(part => {
          const key = `${source.id}:${part.id}`;
          const state = parts[key];
          const job = state?.job ?? null;
          const busy = Boolean(state && !state.error && (!job || !TERMINAL.has(job.status)));
          return <li key={part.id}>
            <div>
              <strong>{part.title}</strong>
              <span className="chip">{part.role === "train" ? "학습·정상 기준" : "평가 · 정답 레이블"}</span>
              <p className="hint">{part.description}</p>
              {job && <p className="hint" role="status">{job.status === "Succeeded"
                ? `가져옴 · ${importSummary(job.result as Record<string, unknown>)}`
                : job.status === "Failed"
                  ? `가져오기 실패 · ${job.error ?? ""}${job.error_code ? ` (${job.error_code})` : ""}`
                  : `가져오기 ${jobStatusLabel(job.status)}`}</p>}
              {state?.error && <p role="alert">{state.error}</p>}
            </div>
            <button type="button" className="secondary" disabled={!consent || busy}
              onClick={() => void start(source, part.id)}>
              {busy ? "가져오는 중…" : job?.status === "Succeeded" ? "다시 가져오기" : "가져오기"}</button>
          </li>;
        })}</ul>
      </section>;
    })}
  </div>;
}
