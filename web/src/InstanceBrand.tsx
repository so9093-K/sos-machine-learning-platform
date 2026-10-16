import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import type { components } from "./api.generated";

export type InstanceSettings = components["schemas"]["InstanceSettingsOut"];

export function InstanceBrand({ canEdit }: { canEdit: boolean }) {
  const [instance, setInstance] = useState<InstanceSettings | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    request<InstanceSettings>("/api/instance")
      .then(value => { if (active) { setInstance(value); setDraft(value.display_name); } })
      .catch(cause => { if (active) setError(formatError(cause)); });
    return () => { active = false; };
  }, []);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    const name = draft.trim();
    if (!name || name.length > 48) return;
    setSaving(true); setError("");
    try {
      const changed = await request<InstanceSettings>("/api/instance", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: name }),
      });
      setInstance(changed); setDraft(changed.display_name); setEditing(false);
    } catch (cause) { setError(formatError(cause)); }
    finally { setSaving(false); }
  }
  return <div className="brand">
    <div className="brandmark" aria-hidden="true">S</div>
    <div className="brand-content">
      <strong>SOS</strong>
      <span className="instance-name">{instance?.display_name ?? "Workspace"}</span>
      {canEdit && !editing && <button type="button" className="brand-edit ghost"
        onClick={() => { setDraft(instance?.display_name ?? "Local Workspace"); setEditing(true); }}>
        표시명 설정
      </button>}
      {editing && <form className="brand-form" onSubmit={event => void save(event)}>
        <label htmlFor="instance-display-name">인스턴스 표시명</label>
        <input id="instance-display-name" value={draft} maxLength={48}
          onChange={event => setDraft(event.target.value)} autoFocus required />
        <div className="brand-form-actions">
          <button type="submit" disabled={saving || !draft.trim()}>{saving ? "저장 중" : "저장"}</button>
          <button type="button" className="ghost" onClick={() => {
            setEditing(false); setDraft(instance?.display_name ?? ""); setError("");
          }}>취소</button>
        </div>
      </form>}
      {error && <p className="brand-error" role="alert">{error}</p>}
    </div>
  </div>;
}
