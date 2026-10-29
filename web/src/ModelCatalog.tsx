import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import type { ModelOffering, ModelOfferingPage } from "./types";
import { algorithmLabel, formatRecordedAt } from "./display-format";

/** 모델 활용의 진입점. 개발·배포 과정이 제공한 모델 판본의 목적과 입력 조건을 먼저 보인다. */
export function ModelCatalog({ onOpenModel, onScoreModel }: {
  onOpenModel: (id: string) => void;
  onScoreModel: (id: string) => void;
}) {
  const [items, setItems] = useState<ModelOffering[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
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
    <div className="panel-head"><div><h2 id="offered-models-heading">사용할 수 있는 모델</h2>
      <p>준비된 모델의 목적과 입력 조건을 확인한 뒤 자신의 Dataset에 적용합니다.</p></div>
      <div className="actions">
        <button type="button" className="secondary" onClick={() => setRevision(x => x + 1)}>새로고침</button>
      </div>
    </div>
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
        </div>
        <div className="model-index-actions">
          <button type="button" onClick={() => onScoreModel(item.model.id)}>데이터에 적용 →</button>
          <button type="button" className="secondary" onClick={() => onOpenModel(item.model.id)}>모델 상세</button>
        </div>
      </article>)}
    </div>
    {cursor && <button type="button" className="secondary" disabled={loadingMore}
      onClick={() => void loadMore()}>모델 더 보기</button>}
  </section>;
}
