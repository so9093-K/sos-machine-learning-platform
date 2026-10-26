import { useEffect, useState, type FormEvent } from "react";
import { formatError, request } from "./api";
import type { components } from "./api.generated";

export type InstanceSettings = components["schemas"]["InstanceSettingsOut"];
export type BrandingSettings = components["schemas"]["BrandingSettingsOut"];

type Props = {
  canEdit: boolean;
  brandName: string;
  brandLoadError: string;
  onBrandChange: (name: string) => void;
  onBrandRetry: () => void;
};

export function InstanceBrand({ canEdit, brandName, brandLoadError,
  onBrandChange, onBrandRetry }: Props) {
  const [instance, setInstance] = useState<InstanceSettings | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [brandDraft, setBrandDraft] = useState("");
  const [brandEditing, setBrandEditing] = useState(false);
  const [brandSaving, setBrandSaving] = useState(false);
  const [brandError, setBrandError] = useState("");

  useEffect(() => {
    let active = true;
    setError("");
    request<InstanceSettings>("/api/instance")
      .then(value => {
        if (active) { setInstance(value); setDraft(value.display_name); setError(""); }
      })
      .catch(cause => { if (active) setError(formatError(cause)); });
    return () => { active = false; };
  }, [reload]);

  async function save(event: FormEvent) {
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

  async function saveBrand(event: FormEvent) {
    event.preventDefault();
    const name = brandDraft.trim();
    if (!name || name.length > 48) return;
    setBrandSaving(true); setBrandError("");
    try {
      const changed = await request<BrandingSettings>("/api/branding", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ brand_name: name }),
      });
      onBrandChange(changed.brand_name);
      setBrandEditing(false);
    } catch (cause) { setBrandError(formatError(cause)); }
    finally { setBrandSaving(false); }
  }

  return <div className="brand">
    <div className="brandmark" aria-hidden="true">{Array.from(brandName)[0]?.toUpperCase() ?? "M"}</div>
    <div className="brand-content">
      <strong>{brandName}</strong>
      <span className="instance-name" aria-live="polite">{
        instance?.display_name ?? (error ? "설정 조회 실패" : "인스턴스 정보 조회 중")
      }</span>
      {canEdit && !brandEditing && <button type="button"
        className="brand-edit ghost"
        onClick={() => { setBrandDraft(brandName); setBrandError(""); setBrandEditing(true); }}>
        제품명 설정
      </button>}
      {brandEditing && <form className="brand-form" onSubmit={event => void saveBrand(event)}>
        <label htmlFor="brand-display-name">제품 브랜드명</label>
        <input id="brand-display-name" value={brandDraft} maxLength={48}
          onChange={event => setBrandDraft(event.target.value)} autoFocus required />
        <div className="brand-form-actions">
          <button type="submit" disabled={brandSaving || !brandDraft.trim()}>
            {brandSaving ? "저장 중" : "저장"}
          </button>
          <button type="button" className="ghost" onClick={() => {
            setBrandEditing(false); setBrandError("");
          }}>취소</button>
        </div>
      </form>}
      {brandError && <div className="brand-error" role="alert">{brandError}</div>}
      {brandLoadError && <div className="brand-error" role="alert">
        <p>제품명 조회 실패: {brandLoadError}</p>
        <button type="button" className="ghost" onClick={onBrandRetry}>제품명 다시 조회</button>
      </div>}
      {canEdit && !editing && <button type="button" disabled={!instance}
        className="brand-edit ghost"
        onClick={() => { setDraft(instance!.display_name); setEditing(true); }}>
        인스턴스 표시명 설정
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
      {error && <div className="brand-error" role="alert">
        <p>{error}</p>
        {!instance && <button type="button" className="ghost"
          onClick={() => setReload(value => value + 1)}>설정 다시 조회</button>}
      </div>}
    </div>
  </div>;
}
