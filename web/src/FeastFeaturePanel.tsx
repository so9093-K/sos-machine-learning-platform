import { useEffect, useMemo, useState } from "react";
import { formatError, request, submitJob } from "./api";
import { jobStatusLabel } from "./display-format";
import { FeatureSelector } from "./FeatureSelector";
import type { Dataset, DatasetColumns, Job } from "./types";

const NUMERIC = /^(?:Int(?:32|64)|Float(?:32|64))$/;
const TERMINAL = new Set(["Succeeded", "Failed", "Cancelled"]);

/** 등록 Feature 조회는 기존 학습과 독립된 SOS Job으로 실행·게시된다. */
export function FeastFeaturePanel({ datasets, canCreate, onDatasetReady }: {
  datasets: Dataset[];
  canCreate: boolean;
  onDatasetReady: (datasetId: string) => void;
}) {
  const [featureDataset, setFeatureDataset] = useState("");
  const [entityDataset, setEntityDataset] = useState("");
  const [entityColumn, setEntityColumn] = useState("host_id");
  const [timestampColumn, setTimestampColumn] = useState("event_timestamp");
  const [ttlDays, setTtlDays] = useState(30);
  const [columns, setColumns] = useState<DatasetColumns["columns"]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const numeric = useMemo(() => columns.filter(c => NUMERIC.test(c.dtype)), [columns]);
  const sources = datasets.filter(d => d.kind === "file" && d.name.toLowerCase().endsWith(".parquet"));

  useEffect(() => {
    let active = true;
    setColumns([]);
    setSelected([]);
    if (!featureDataset) return () => { active = false; };
    void request<DatasetColumns>(`/api/datasets/${encodeURIComponent(featureDataset)}/columns`)
      .then(value => { if (active) setColumns(value.columns); })
      .catch(exc => { if (active) setError(formatError(exc)); });
    return () => { active = false; };
  }, [featureDataset]);

  useEffect(() => {
    if (!job || TERMINAL.has(job.status)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const value = await request<Job>(`/api/jobs/${encodeURIComponent(job.id)}`);
        if (active) setJob(value);
      } catch (exc) {
        if (active) setError(formatError(exc));
      } finally {
        if (active) timer = setTimeout(() => void poll(), 2500);
      }
    };
    timer = setTimeout(() => void poll(), 1200);
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [job?.id, job?.status]);

  async function submit() {
    if (!canCreate || !featureDataset || !entityDataset || !entityColumn.trim() ||
        !timestampColumn.trim() || selected.length === 0 || submitting ||
        !Number.isSafeInteger(ttlDays) || ttlDays < 1 || ttlDays > 3650) return;
    setSubmitting(true);
    setError("");
    setJob(null);
    try {
      const value = await submitJob<Job>("/api/features/retrieval/jobs", {
        feature_dataset_id: featureDataset,
        entity_dataset_id: entityDataset,
        entity_column: entityColumn.trim(),
        timestamp_column: timestampColumn.trim(),
        features: selected,
        ttl_seconds: ttlDays * 86400,
      });
      setJob(value);
    } catch (exc) {
      setError(formatError(exc));
    } finally {
      setSubmitting(false);
    }
  }

  async function cancel() {
    if (!job) return;
    try {
      setJob(await request<Job>(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, {
        method: "POST",
      }));
    } catch (exc) {
      setError(formatError(exc));
    }
  }

  const publishedId = job?.status === "Succeeded" && job.result &&
    typeof job.result.dataset_id === "string" ? job.result.dataset_id : null;

  return <details className="panel">
    <summary>등록 Feature로 학습 Dataset 구성 · Feast</summary>
    <p className="hint">소스의 기존 Feature를 Entity와 시점에 맞춰 조회합니다. Feature 계산이나
      모델 학습은 자동 실행하지 않습니다. 게시된 Dataset을 직접 학습에 사용할 수 있습니다.</p>
    <div className="form-row">
      <label>Feature 원본 Dataset
        <select aria-label="Feast Feature 원본 Dataset" value={featureDataset}
          onChange={e => setFeatureDataset(e.target.value)} disabled={!canCreate}>
          <option value="">Parquet Dataset 선택</option>
          {sources.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </label>
      <label>조회 시점 Dataset
        <select aria-label="Feast 조회 시점 Dataset" value={entityDataset}
          onChange={e => setEntityDataset(e.target.value)} disabled={!canCreate}>
          <option value="">Dataset 선택</option>
          {datasets.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </label>
    </div>
    <div className="form-row">
      <label>지속적 Entity 키 컬럼
        <input value={entityColumn} onChange={e => setEntityColumn(e.target.value)}
          maxLength={256} disabled={!canCreate} aria-label="Feast Entity 키 컬럼" />
      </label>
      <label>UTC 시간 컬럼
        <input value={timestampColumn} onChange={e => setTimestampColumn(e.target.value)}
          maxLength={256} disabled={!canCreate} aria-label="Feast UTC 시간 컬럼" />
      </label>
      <label>Feature 유효 기간 (일)
        <input type="number" min={1} max={3650} step={1} value={ttlDays}
          onChange={e => setTtlDays(Number(e.target.value))}
          disabled={!canCreate} aria-label="Feast Feature TTL" />
      </label>
    </div>
    <p className="hint">Entity 키는 관측치 UUID와 다르며, 같은 대상에 대해 시점별로
      유지되는 식별자입니다. 두 Dataset에는 같은 Entity 키 컬럼 및 UTC Datetime 컬럼이 필요합니다.</p>
    {featureDataset && <div className="steps">
      <strong>등록할 숫자형 Feature · {selected.length}개</strong>
      <FeatureSelector columns={numeric} selected={selected} disabled={!canCreate}
        onChange={setSelected} />
      {numeric.length === 0 && <p className="hint">등록 가능한 숫자형 컬럼이 없습니다.</p>}
    </div>}
    <div className="actions">
      <button type="button" disabled={!canCreate || submitting || !featureDataset ||
        !entityDataset || !selected.length || !entityColumn.trim() ||
        !timestampColumn.trim() || !Number.isInteger(ttlDays) || ttlDays < 1 || ttlDays > 3650}
        onClick={() => void submit()}>
        {submitting ? "접수 중…" : "Feature 조회 Job 제출"}
      </button>
    </div>
    {error && <p role="alert">{error}</p>}
    {job && <div role="status" className="panel">
      <strong>Feature 조회 · {jobStatusLabel(job.status)}</strong>
      <p className="hint">조회는 별도 Job이며 원본 Dataset을 수정하지 않습니다.</p>
      {job.status === "Failed" && <p role="alert">{job.error ?? "조회에 실패했습니다."}</p>}
      {canCreate && ["Queued", "Running"].includes(job.status) && !job.cancel_requested &&
        <button type="button" className="secondary" onClick={() => void cancel()}>취소 요청</button>}
      {publishedId && <button type="button" onClick={() => onDatasetReady(publishedId)}>
        게시된 Feature Dataset으로 학습 준비
      </button>}
    </div>}
  </details>;
}
