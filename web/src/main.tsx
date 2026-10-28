import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiError, request, setAccessToken, formatError } from "./api";
import { Workspace } from "./Workspace";
import { StoragePanel } from "./StoragePanel";
import { AuditPanel } from "./AuditPanel";
import { PlatformShell } from "./PlatformShell";
import { useWorkspaceNavigation } from "./PlatformNavigation";
import type { Identity } from "./types";
import type { components } from "./api.generated";

import "./style.css";

type BrandingSettings = components["schemas"]["BrandingSettingsOut"];

function OperatorWorkspace({ actor, brandName, brandReady, brandLoadError, onBrandRetry, onSignOut }: {
  actor: Identity; brandName: string; brandReady: boolean; brandLoadError: string;
  onBrandRetry: () => void; onSignOut: () => void;
}) {
  const { tab, navigate } = useWorkspaceNavigation();
  const canInspectStorage = actor.capabilities.includes("storage.inspect");
  const canViewAudit = actor.capabilities.includes("audit.read");
  const section = tab === "audit" && canViewAudit ? "audit"
    : canInspectStorage ? "storage" : "audit";
  useEffect(() => { if (tab !== section) navigate(section); }, [tab, section, navigate]);
  return <PlatformShell actor={actor} section={section} onNavigate={navigate}
    modelContext={{}} modelResource={null} onSignOut={onSignOut}
    canConfigure={false} brandName={brandName} brandReady={brandReady}
    brandLoadError={brandLoadError} onBrandRetry={onBrandRetry} onBrandChange={() => {}}
    canInspectStorage={canInspectStorage} canViewAudit={canViewAudit}>
    {section === "storage" && canInspectStorage && <StoragePanel />}
    {section === "audit" && canViewAudit && <AuditPanel />}
    {!canInspectStorage && !canViewAudit && <p>현재 계정에서 조회할 수 있는 운영 화면이 없습니다.</p>}
  </PlatformShell>;
}

function App() {
  const [actor, setActor] = useState<Identity | null>(null);
  const [checking, setChecking] = useState(true);
  const [token, setToken] = useState("");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [branding, setBranding] = useState<BrandingSettings | null>(null);
  const [brandingError, setBrandingError] = useState("");
  // 이전 조회 응답이 새로 저장한 제품명을 덮어쓰지 않도록 요청 세대를 추적한다.
  const brandRequestRevision = useRef(0);
  const brandName = branding?.brand_name ?? "ML Platform";
  function reloadBranding() {
    const revision = ++brandRequestRevision.current;
    setBrandingError("");
    void request<BrandingSettings>("/api/branding")
      .then(value => {
        if (revision === brandRequestRevision.current) setBranding(value);
      })
      .catch(error => {
        if (revision !== brandRequestRevision.current) return;
        setBranding(null);
        setBrandingError(formatError(error));
      });
  }
  function applyBrand(name: string) {
    ++brandRequestRevision.current;
    setBranding({ brand_name: name });
    setBrandingError("");
  }
  async function checkIdentity() {
    setChecking(true); setMessage("");
    try {
      setActor(await request<Identity>("/api/v2/auth/me"));
    } catch (error) {
      setMessage(error instanceof ApiError && error.status === 401
        ? ""
        : `서비스 연결을 확인하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도하세요. ${formatError(error)}`);
    } finally { setChecking(false); }
  }
  useEffect(() => { void checkIdentity(); void reloadBranding(); }, []);
  useEffect(() => {
    if (!actor) document.title = `${brandName} · 접근 인증`;
    else if (!actor.capabilities.includes("dataset.read")) {
      document.title = `운영 진단 · ${brandName}`;
    }
  }, [actor, brandName]);
  async function authenticate(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true); setMessage("");
    setAccessToken(token);
    try {
      setActor(await request<Identity>("/api/v2/auth/me"));
      setToken("");
    } catch (error) {
      setAccessToken(null);
      setMessage(error instanceof ApiError && error.status === 401
        ? "액세스 토큰을 확인하세요. 인증되지 않았으며 토큰은 저장되지 않았습니다."
        : formatError(error));
    } finally { setSubmitting(false); }
  }
  function signOut() {
    setAccessToken(null);
    setActor(null);
  }
  if (checking) return <main className="main" role="status">사용자 권한을 확인하고 있습니다.</main>;
  if (!actor) return <main className="main auth-main">
    <section className="panel auth-panel" aria-label="인증">
      <div className="auth-intro">
        <span className="eyebrow">SOS WORKSPACE</span>
        <h1>{brandName} 접근 인증</h1>
        <p>발급받은 액세스 토큰으로 로그인합니다.
          토큰은 브라우저 메모리에만 보관하며 새로 접속하면 다시 인증해야 합니다.</p>
      </div>
      <form className="auth-form" onSubmit={event => void authenticate(event)}>
        <label htmlFor="sos-access-token">액세스 토큰</label>
        <input id="sos-access-token" type="password" value={token}
          onChange={event => setToken(event.target.value)} autoComplete="off"
          aria-label="액세스 토큰" placeholder="액세스 토큰 입력" required />
        <button type="submit" disabled={submitting || !token.trim()}>
          {submitting ? "인증 중…" : "인증하고 시작"}
        </button>
      </form>
      {message && <p className="auth-message" role="alert">{message}</p>}
      {brandingError && <div className="auth-message" role="alert">
        <p>브랜드 정보를 불러오지 못했습니다: {brandingError}</p>
        <button type="button" className="secondary" onClick={reloadBranding}>
          브랜드 다시 조회
        </button>
      </div>}
      {message.startsWith("서비스 연결") && <button type="button" className="secondary"
        onClick={() => void checkIdentity()}>연결 다시 확인</button>}
    </section>
  </main>;
  const canViewDataset = actor.capabilities.includes("dataset.read");
  return <>
    {canViewDataset ? <Workspace actor={actor}
      brandName={brandName} brandReady={branding !== null} brandLoadError={brandingError}
      onBrandChange={applyBrand}
      onBrandRetry={reloadBranding} onSignOut={signOut} /> :
      <OperatorWorkspace actor={actor} brandName={brandName} brandReady={branding !== null}
        brandLoadError={brandingError} onBrandRetry={reloadBranding} onSignOut={signOut} />}
  </>;
}
createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
