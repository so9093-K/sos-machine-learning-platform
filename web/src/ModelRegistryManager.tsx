/** MLflow 모델 관리 정본을 SOS 권한이 적용된 API로 조작한다. */
import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import type { MLflowModelVersion } from "./types";

export type ModelGroup = {
  registry_key: string;
  name: string;
  title: string;
  description: string;
  archived: boolean;
};
type GroupPage = { items: ModelGroup[]; next_page_token: string | null };
type Impact = {
  model_id: string;
  lifecycle_state: "Active" | "Deleting" | "Deleted";
  references: {
    scoring: number; policies: number; evaluations: number;
    explanations: number; active_jobs: number; shared_artifacts: number;
  };
};

function body(method: string, value: unknown): RequestInit {
  return {
    method, headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  };
}

export function ModelRegistryManager({ activeKey, onSelect, onChanged, canManage }: {
  activeKey: string;
  onSelect: (key: string) => void;
  onChanged: () => void;
  canManage: boolean;
}) {
  const [groups, setGroups] = useState<ModelGroup[]>([]);
  const [groupCursor, setGroupCursor] = useState<string | null>(null);
  const [loadingMoreGroups, setLoadingMoreGroups] = useState(false);
  const [pendingDeletes, setPendingDeletes] = useState<Array<{
    id: string; registry_key: string; registry_version: string;
  }>>([]);
  const [key, setKey] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const current = groups.find(g => g.registry_key === activeKey);
  useEffect(() => {
    let active = true;
    void Promise.all([
      request<GroupPage>("/api/ml/registry/groups"),
      request<{items: Array<{id:string; registry_key:string; registry_version:string}>}>(
        "/api/ml/models/deletion-pending",
      ),
    ]).then(([groupsPage, pendingPage]) => {
      if (active) {
        setGroups(groupsPage.items);
        setGroupCursor(groupsPage.next_page_token ?? null);
        setPendingDeletes(pendingPage.items);
      }
    }).catch(e => { if (active) setError(formatError(e)); });
    return () => { active = false; };
  }, [revision]);
  useEffect(() => {
    setEditTitle(current?.title ?? "");
    setEditDescription(current?.description ?? "");
  }, [current?.title, current?.description, activeKey]);

  async function loadMoreGroups() {
    if (!groupCursor || loadingMoreGroups) return;
    setLoadingMoreGroups(true); setError("");
    try {
      const currentCursor = groupCursor;
      const page = await request<GroupPage>(
        "/api/ml/registry/groups?page_token=" + encodeURIComponent(currentCursor));
      setGroups(previous => {
        const seen = new Set(previous.map(group => group.registry_key));
        return [...previous, ...page.items.filter(group => !seen.has(group.registry_key))];
      });
      setGroupCursor(page.next_page_token ?? null);
    } catch (e) { setError(formatError(e)); }
    finally { setLoadingMoreGroups(false); }
  }

  async function change(action: () => Promise<unknown>) {
    setPending(true); setError("");
    try {
      await action();
      setRevision(x => x + 1);
      onChanged();
    } catch (e) { setError(formatError(e)); }
    finally { setPending(false); }
  }

  return <section className="panel" aria-label="모델 그룹 관리">
    <div className="panel-head">
      <div><h3>내 모델 관리</h3>
        <p className="hint">목적별 모델을 만들고, 각 모델의 학습 버전을 관리합니다.</p></div>
    </div>
    {error && <p role="alert">{error}</p>}
    <label>모델 그룹 선택
      <select value={activeKey} onChange={e => onSelect(e.target.value)}>
        <option value="default">기본 활동 이상 탐지</option>
        {groups.filter(g => g.registry_key !== "default").map(g =>
          <option key={g.registry_key} value={g.registry_key}>
            {g.title}{g.archived ? " (보관됨)" : ""}
          </option>)}
      </select>
    </label>
    {groupCursor && <button type="button" className="secondary"
      disabled={loadingMoreGroups} onClick={() => void loadMoreGroups()}>
      {loadingMoreGroups ? "모델 그룹 조회 중…" : "모델 그룹 더 보기"}
    </button>}
    {canManage && current && <div className="steps">
      <label>모델 표시 이름
        <input value={editTitle} maxLength={80}
          onChange={e => setEditTitle(e.target.value)} />
      </label>
      <label>설명
        <input value={editDescription} maxLength={1000}
          onChange={e => setEditDescription(e.target.value)} />
      </label>
      <div className="actions">
        <button type="button" className="secondary" disabled={pending || !editTitle.trim()}
          onClick={() => void change(() => request(
            "/api/ml/registry/groups/" + encodeURIComponent(activeKey),
            body("PATCH", { title: editTitle.trim(), description: editDescription }),
          ))}>이름·설명 저장</button>
        <button type="button" className="secondary" disabled={pending}
          onClick={() => void change(() => request(
            "/api/ml/registry/groups/" + encodeURIComponent(activeKey),
            body("PATCH", { archived: !current.archived }),
          ))}>{current.archived ? "모델 그룹 복원" : "모델 그룹 보관"}</button>
        {activeKey !== "default" &&
          <button type="button" className="secondary" disabled={pending}
            onClick={() => {
              if (!window.confirm("빈 모델 그룹을 영구 삭제하시겠습니까? 등록된 버전이 있으면 거부됩니다.")) return;
              void change(async () => {
                await request("/api/ml/registry/groups/" + encodeURIComponent(activeKey),
                  { method: "DELETE" });
                onSelect("default");
              });
            }}>빈 그룹 삭제</button>}
      </div>
    </div>}
    {canManage && pendingDeletes.length > 0 && <div className="pipeline-readiness-blocked">
      <h4>영구 삭제 완료 대기</h4>
      <p>S3 또는 Registry 작업이 끝나지 않은 모델입니다. 완료 전에는 다시 사용할 수 없습니다.</p>
      {pendingDeletes.map(item => <div key={item.id} className="actions">
        <span>{item.registry_key} · v{item.registry_version} · {item.id.slice(0, 8)}</span>
        <button type="button" disabled={pending} onClick={() => {
          if (!window.confirm("이 모델의 영구 삭제를 계속 진행하시겠습니까?")) return;
          void change(() => request(
            "/api/ml/models/" + encodeURIComponent(item.id) + "?confirm=true",
            { method: "DELETE" },
          ));
        }}>삭제 재시도</button>
      </div>)}
    </div>}
    {canManage && <details>
      <summary>새 모델 그룹 만들기</summary>
      <div className="steps">
        <label>모델 키 (영문 소문자·숫자·하이픈)
          <input value={key} maxLength={40} placeholder="activity-risk"
            onChange={e => setKey(e.target.value)} />
        </label>
        <label>모델 표시 이름
          <input value={title} maxLength={80} placeholder="활동 이상 탐지 실험 A"
            onChange={e => setTitle(e.target.value)} />
        </label>
        <label>설명
          <input value={description} maxLength={1000}
            onChange={e => setDescription(e.target.value)} />
        </label>
        <button type="button" disabled={pending || !title.trim() ||
          !/^[a-z][a-z0-9-]{0,39}$/.test(key) || key === "default"}
          onClick={() => void change(async () => {
            await request("/api/ml/registry/groups", body("POST", {
              registry_key: key, title: title.trim(), description,
            }));
            onSelect(key);
            setKey(""); setTitle(""); setDescription("");
          })}>새 모델 만들기</button>
      </div>
    </details>}
    <p className="hint">새 그룹을 만든 뒤 학습 요청의 registry_key에 해당 키를 지정하면 새 버전이 등록됩니다.</p>
  </section>;
}

export function ModelVersionManagement({ model, registryKey, onChanged, canManage }: {
  model: MLflowModelVersion;
  registryKey: string;
  onChanged: () => void;
  canManage: boolean;
}) {
  const [impact, setImpact] = useState<Impact | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function mutate(action: () => Promise<unknown>) {
    setError(""); setBusy(true);
    try { await action(); setImpact(null); setAcknowledged(false); onChanged(); }
    catch (e) { setError(formatError(e)); }
    finally { setBusy(false); }
  }

  if (!canManage) return null;
  return <div className="model-version-management">
    <button type="button" className="secondary" disabled={busy}
      onClick={() => void mutate(() => request(
        "/api/ml/registry/groups/" + encodeURIComponent(registryKey) +
        "/versions/" + encodeURIComponent(model.version) + "/archive",
        body("PATCH", { archived: !model.archived }),
      ))}>{model.archived ? "버전 복원" : "버전 보관"}</button>
    <button type="button" className="secondary" disabled={busy}
      onClick={() => {
        setBusy(true); setError("");
        void request<Impact>("/api/ml/models/" + encodeURIComponent(model.model_id) +
          "/deletion-impact").then(setImpact).catch(e => setError(formatError(e)))
          .finally(() => setBusy(false));
      }}>영구 삭제 영향 확인</button>
    {impact && <div className="pipeline-readiness-blocked" role="group"
      aria-label="영구 삭제 영향 확인">
      <p>이 모델의 학습 파일과 Registry 버전을 영구 삭제합니다.
        과거 Scoring·평가 결과와 삭제된 모델의 기록은 남습니다.</p>
      <p>연결된 Scoring {impact.references.scoring}개,
        정책 {impact.references.policies}개, 평가 {impact.references.evaluations}개,
        설명 {impact.references.explanations}개 · 진행 중 작업 {impact.references.active_jobs}개</p>
      {impact.references.active_jobs > 0 || impact.references.shared_artifacts > 0
        ? <p>작업 진행 중이거나 다른 모델과 아티팩트를 공유하면 삭제할 수 없습니다.</p>
        : <>
          <label><input type="checkbox" checked={acknowledged}
            onChange={e => setAcknowledged(e.target.checked)}/>
            모델 파일 영구 삭제 및 이후 사용 불가에 동의합니다.</label>
          <button type="button" disabled={busy || !acknowledged ||
            impact.lifecycle_state === "Deleted"}
            onClick={() => void mutate(() => request(
              "/api/ml/models/" + encodeURIComponent(model.model_id) + "?confirm=true",
              { method: "DELETE" },
            ))}>영구 삭제 실행</button>
        </>}
    </div>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
