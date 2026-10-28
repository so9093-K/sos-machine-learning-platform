import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { formatError, request, submitJob } from "./api";
import { jobStatusLabel } from "./display-format";
import { mergeById } from "./collections";
import { FeatureSelector } from "./FeatureSelector";
import type { Dataset, DatasetColumns, Job, JobSummary } from "./types";

const NUMERIC = /^(?:Int(?:32|64)|Float(?:32|64))$/;
const TERMINAL = new Set(["Succeeded", "Failed", "Cancelled"]);
type FeatureRegistrationItem = JobSummary & { retired: boolean };

function usePolledJob() {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!job || TERMINAL.has(job.status)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const item = await request<Job>(`/api/jobs/${encodeURIComponent(job.id)}`);
        if (active) {
          if (item.id !== job.id) throw new Error("조회한 작업과 요청한 작업이 일치하지 않습니다.");
          setJob(item); setError("");
        }
      } catch (exc) {
        if (active) setError(formatError(exc));
      } finally {
        if (active) timer = setTimeout(() => void poll(), 2000);
      }
    };
    timer = setTimeout(() => void poll(), 1000);
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [job?.id, job?.status]);
  return { job, setJob, error, setError };
}

/** Feast는 Feature 정의 정본, SOS는 등록·조회 Job과 불변 Dataset의 정본이다. */
export function FeastFeaturePanel({ datasets, canCreate, onDatasetReady }: {
  datasets: Dataset[];
  canCreate: boolean;
  onDatasetReady: (datasetId: string) => void;
}) {
  const [featureDataset, setFeatureDataset] = useState("");
  const [entityDataset, setEntityDataset] = useState("");
  const [entityColumn, setEntityColumn] = useState("");
  const [sourceTimestamp, setSourceTimestamp] = useState("");
  const [entityTimestamp, setEntityTimestamp] = useState("");
  const [ttlDays, setTtlDays] = useState(30);
  const [columns, setColumns] = useState<DatasetColumns["columns"]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [columnsLoading, setColumnsLoading] = useState(false);
  const [columnsError, setColumnsError] = useState("");
  const [columnsRetry, setColumnsRetry] = useState(0);
  const [entityColumns, setEntityColumns] = useState<DatasetColumns["columns"]>([]);
  const [entityColumnsError, setEntityColumnsError] = useState("");
  const [entityColumnsLoading, setEntityColumnsLoading] = useState(false);
  const [entityColumnsRetry, setEntityColumnsRetry] = useState(0);
  const [listRetry, setListRetry] = useState(0);
  const [registrations, setRegistrations] = useState<FeatureRegistrationItem[]>([]);
  const [retireTarget, setRetireTarget] = useState("");
  const [retireBusy, setRetireBusy] = useState(false);
  const [registrationCursor, setRegistrationCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const retainedPages = useRef(false);
  const [registrationId, setRegistrationId] = useState("");
  const [registerBusy, setRegisterBusy] = useState(false);
  const [retrievalBusy, setRetrievalBusy] = useState(false);
  const [listError, setListError] = useState("");
  const registration = usePolledJob();
  const retrieval = usePolledJob();
  const retirement = usePolledJob();
  const registering = registerBusy || Boolean(registration.job && !TERMINAL.has(registration.job.status));
  const retrieving = retrievalBusy || Boolean(retrieval.job && !TERMINAL.has(retrieval.job.status));
  const retiring = retireBusy || Boolean(retirement.job && !TERMINAL.has(retirement.job.status));
  const numeric = useMemo(() => columns.filter(c => NUMERIC.test(c.dtype) &&
    c.name !== entityColumn && c.name !== sourceTimestamp && c.name !== "sos_observation_id"), [columns, entityColumn, sourceTimestamp]);
  const keys = columns.filter(c => /^(String|Int32|Int64)$/.test(c.dtype));
  const sourceTimes = columns.filter(c => /Datetime.*UTC/.test(c.dtype));
  const entityTimes = entityColumns.filter(c => /Datetime.*UTC/.test(c.dtype));
  const selectedRegistration = registrations.find(item => item.id === registrationId) ??
    (registration.job?.id === registrationId ? registration.job : null);
  const requiredKey = selectedRegistration?.spec.entity_column;
  const missingEntityKey = typeof requiredKey === "string" && entityDataset !== "" &&
    !entityColumnsLoading && !entityColumnsError && !entityColumns.some(column => column.name === requiredKey);
  const datasetName = (id: unknown) => datasets.find(item => item.id === id)?.name ?? "현재 목록 밖의 Dataset";
  const sources = datasets.filter(d => d.kind === "file" && d.name.toLowerCase().endsWith(".parquet"));

  useEffect(() => {
    let active = true;
    setColumns([]); setSelected([]); setEntityColumn(""); setSourceTimestamp(""); setColumnsError("");
    setColumnsLoading(Boolean(featureDataset));
    if (!featureDataset) return () => { active = false; };
    void request<DatasetColumns>(`/api/datasets/${encodeURIComponent(featureDataset)}/columns`)
      .then(value => { if (active) setColumns(value.columns); })
      .catch(exc => { if (active) setColumnsError(formatError(exc)); })
      .finally(() => { if (active) setColumnsLoading(false); });
    return () => { active = false; };
  }, [featureDataset, columnsRetry]);

  useEffect(() => {
    let active = true;
    setEntityColumns([]); setEntityTimestamp(""); setEntityColumnsError("");
    setEntityColumnsLoading(Boolean(entityDataset));
    if (entityDataset) void request<DatasetColumns>(`/api/datasets/${encodeURIComponent(entityDataset)}/columns`)
      .then(value => { if (active) setEntityColumns(value.columns); })
      .catch(exc => { if (active) setEntityColumnsError(formatError(exc)); })
      .finally(() => { if (active) setEntityColumnsLoading(false); });
    return () => { active = false; };
  }, [entityDataset, entityColumnsRetry]);

  useEffect(() => {
    let active = true;
    setListError("");
    void request<{items: FeatureRegistrationItem[]; next_cursor: string | null}>(
      "/api/features/services/registrations?limit=100",
    ).then(page => {
        if (active) {
          setRegistrations(current => mergeById(page.items, current));
          if (!retainedPages.current) setRegistrationCursor(page.next_cursor);
        }
      })
      .catch(exc => { if (active) setListError(formatError(exc)); });
    return () => { active = false; };
  }, [registration.job?.status, retirement.job?.status, listRetry]);

  useEffect(() => {
    if (registration.job?.status === "Succeeded") setRegistrationId(registration.job.id);
  }, [registration.job?.id, registration.job?.status]);

  useEffect(() => {
    if (retirement.job?.status !== "Succeeded") return;
    const retiredId = retirement.job.spec.registration_job_id;
    setRegistrations(current => current.map(item => item.id === retiredId ? { ...item, retired: true } : item));
    if (retiredId === registrationId) setRegistrationId("");
  }, [retirement.job?.status, retirement.job?.id, registrationId]);

  async function loadEarlierRegistrations() {
    if (!registrationCursor || loadingMore) return;
    setLoadingMore(true); setListError("");
    try {
      const page = await request<{items: FeatureRegistrationItem[]; next_cursor: string | null}>(
        `/api/features/services/registrations?limit=100&cursor=${encodeURIComponent(registrationCursor)}`,
      );
      retainedPages.current = true;
      setRegistrations(current => {
        const known = new Set(current.map(item => item.id));
        return [...current, ...page.items.filter(item => !known.has(item.id))];
      });
      setRegistrationCursor(page.next_cursor);
    } catch (exc) {
      setListError(formatError(exc));
    } finally {
      setLoadingMore(false);
    }
  }

  async function retireRegistration() {
    if (!canCreate || !retireTarget || retiring) return;
    setRetireBusy(true); retirement.setError(""); retirement.setJob(null);
    try {
      const item = await submitJob<Job>(
        `/api/features/services/${encodeURIComponent(retireTarget)}/retirement/jobs`, {},
      );
      retirement.setJob(item);
    } catch (exc) { retirement.setError(formatError(exc)); }
    finally { setRetireBusy(false); }
  }

  async function createRegistration() {
    if (!canCreate || !featureDataset || !selected.length || selected.length > 128 || registering || columnsLoading || columnsError ||
        !entityColumn.trim() || !sourceTimestamp.trim() ||
        !Number.isSafeInteger(ttlDays) || ttlDays < 1 || ttlDays > 3650) return;
    setRegisterBusy(true);
    registration.setError("");
    try {
      registration.setJob(await submitJob<Job>("/api/features/services/jobs", {
        feature_dataset_id: featureDataset,
        entity_column: entityColumn.trim(),
        timestamp_column: sourceTimestamp.trim(),
        features: selected,
        ttl_seconds: ttlDays * 86400,
      }));
    } catch (exc) { registration.setError(formatError(exc)); }
    finally { setRegisterBusy(false); }
  }

  async function createRetrieval() {
    if (!canCreate || !registrationId || !entityDataset || !entityTimestamp.trim() ||
        retrieving || missingEntityKey || entityColumnsLoading || entityColumnsError) return;
    setRetrievalBusy(true);
    retrieval.setError(""); retrieval.setJob(null);
    try {
      retrieval.setJob(await submitJob<Job>("/api/features/retrieval/jobs", {
        registration_job_id: registrationId,
        entity_dataset_id: entityDataset,
        timestamp_column: entityTimestamp.trim(),
      }));
    } catch (exc) { retrieval.setError(formatError(exc)); }
    finally { setRetrievalBusy(false); }
  }

  async function cancel(job: Job, setJob: Dispatch<SetStateAction<Job | null>>, setError: (value: string) => void) {
    try {
      const updated = await request<Job>(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST" });
      setJob(previous => previous?.id === job.id ? updated : previous);
    } catch (exc) { setError(formatError(exc)); }
  }

  const published = retrieval.job?.status === "Succeeded" && retrieval.job.result &&
    typeof retrieval.job.result.dataset_id === "string" ? retrieval.job.result.dataset_id : null;

  return <details className="panel feast-feature-panel">
    <summary>등록 Feature로 학습 Dataset 구성 · Feast</summary>
    <p className="hint">재사용할 Feature Service를 먼저 등록한 뒤 조회 시점 Dataset에 연결합니다.
      원본·조회 Dataset에는 UTC 시간형 컬럼이 필요합니다.</p>
    {!canCreate && <p className="hint">조회 전용 계정입니다. Feature 작업 실행 권한이 없습니다.</p>}
    <div className="steps">
      <strong>1. 등록 Feature Service</strong>
      <label>등록된 Feature Service
        <select aria-label="Feast 등록 Feature Service" value={registrationId}
          onChange={e => setRegistrationId(e.target.value)} disabled={retrieving}>
          <option value="">새로 등록하거나 기존 등록을 선택하세요</option>
          {registration.job?.status === "Succeeded" &&
            !(retirement.job?.status === "Succeeded" && retirement.job.spec.registration_job_id === registration.job.id) &&
            !registrations.some(r => r.id === registration.job?.id) &&
            <option value={registration.job.id}>방금 등록한 Feature · {registration.job.id.slice(0,8)}</option>}
          {registrations.filter(j => j.status === "Succeeded" && !j.retired &&
            typeof j.result?.feature_service === "string").map(j => <option key={j.id} value={j.id}>
            {datasetName(j.dataset_id)} · {Array.isArray(j.spec.features) ? j.spec.features.length + "개 Feature" : "등록 Feature"} · {new Date(j.created_at).toLocaleDateString("ko-KR")}
          </option>)}
        </select>
      </label>
      {registrationCursor && <button type="button" className="secondary"
        disabled={loadingMore} onClick={() => void loadEarlierRegistrations()}>
        {loadingMore ? "이전 등록을 조회하는 중…" : "이전 Feature Service 더 보기"}
      </button>}
      <details>
        <summary>Feature Service 폐기·등록 잔여물 정리</summary>
        <p className="hint">완료·실패·취소된 등록을 폐기할 수 있습니다. 과거 조회나 다른
          활성 등록이 사용 중인 정의는 Registry에 보존되며 신규 조회에는 사용할 수 없습니다.</p>
        <label>정리할 등록
          <select value={retireTarget} onChange={e => setRetireTarget(e.target.value)}
            disabled={!canCreate || retiring} aria-label="Feast 폐기 대상 등록">
            <option value="">등록 기록 선택</option>
            {registrations.filter(r => !r.retired && TERMINAL.has(r.status)).map(r =>
              <option key={r.id} value={r.id}>{datasetName(r.dataset_id)} · {jobStatusLabel(r.status)} · {r.id.slice(0,8)}</option>)}
            {registration.job && !registrations.some(r => r.id === registration.job?.id) &&
              TERMINAL.has(registration.job.status) &&
              <option value={registration.job.id}>{registration.job.id.slice(0,8)} · 최근 등록</option>}
          </select>
        </label>
        <button type="button" disabled={!canCreate || !retireTarget || retiring}
          className="secondary" onClick={() => void retireRegistration()}>
          {retireBusy ? "폐기 접수 중…" : "등록 폐기 Job 제출"}
        </button>
        {retirement.job && <div role="status">
          <strong>폐기 · {jobStatusLabel(retirement.job.status)}</strong>
          {retirement.job.status === "Failed" && <p role="alert">{retirement.job.error ?? "폐기 실패"}</p>}
          {retirement.job.status === "Succeeded" &&
            <p className="hint">{retirement.job.result?.registry_deleted
              ? "Feast Registry에서도 참조 없는 정의를 정리했습니다."
              : "등록은 폐기됐으며 사용 중인 과거 정의는 Registry에 보존했습니다."}</p>}
        </div>}
        {retirement.error && <p role="alert">{retirement.error}</p>}
      </details>
      <details>
        <summary>새 Feature Service 정의·등록</summary>
        <div className="form-row">
          <label>Feature Source Dataset
            <select aria-label="Feast Feature Source Dataset" value={featureDataset}
              onChange={e => setFeatureDataset(e.target.value)} disabled={!canCreate || registering}>
              <option value="">Parquet Dataset 선택</option>
              {sources.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </label>
          <label>연결 대상 식별 컬럼
            <select value={entityColumn} onChange={event => {
              setEntityColumn(event.target.value); setSelected(current => current.filter(name => name !== event.target.value));
            }} disabled={!canCreate || registering || columnsLoading} aria-label="Feast Source Entity 키">
              <option value="">식별 컬럼 선택</option>
              {keys.map(column => <option key={column.name} value={column.name}>{column.name} · {column.dtype}</option>)}
            </select>
          </label>
          <label>원본 UTC 시각 컬럼
            <select value={sourceTimestamp} onChange={event => setSourceTimestamp(event.target.value)}
              disabled={!canCreate || registering || columnsLoading} aria-label="Feast Source UTC 시각">
              <option value="">UTC 시간형 컬럼 선택</option>
              {sourceTimes.map(column => <option key={column.name} value={column.name}>{column.name}</option>)}
            </select>
          </label>
          <label>Feature TTL (일)
            <input type="number" min={1} max={3650} step={1} value={ttlDays}
              onChange={e => setTtlDays(Number(e.target.value))}
              disabled={!canCreate || registering} aria-label="Feast Source TTL" />
          </label>
        </div>
        {columnsLoading && <p role="status">원본 컬럼 조회 중…</p>}
        {columnsError && <div role="alert">{columnsError}<button type="button" className="secondary"
          onClick={() => setColumnsRetry(value => value + 1)}>원본 컬럼 다시 조회</button></div>}
        {featureDataset && !columnsLoading && !columnsError && sourceTimes.length === 0 &&
          <p className="hint">UTC 시간형 컬럼이 없습니다. UTC Datetime 컬럼을 포함한 Parquet Dataset이 필요합니다.</p>}
        {selected.length > 128 && <p role="alert">한 번에 등록할 Feature는 최대 128개입니다. 현재 {selected.length}개를 선택했습니다.</p>}
        {featureDataset && !columnsLoading && !columnsError && <div className="steps">
          <strong>숫자형 Feature · {selected.length}개</strong>
          <FeatureSelector columns={numeric} selected={selected} disabled={!canCreate || registering}
            onChange={setSelected} />
          {numeric.length === 0 && <p className="hint">등록 가능한 숫자형 Feature가 없습니다.</p>}
        </div>}
        <div className="actions">
          <button type="button" disabled={!canCreate || !featureDataset || !selected.length ||
            !entityColumn.trim() || !sourceTimestamp.trim() || registering || columnsLoading || Boolean(columnsError) || selected.length > 128 ||
            !Number.isInteger(ttlDays) || ttlDays < 1 || ttlDays > 3650}
            onClick={() => void createRegistration()}>
            {registerBusy ? "등록 접수 중…" : "Feature Service 등록 Job 제출"}
          </button>
        </div>
        {registration.job && <div role="status">
          <strong>등록 · {jobStatusLabel(registration.job.status)}</strong>
          <p className="hint">원본 Dataset · {datasetName(registration.job.dataset_id)}</p>
          {registration.job.status === "Failed" && <p role="alert">{registration.job.error ?? "등록 실패"}</p>}
          {canCreate && ["Queued","Running"].includes(registration.job.status) &&
            !registration.job.cancel_requested &&
            <button type="button" className="secondary"
              onClick={() => void cancel(registration.job!, registration.setJob, registration.setError)}>
              취소 요청
            </button>}
        </div>}
        {registration.error && <p role="alert">{registration.error}</p>}
      </details>
    </div>
    <div className="steps">
      <strong>2. 등록 Feature 조회</strong>
      <div className="form-row">
        <label>조회 시점 Dataset
          <select aria-label="Feast 조회 시점 Dataset" value={entityDataset}
            onChange={e => setEntityDataset(e.target.value)} disabled={!canCreate || retrieving}>
            <option value="">Dataset 선택</option>
            {datasets.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select>
        </label>
        <label>조회 시점 UTC 시간 컬럼
          <select value={entityTimestamp} onChange={e => setEntityTimestamp(e.target.value)}
            disabled={!canCreate || retrieving || entityColumnsLoading} aria-label="Feast 조회 시점 UTC 컬럼">
            <option value="">UTC 시간형 컬럼 선택</option>
            {entityTimes.map(column => <option key={column.name} value={column.name}>{column.name}</option>)}
          </select>
        </label>
      </div>
      {entityColumnsLoading && <p role="status">조회 Dataset 컬럼 확인 중…</p>}
      {entityColumnsError && <div role="alert">{entityColumnsError}<button type="button" className="secondary"
        onClick={() => setEntityColumnsRetry(value => value + 1)}>조회 컬럼 다시 확인</button></div>}
      {entityDataset && !entityColumnsLoading && !entityColumnsError && entityTimes.length === 0 &&
        <p className="hint">조회 Dataset에 UTC 시간형 컬럼이 없습니다.</p>}
      {missingEntityKey && <p role="alert">등록 서비스의 식별 컬럼 {String(requiredKey)}이 조회 Dataset에 없습니다.</p>}
      <p className="hint">조회 시점 Dataset에는 등록한 서비스의 동일 Entity 키와 UTC 시각이
        필요합니다. 원본 Feature 계산과 모델 학습은 자동 실행하지 않습니다.</p>
      <div className="actions">
        <button type="button" disabled={!canCreate || !registrationId || !entityDataset ||
          !entityTimestamp.trim() || retrieving || missingEntityKey || entityColumnsLoading || Boolean(entityColumnsError)} onClick={() => void createRetrieval()}>
          {retrievalBusy ? "접수 중…" : "Point-in-Time Feature 조회 Job 제출"}
        </button>
      </div>
      {retrieval.job && <div role="status">
        <strong>조회 · {jobStatusLabel(retrieval.job.status)}</strong>
        <p className="hint">조회 Dataset · {datasetName(retrieval.job.spec.entity_dataset_id)}</p>
        {retrieval.job.status === "Failed" &&
          <p role="alert">{retrieval.job.error ?? "조회에 실패했습니다."}</p>}
        {canCreate && ["Queued","Running"].includes(retrieval.job.status) &&
          !retrieval.job.cancel_requested &&
          <button type="button" className="secondary"
            onClick={() => void cancel(retrieval.job!, retrieval.setJob, retrieval.setError)}>
            취소 요청
          </button>}
        {published && <button type="button" onClick={() => onDatasetReady(published)}>
          게시된 Feature Dataset으로 학습 준비
        </button>}
      </div>}
      {retrieval.error && <p role="alert">{retrieval.error}</p>}
    </div>
    {listError && <div role="alert">등록 목록 조회 실패 · {listError}
      <button type="button" className="secondary" onClick={() => setListRetry(value => value + 1)}>등록 목록 다시 조회</button></div>}
  </details>;
}
