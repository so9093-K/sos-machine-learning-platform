import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import type { DatasetColumns, ModelOffering, ModelOfferingPage } from "./types";
import { checkModelDatasetInput, ModelDatasetCompatibility } from "./ModelDatasetCompatibility";
import { algorithmLabel, formatRecordedAt } from "./display-format";

/** 모델 활용의 진입점. 개발·배포 과정이 제공한 모델 판본의 목적과 입력 조건을 먼저 보인다. */
export function ModelCatalog({ onOpenModel, onScoreModel, canRun = false, datasetId = "", datasetName, onClearDataset }: {
  canRun?: boolean;
  datasetId?: string;
  datasetName?: string;
  onClearDataset?: () => void;
  onOpenModel: (id: string) => void;
  onScoreModel: (id: string) => void;
}) {
  const [items, setItems] = useState<ModelOffering[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [schema, setSchema] = useState<{ id: string; columns: DatasetColumns["columns"] | null; error: string } | null>(null);
  useEffect(() => {
    if (!datasetId) { setSchema(null); return; }
    let active = true;
    setSchema({ id: datasetId, columns: null, error: "" });
    void request<DatasetColumns>(`/api/datasets/${encodeURIComponent(datasetId)}/columns`)
      .then(data => { if (active) setSchema({ id: datasetId, columns: data.columns, error: "" }); })
      .catch(cause => { if (active) setSchema({ id: datasetId, columns: null, error: formatError(cause) }); });
    return () => { active = false; };
  }, [datasetId, revision]);
  const currentSchema = schema?.id === datasetId ? schema : null;
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    void request<ModelOfferingPage>("/api/ml/offered-models?limit=50")
      .then(page => { if (active) { setItems(page.items); setCursor(page.next_cursor ?? null); } })
      .catch(cause => { if (active) setError(formatError(cause)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [revision]);
  async function loadMore() {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await request<ModelOfferingPage>(
        "/api/ml/offered-models?limit=50&cursor=" + encodeURIComponent(cursor));
      setItems(previous => [...previous, ...page.items.filter(item =>
        !previous.some(entry => entry.model.id === item.model.id))]);
      setCursor(page.next_cursor ?? null);
    } catch (cause) { setError(formatError(cause)); }
    finally { setLoadingMore(false); }
  }
  return <section className="panel model-version-panel" aria-labelledby="offered-models-heading">
    <div className="panel-head"><div><h2 id="offered-models-heading">제공 모델</h2>
      <p>준비된 모델의 목적과 입력 조건을 확인한 뒤 자신의 Dataset에 적용합니다.</p></div>
      <div className="actions">
        <button type="button" className="secondary" onClick={() => setRevision(x => x + 1)}>새로고침</button>
      </div>
    </div>
    {!canRun && <p className="hint">조회 전용 · 모델 설명과 입력 조건을 확인할 수 있습니다. 데이터 적용에는 작업 실행 권한이 필요합니다.</p>}
    {datasetId && <div className="model-dataset-context">
      <strong>선택 Dataset · {datasetName || "선택한 Dataset"}</strong>
      <p className="hint">모델별 Feature 이름·자료형을 비교합니다. 입력 구조 일치는 성능·업무 적합성 검증이 아닙니다.</p>
      {onClearDataset && <>
        <button type="button" className="secondary" onClick={onClearDataset}>Dataset 선택 해제</button>
        <p className="hint">선택 해제 후 모델 적용 화면에서 다른 Dataset을 고르거나 값 직접 입력을 확인할 수 있습니다.</p>
      </>}
      {!currentSchema?.columns && !currentSchema?.error && <p role="status">Dataset 입력 구조 확인 중…</p>}
      {currentSchema?.error && <p role="alert">Dataset 입력 구조 조회 실패 · {currentSchema.error}</p>}
    </div>}
    {loading && <p role="status">사용할 수 있는 모델을 조회하고 있습니다.</p>}
    {error && <p role="alert">{error}</p>}
    {!loading && !error && items.length === 0 &&
      <p className="muted">아직 준비된 모델이 없습니다. 모델이 제공되면 이곳에서 선택해 데이터에 적용할 수 있습니다.</p>}
    <div className="model-version-list">
      {items.map(item => <article className="model-version-row" key={item.model.id}>
        <div className="model-version-identity">
          <h3>{algorithmLabel(item.model.algorithm)} · v{item.model.registry_version ?? "?"}</h3>
          <p className="model-offering-description">{item.description}</p>
          <small>제공 {formatRecordedAt(item.offered_at)} · 입력 Feature {item.model.features.length}개</small>
          {datasetId && currentSchema?.columns && <ModelDatasetCompatibility compact model={item.model} columns={currentSchema.columns} />}
        </div>
        <div className="model-index-actions">
          <button type="button" disabled={!canRun || Boolean(datasetId &&
            (!currentSchema?.columns || !checkModelDatasetInput(item.model, currentSchema.columns).ready))}
            onClick={() => onScoreModel(item.model.id)}>{datasetId ? "선택 Dataset에 적용 →" : "데이터에 적용 →"}</button>
          <button type="button" className="secondary" onClick={() => onOpenModel(item.model.id)}>모델 상세</button>
        </div>
      </article>)}
    </div>
    {cursor && <button type="button" className="secondary" disabled={loadingMore}
      onClick={() => void loadMore()}>모델 더 보기</button>}
  </section>;
}
