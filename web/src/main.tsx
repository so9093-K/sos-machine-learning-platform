import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiError, request, setAccessToken, formatError } from "./api";
import { Workspace } from "./Workspace";
import { StoragePanel } from "./StoragePanel";
import { AuditPanel } from "./AuditPanel";
import type { Identity } from "./types";
import type { components } from "./api.generated";

import "./style.css";

type BrandingSettings = components["schemas"]["BrandingSettingsOut"];

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
        ? "인증이 필요합니다. 발급된 액세스 토큰을 입력하세요."
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
      setMessage(formatError(error));
    } finally { setSubmitting(false); }
  }
  function signOut() {
    setAccessToken(null);
    setActor(null);
  }
  if (checking) return <main className="main" role="status">사용자 권한을 확인하고 있습니다.</main>;
  if (!actor) return <main className="main">
    <section className="panel" aria-label="인증">
      <h1>{brandName} 접근 인증</h1>
      <p>현재 서비스에서 발급받은 Bearer 토큰을 입력하세요.
        토큰은 브라우저 메모리에만 보관하며, 다시 접속하면 재인증이 필요합니다.</p>
      <form onSubmit={event => void authenticate(event)}>
        <label>액세스 토큰 <input type="password" value={token}
          onChange={event => setToken(event.target.value)} autoComplete="off"
          aria-label="액세스 토큰" required /></label>
        <button disabled={submitting || !token}>인증</button>
      </form>
      {message && <p role="alert">{message}</p>}
      {brandingError && <p role="alert">브랜드 정보를 불러오지 못했습니다: {brandingError}</p>}
      {brandingError && <button type="button" className="secondary" onClick={reloadBranding}>
        브랜드 다시 조회
      </button>}
      <button type="button" className="secondary" onClick={() => void checkIdentity()}>
        연결 다시 확인
      </button>
    </section>
  </main>;
  const canViewDataset = actor.capabilities.includes("dataset.read");
  const canInspectStorage = actor.capabilities.includes("storage.inspect");
  const canViewAudit = actor.capabilities.includes("audit.read");
  return <>
    {canViewDataset ? <Workspace actor={actor}
      brandName={brandName} brandReady={branding !== null} brandLoadError={brandingError}
      onBrandChange={applyBrand}
      onBrandRetry={reloadBranding} onSignOut={signOut} /> :
      <main className="main"><h1>{brandName} · 운영 진단</h1>
        {actor.mode !== "development" &&
          <button type="button" className="ghost" onClick={signOut}>인증 해제</button>}
        {canInspectStorage && <StoragePanel />}
        {canViewAudit && <AuditPanel />}
        {brandingError && <p role="alert">브랜드 정보 조회 실패: {brandingError}</p>}
      </main>}
  </>;
}
createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
