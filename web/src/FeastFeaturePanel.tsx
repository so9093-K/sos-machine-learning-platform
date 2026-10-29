import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { formatError, request, submitJob } from "./api";
import { jobStatusLabel } from "./display-format";
import { mergeById } from "./collections";
import { FeatureSelector } from "./FeatureSelector";
import type { Dataset, DatasetColumns, FeatureRegistrationPage, FeatureRegistrationSummary, Job, JobSummary } from "./types";

const NUMERIC = /^(?:Int(?:32|64)|Float(?:32|64))$/;
const TERMINAL = new Set(["Succeeded", "Failed", "Cancelled"]);

/** 폐기 실패는 외부 변경 미반영을 보장하지 않는다. 상태 확인 전에는 조회를 재허용하지 않는다. */
export function featureRetrievalBlockReason(registrationId: string, retired: boolean,
  retirement: Pick<Job, "spec" | "status"> | null, blocked = false): string | null {
  if (!registrationId) return null;
  if (retired) return "선택한 등록은 폐기돼 신규 조회에 사용할 수 없습니다. 다른 등록을 선택하세요.";
  if (retirement?.spec.registration_job_id !== registrationId || retirement.status === "Cancelled")
    return blocked ? "폐기 처리로 신규 조회가 차단된 등록입니다. 작업 이력에서 폐기 상태를 확인하고, 실패했다면 폐기를 다시 요청하세요." : null;
  return retirement.status === "Failed"
    ? "등록 폐기에 실패해 처리 상태 확인이 필요합니다. Registry 변경이 일부 반영됐을 수 있어 신규 조회를 중단했습니다. 폐기 상태를 확인하거나 폐기를 다시 요청하세요."
    : "선택한 등록의 폐기가 접수돼 신규 조회에 사용할 수 없습니다. 다른 등록을 선택하세요.";
}

/** 표시 이름은 파일 형식의 근거가 아니다. Worker와 같은 불변 위치 조건을 사용한다. */
export function isFeastSource(dataset: Dataset): boolean {
  return dataset.kind === "file" && dataset.location.endsWith(".parquet");
}

export function entityKeyIssue(key: string, source: DatasetColumns["columns"], target: DatasetColumns["columns"]): string {
  const expected = source.find(column => column.name === key);
  const actual = target.find(column => column.name === key);
  if (!expected) return `등록 원본에서 식별 컬럼 ${key}의 자료형을 확인할 수 없습니다.`;
  if (!actual) return `조회 Dataset에 식별 컬럼 ${key}이 없습니다.`;
  return expected.dtype === actual.dtype ? "" : `식별 컬럼 ${key}의 자료형 불일치 · 등록 원본 ${expected.dtype} / 조회 Dataset ${actual.dtype}`;
}

/** 성공한 등록의 기록을 보여주며 현재 Registry의 연결·무결성으로 확대 해석하지 않는다. */
export function RegisteredFeatureSummary({ registration, sourceName }: {
  registration: Pick<JobSummary, "id" | "spec" | "result">; sourceName: string;
}) {
  const definition = registration.result;
  const features = definition?.feature_names ?? registration.spec.features;
  const names = Array.isArray(features) ? features.filter((name): name is string => typeof name === "string") : [];
  const ttl = definition?.ttl_seconds ?? registration.spec.ttl_seconds;
  const ttlLabel = typeof ttl === "number" && Number.isFinite(ttl) && ttl > 0
    ? ttl % 86400 === 0 ? `${ttl / 86400}일` : `${ttl.toLocaleString("ko-KR")}초` : "기록 없음";
  const text = (value: unknown) => typeof value === "string" && value ? value : "기록 없음";
  return <section className="registered-feature-summary" aria-label="선택한 Feature Service">
    <h4>선택한 Feature Service</h4>
    <dl className="evidence-facts evidence-facts-compact">
      <div><dt>원본 Dataset</dt><dd>{sourceName}</dd></div>
      <div><dt>식별 컬럼</dt><dd>{text(definition?.entity_column ?? registration.spec.entity_column)}</dd></div>
      <div><dt>원본 UTC 시각</dt><dd>{text(definition?.timestamp_column ?? registration.spec.timestamp_column)}</dd></div>
      <div><dt>Feature</dt><dd>{Array.isArray(features) ? `${names.length}개` : "기록 없음"}</dd></div>
      <div><dt>과거 조회 기간</dt><dd>{ttlLabel}</dd></div>
    </dl>
    <p className="hint">등록 완료 기록입니다. 현재 Registry의 정의·연결 상태는 조회 실행 시 검증합니다.</p>
    <details><summary>Feature 목록·등록 근거</summary>
      <p>{names.join(" · ") || "기록 없음"}</p>
      <dl className="evidence-facts"><div><dt>Feature Service</dt><dd>{text(definition?.feature_service)}</dd></div>
        <div><dt>등록 Job</dt><dd>{registration.id}</dd></div></dl>
    </details>
  </section>;
}

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

const purposeText = {
  training: { summary: "등록 Feature로 학습 Dataset 구성 · Feast", ready: "게시된 Feature Dataset으로 학습 준비" },
  preparation: { summary: "등록 Feature 조회 · Feast", ready: "게시된 Feature Dataset 열기" },
} as const;

/** Feast는 Feature 정의 정본, SOS는 등록·조회 Job과 불변 Dataset의 정본이다.
 *  조회는 Job 실행 권한, 등록·폐기는 Feature 관리 권한으로 구분한다. */
export function FeastFeaturePanel({ datasets, canCreate, canManage = false, purpose = "training",
  onDatasetReady }: {
  datasets: Dataset[];
  canCreate: boolean;
  canManage?: boolean;
  purpose?: keyof typeof purposeText;
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
  const [entitySchemaId, setEntitySchemaId] = useState("");
  const [entityColumnsError, setEntityColumnsError] = useState("");
  const [entityColumnsLoading, setEntityColumnsLoading] = useState(false);
  const [entityColumnsRetry, setEntityColumnsRetry] = useState(0);
  const [listRetry, setListRetry] = useState(0);
  const [registrations, setRegistrations] = useState<FeatureRegistrationSummary[]>([]);
  const [retireTarget, setRetireTarget] = useState("");
  const [retireConfirmation, setRetireConfirmation] = useState<string | null>(null);
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
  const requiredKey = selectedRegistration?.result?.entity_column ?? selectedRegistration?.spec.entity_column;
  const registeredSourceId = selectedRegistration?.dataset_id ?? "";
  const [serviceSchema, setServiceSchema] = useState<{ id: string; columns: DatasetColumns["columns"]; error: string } | null>(null);
  const [serviceSchemaRetry, setServiceSchemaRetry] = useState(0);
  const currentServiceSchema = serviceSchema?.id === registeredSourceId ? serviceSchema : null;
  const retirementBlockReason = featureRetrievalBlockReason(registrationId,
    Boolean(selectedRegistration && "retired" in selectedRegistration && selectedRegistration.retired), retirement.job,
    Boolean(selectedRegistration && "blocked" in selectedRegistration && selectedRegistration.blocked));
  const retirementBlocksSelection = Boolean(retirementBlockReason || (retireBusy && retireConfirmation === registrationId));
  const serviceSchemaLoading = Boolean(registeredSourceId && !currentServiceSchema);
  const keyIssue = typeof requiredKey === "string" && entityDataset && currentServiceSchema &&
    !currentServiceSchema.error && !entityColumnsLoading && !entityColumnsError
    ? entityKeyIssue(requiredKey, currentServiceSchema.columns, entityColumns) : "";
  const datasetName = (id: unknown) => datasets.find(item => item.id === id)?.name ?? "현재 목록 밖의 Dataset";
  const sources = datasets.filter(isFeastSource);

  useEffect(() => {
    let active = true;
    setServiceSchema(null);
    if (registeredSourceId) void request<DatasetColumns>(`/api/datasets/${encodeURIComponent(registeredSourceId)}/columns`)
      .then(value => { if (active) setServiceSchema({ id: registeredSourceId, columns: value.columns, error: "" }); })
      .catch(error => { if (active) setServiceSchema({ id: registeredSourceId, columns: [], error: formatError(error) }); });
    return () => { active = false; };
  }, [registeredSourceId, serviceSchemaRetry]);

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
    setEntityColumns([]); setEntitySchemaId(""); setEntityTimestamp(""); setEntityColumnsError("");
    setEntityColumnsLoading(Boolean(entityDataset));
    if (entityDataset) void request<DatasetColumns>(`/api/datasets/${encodeURIComponent(entityDataset)}/columns`)
      .then(value => { if (active) { setEntityColumns(value.columns); setEntitySchemaId(entityDataset); } })
      .catch(exc => { if (active) setEntityColumnsError(formatError(exc)); })
      .finally(() => { if (active) setEntityColumnsLoading(false); });
    return () => { active = false; };
  }, [entityDataset, entityColumnsRetry]);

  useEffect(() => {
    let active = true;
    setListError("");
    void request<FeatureRegistrationPage>(
      "/api/features/services/registrations?limit=100",
    ).then(page => {
        if (active) {
          setRegistrations(current => mergeById(page.items, current));
          if (!retainedPages.current) setRegistrationCursor(page.next_cursor ?? null);
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
    setRegistrations(current => current.map(item => item.id === retiredId ? { ...item, retired: true, blocked: true } : item));
    if (retiredId === registrationId) setRegistrationId("");
  }, [retirement.job?.status, retirement.job?.id, registrationId]);

  async function loadEarlierRegistrations() {
    if (!registrationCursor || loadingMore) return;
    setLoadingMore(true); setListError("");
    try {
      const page = await request<FeatureRegistrationPage>(
        `/api/features/services/registrations?limit=100&cursor=${encodeURIComponent(registrationCursor)}`,
      );
      retainedPages.current = true;
      setRegistrations(current => {
        const known = new Set(current.map(item => item.id));
        return [...current, ...page.items.filter(item => !known.has(item.id))];
      });
      setRegistrationCursor(page.next_cursor ?? null);
    } catch (exc) {
      setListError(formatError(exc));
    } finally {
      setLoadingMore(false);
    }
  }

  async function retireRegistration() {
    if (!canManage || !retireConfirmation || retireConfirmation !== retireTarget || retiring) return;
    setRetireBusy(true); retirement.setError("");
    try {
      const item = await submitJob<Job>(
        `/api/features/services/${encodeURIComponent(retireConfirmation)}/retirement/jobs`, {},
      );
      retirement.setJob(item);
      setRetireConfirmation(null);
    } catch (exc) { retirement.setError(formatError(exc)); }
    finally { setRetireBusy(false); }
  }

  async function createRegistration() {
    if (!canManage || !featureDataset || !selected.length || selected.length > 128 || registering || columnsLoading || columnsError ||
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
        !selectedRegistration || retirementBlocksSelection || entitySchemaId !== entityDataset || typeof requiredKey !== "string" || !currentServiceSchema || currentServiceSchema.error ||
        retrieving || keyIssue || entityColumnsLoading || entityColumnsError) return;
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
    <summary>{purposeText[purpose].summary}</summary>
    <p className="hint">등록된 Feature Service를 선택해 조회 시점 Dataset에 연결합니다.
      원본·조회 Dataset에는 UTC 시간형 컬럼이 필요합니다.</p>
    {!canCreate && <p className="hint">조회 전용 계정입니다. Feature 작업 실행 권한이 없습니다.</p>}
    <div className="steps">
      <strong>1. 등록 Feature Service</strong>
      <label>등록된 Feature Service
        <select aria-label="Feast 등록 Feature Service" value={registrationId}
          onChange={e => setRegistrationId(e.target.value)} disabled={retrieving}>
          <option value="">등록된 Feature Service 선택</option>
          {registration.job?.status === "Succeeded" &&
            !(retirement.job?.status === "Succeeded" && retirement.job.spec.registration_job_id === registration.job.id) &&
            !registrations.some(r => r.id === registration.job?.id) &&
            <option value={registration.job.id}>방금 등록한 Feature · {registration.job.id.slice(0,8)}</option>}
          {registrations.filter(j => j.status === "Succeeded" && !j.retired &&
            typeof j.result?.feature_service === "string").map(j => <option key={j.id} value={j.id}>
            {datasetName(j.dataset_id)} · {Array.isArray(j.spec.features) ? j.spec.features.length + "개 Feature" : "등록 Feature"} · {new Date(j.created_at).toLocaleDateString("ko-KR")}{j.blocked ? " · 조회 차단" : ""}
          </option>)}
        </select>
      </label>
      {selectedRegistration && <RegisteredFeatureSummary registration={selectedRegistration}
        sourceName={datasetName(selectedRegistration.dataset_id)} />}
      {serviceSchemaLoading && <p role="status">등록 원본의 식별 컬럼 확인 중…</p>}
      {currentServiceSchema?.error && <div role="alert"><p>등록 원본의 자료형 조회 실패 · {currentServiceSchema.error}</p>
        <button type="button" className="secondary" onClick={() => setServiceSchemaRetry(value => value + 1)}>등록 원본 다시 확인</button></div>}
      {registrationCursor && <button type="button" className="secondary"
        disabled={loadingMore} onClick={() => void loadEarlierRegistrations()}>
        {loadingMore ? "이전 등록을 조회하는 중…" : "이전 Feature Service 더 보기"}
      </button>}
      {canManage && <details>
        <summary>Feature Service 폐기·등록 잔여물 정리</summary>
        <p className="hint">완료·실패·취소된 등록을 폐기할 수 있습니다. 과거 조회나 다른
          활성 등록이 사용 중인 정의는 Registry에 보존되며 신규 조회에는 사용할 수 없습니다.</p>
        <label>정리할 등록
          <select value={retireTarget} onChange={e => { setRetireTarget(e.target.value); setRetireConfirmation(null); }}
            disabled={!canManage || retiring} aria-label="Feast 폐기 대상 등록">
            <option value="">등록 기록 선택</option>
            {registrations.filter(r => !r.retired && TERMINAL.has(r.status)).map(r =>
              <option key={r.id} value={r.id}>{datasetName(r.dataset_id)} · {jobStatusLabel(r.status)} · {r.id.slice(0,8)}</option>)}
            {registration.job && !registrations.some(r => r.id === registration.job?.id) &&
              !(retirement.job?.status === "Succeeded" && retirement.job.spec.registration_job_id === registration.job.id) &&
              TERMINAL.has(registration.job.status) &&
              <option value={registration.job.id}>{registration.job.id.slice(0,8)} · 최근 등록</option>}
          </select>
        </label>
        <button type="button" disabled={!canManage || !retireTarget || retiring || Boolean(retireConfirmation)}
          className="secondary" onClick={() => setRetireConfirmation(retireTarget)}>
          등록 폐기 준비
        </button>
        {retireConfirmation && <div className="feature-retirement-confirmation" role="group" aria-label="등록 폐기 확인">
          <strong>선택 등록의 폐기 확인</strong>
          <p>{datasetName(registrations.find(item => item.id === retireConfirmation)?.dataset_id ?? registration.job?.dataset_id)} · 등록 {retireConfirmation.slice(0, 8)}</p>
          <p>이 등록으로 새 조회를 시작할 수 없게 됩니다. 과거 조회 Dataset과 모델 결과는 보존됩니다.
            다른 등록이나 과거 조회가 참조하는 Registry 정의는 남을 수 있습니다.</p>
          <div className="actions"><button type="button" className="secondary" disabled={retiring}
            onClick={() => setRetireConfirmation(null)}>돌아가기</button>
            <button type="button" disabled={!canManage || retiring} onClick={() => void retireRegistration()}>
              {retireBusy ? "폐기 접수 중…" : "선택 등록 폐기"}</button></div>
        </div>}
        {retirement.job && <div role="status">
          <strong>폐기 · {jobStatusLabel(retirement.job.status)}</strong>
          {retirement.job.status === "Failed" && <p role="alert">{retirement.job.error ?? "폐기 실패"}</p>}
          {retirement.job.status === "Succeeded" &&
            <p className="hint">{retirement.job.result?.registry_deleted
              ? "Feast Registry에서도 참조 없는 정의를 정리했습니다."
              : "등록은 폐기됐으며 사용 중인 과거 정의는 Registry에 보존했습니다."}</p>}
        </div>}
        {retirement.error && <p role="alert">{retirement.error}</p>}
      </details>}
      {canManage && <details>
        <summary>새 Feature Service 정의·등록</summary>
        <div className="form-row">
          <label>Feature Source Dataset
            <select aria-label="Feast Feature Source Dataset" value={featureDataset}
              onChange={e => setFeatureDataset(e.target.value)} disabled={!canManage || registering}>
              <option value="">Parquet Dataset 선택</option>
              {sources.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </label>
          <label>연결 대상 식별 컬럼
            <select value={entityColumn} onChange={event => {
              setEntityColumn(event.target.value); setSelected(current => current.filter(name => name !== event.target.value));
            }} disabled={!canManage || registering || columnsLoading} aria-label="Feast Source Entity 키">
              <option value="">식별 컬럼 선택</option>
              {keys.map(column => <option key={column.name} value={column.name}>{column.name} · {column.dtype}</option>)}
            </select>
          </label>
          <label>원본 UTC 시각 컬럼
            <select value={sourceTimestamp} onChange={event => setSourceTimestamp(event.target.value)}
              disabled={!canManage || registering || columnsLoading} aria-label="Feast Source UTC 시각">
              <option value="">UTC 시간형 컬럼 선택</option>
              {sourceTimes.map(column => <option key={column.name} value={column.name}>{column.name}</option>)}
            </select>
          </label>
          <label>과거 Feature 조회 기간 (일)
            <input type="number" min={1} max={3650} step={1} value={ttlDays}
              onChange={e => setTtlDays(Number(e.target.value))}
              disabled={!canManage || registering} aria-label="Feast Source TTL" />
          </label>
        </div>
        <p className="hint">TTL은 조회 시점에서 거슬러 찾을 과거 Feature의 최대 기간입니다. 저장 데이터의 자동 삭제 기간과는 별개입니다.</p>
        {columnsLoading && <p role="status">원본 컬럼 조회 중…</p>}
        {columnsError && <div role="alert">{columnsError}<button type="button" className="secondary"
          onClick={() => setColumnsRetry(value => value + 1)}>원본 컬럼 다시 조회</button></div>}
        {featureDataset && !columnsLoading && !columnsError && sourceTimes.length === 0 &&
          <p className="hint">UTC 시간형 컬럼이 없습니다. UTC Datetime 컬럼을 포함한 Parquet Dataset이 필요합니다.</p>}
        {selected.length > 128 && <p role="alert">한 번에 등록할 Feature는 최대 128개입니다. 현재 {selected.length}개를 선택했습니다.</p>}
        {featureDataset && !columnsLoading && !columnsError && <div className="steps">
          <strong>숫자형 Feature · {selected.length}개</strong>
          <FeatureSelector columns={numeric} selected={selected} disabled={!canManage || registering}
            onChange={setSelected} />
          {numeric.length === 0 && <p className="hint">등록 가능한 숫자형 Feature가 없습니다.</p>}
        </div>}
        <div className="actions">
          <button type="button" disabled={!canManage || !featureDataset || !selected.length ||
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
          {canManage && ["Queued","Running"].includes(registration.job.status) &&
            !registration.job.cancel_requested &&
            <button type="button" className="secondary"
              onClick={() => void cancel(registration.job!, registration.setJob, registration.setError)}>
              취소 요청
            </button>}
        </div>}
        {registration.error && <p role="alert">{registration.error}</p>}
      </details>}
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
      {keyIssue && <p role="alert">{keyIssue}</p>}
      {retirementBlockReason && <p role="status">{retirementBlockReason}</p>}
      <p className="hint">조회 시점 Dataset에는 등록한 서비스의 동일 Entity 키와 UTC 시각이
        필요합니다. 원본 Feature 계산과 모델 학습은 자동 실행하지 않습니다.</p>
      <div className="actions">
        <button type="button" disabled={!canCreate || !registrationId || !entityDataset ||
          !selectedRegistration || retirementBlocksSelection || entitySchemaId !== entityDataset || typeof requiredKey !== "string" || !currentServiceSchema || Boolean(currentServiceSchema.error) ||
          !entityTimestamp.trim() || retrieving || Boolean(keyIssue) || entityColumnsLoading || Boolean(entityColumnsError)} onClick={() => void createRetrieval()}>
          {retrievalBusy ? "접수 중…" : "조회 시점에 맞는 Feature Dataset 생성"}
        </button>
      </div>
      <p className="hint">Point-in-Time Retrieval · 각 조회 시점 이후의 Feature는 사용하지 않습니다.</p>
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
          {purposeText[purpose].ready}
        </button>}
      </div>}
      {retrieval.error && <p role="alert">{retrieval.error}</p>}
    </div>
    {listError && <div role="alert">등록 목록 조회 실패 · {listError}
      <button type="button" className="secondary" onClick={() => setListRetry(value => value + 1)}>등록 목록 다시 조회</button></div>}
  </details>;
}
