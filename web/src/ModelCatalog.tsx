import { useEffect, useRef, useState } from "react";
import { formatError, request } from "./api";
import { ModelRegistryManager, ModelVersionManagement, type ModelGroup } from "./ModelRegistryManager";
import type { Dataset, MLflowModelVersion, MLflowModelVersionPage } from "./types";
import { formatRecordedAt } from "./display-format";
import { isLocalOperatorHost, mlflowRunUrl } from "./operator-tools";

/** 모델 활용은 등록 판본의 선택과 실제 입력·결과로 이어지는 진입점이다. */
export function ModelCatalog({ datasets, registryKey, onSelectGroup, onOpenModel,
  onScoreModel, canManage, canInspectOperations = false }: {
  datasets: Dataset[]; registryKey: string;
  onSelectGroup: (key: string) => void;
  onOpenModel: (id: string) => void;
  onScoreModel: (id: string, group: string) => void;
  canManage: boolean; canInspectOperations?: boolean;
}) {
  const [localOperatorHost, setLocalOperatorHost] = useState(false);
  useEffect(() => { setLocalOperatorHost(isLocalOperatorHost()); }, []);
  const operatorLinks = canInspectOperations && localOperatorHost;
  const [models, setModels] = useState<MLflowModelVersion[]>([]);
  const [modelCursor, setModelCursor] = useState<string | null>(null);
  const [modelSelectionKey, setModelSelectionKey] = useState(registryKey);
  const [currentGroup, setCurrentGroup] = useState<ModelGroup | null>(null);
  const group = currentGroup?.registry_key === registryKey ? currentGroup : null;
  const groupTitle = group?.title ?? (registryKey === "default" ? "기본 활동 이상 탐지" : registryKey);
  const groupArchived = group?.archived ?? false;
  const [term, setTerm] = useState("");
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const activeSelection = useRef({ registryKey, revision });
  activeSelection.current = { registryKey, revision };
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setModelCursor(null);
    void request<MLflowModelVersionPage>("/api/ml/registry/models?" + new URLSearchParams({
      registry_key: registryKey, include_archived: "true",
    })).then(model => {
      if (!active) return;
      setModelSelectionKey(registryKey);
      setModels(model.items); setModelCursor(model.next_page_token ?? null);
    }).catch(exc => { if (active) setError(formatError(exc)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [revision, registryKey]);

  async function loadMore() {
    if (!modelCursor || loadingMore) return;
    const selection = activeSelection.current;
    setLoadingMore(true); setError("");
    try {
      const page = await request<MLflowModelVersionPage>("/api/ml/registry/models?" + new URLSearchParams({
        registry_key: registryKey, include_archived: "true", page_token: modelCursor,
      }));
      if (activeSelection.current.registryKey !== selection.registryKey ||
          activeSelection.current.revision !== selection.revision) return;
      setModels(old => [...old, ...page.items]);
      setModelCursor(page.next_page_token ?? null);
    } catch (exc) {
      if (activeSelection.current.registryKey === selection.registryKey &&
          activeSelection.current.revision === selection.revision) setError(formatError(exc));
    } finally { setLoadingMore(false); }
  }
  const filter = term.trim().toLowerCase();
  const datasetName = (id: string | null | undefined) => datasets.find(item => item.id === id)?.name ??
    (id ? "현재 목록 외 Dataset" : "Dataset 정보 없음");
  const filteredModels = modelSelectionKey !== registryKey ? [] : models.filter(item =>
    [groupTitle, item.version, datasetName(item.dataset_id), item.job_id]
      .some(value => value.toLowerCase().includes(filter)));
  return <div className="ml-resource-workspace">
    <ModelRegistryManager activeKey={registryKey} onSelect={onSelectGroup}
      canManage={false} onGroupResolved={setCurrentGroup} onChanged={() => setRevision(x => x + 1)} />
    <section className="panel model-version-panel">
      <div className="panel-head"><div><h2>{groupTitle} · 모델 버전</h2>
        <p>등록 모델의 독립 실행입니다. 학습 데이터와 시각을 확인하고, 선택한 판본의 입력 조건에 맞춰 데이터를 적용합니다.</p></div>
        <div className="actions">
          <button type="button" className="secondary" onClick={() => setRevision(x => x + 1)}>새로고침</button>
        </div>
      </div>
      <label className="model-search">버전 검색
        <input value={term} onChange={event => setTerm(event.target.value)}
          placeholder="버전·학습 Dataset" aria-label="모델 버전 검색" />
      </label>
      <p className="hint">현재 불러온 모델 버전에서 검색합니다.{modelCursor ? " 이전 버전은 더 보기로 조회할 수 있습니다." : ""}</p>
      {loading && <p role="status">모델 버전을 조회하고 있습니다.</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && filteredModels.length === 0 && <p className="muted">
        {term.trim() ? "현재 불러온 버전에서 검색 조건과 일치하는 결과가 없습니다." : modelCursor ? "현재 페이지에 표시할 버전이 없습니다. 이전 버전을 더 조회할 수 있습니다." : "현재 계정에서 사용할 수 있는 모델 판본이 없습니다. 모델 제공 담당자에게 등록·접근 상태를 확인하세요."}</p>}
      <div className="model-version-list">
        {filteredModels.map(item => <article className="model-version-row" key={item.name + "@" + item.version}>
          <div className="model-version-identity"><h3>{groupTitle} · v{item.version}</h3>
            <p>{datasetName(item.dataset_id)}</p>
            <small>{item.created_at ? formatRecordedAt(item.created_at) : "학습 시각 정보 없음"}
              {item.archived ? " · 보관됨" : " · 등록 완료"}</small>
          </div>
          <div className="model-index-actions">
            <button type="button" disabled={item.archived || groupArchived}
              onClick={() => onScoreModel(item.model_id, registryKey)}>데이터에 적용 →</button>
            <button type="button" className="secondary" onClick={() => onOpenModel(item.model_id)}>모델 상세</button>
            {operatorLinks && item.run_id && <a href={mlflowRunUrl(item.run_id)} target="_blank" rel="noopener noreferrer">학습 Run 운영 상세 ↗</a>}
            <details className="model-version-settings"><summary>버전 관리·식별자</summary>
              <p className="hint">Registry {item.name} · Job {item.job_id}</p>
              {(item.aliases?.length ?? 0) > 0 && <p>Alias: {item.aliases?.join(", ")}</p>}
              <ModelVersionManagement model={item} registryKey={registryKey} canManage={canManage}
                onChanged={() => setRevision(x => x + 1)} />
            </details>
          </div>
        </article>)}
      </div>
      {modelCursor && <button type="button" className="secondary" disabled={loadingMore}
        onClick={() => void loadMore()}>모델 버전 더 보기</button>}
    </section>
  </div>;
}
