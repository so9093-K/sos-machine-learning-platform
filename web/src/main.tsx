import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { ApiError, request, setAccessToken, formatError } from "./api";
import { Workspace } from "./Workspace";
import { StoragePanel } from "./StoragePanel";
import { AuditPanel } from "./AuditPanel";
import type { Identity } from "./types";
import "./style.css";

function App() {
  const [actor, setActor] = useState<Identity | null>(null);
  const [checking, setChecking] = useState(true);
  const [token, setToken] = useState("");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
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
  useEffect(() => { void checkIdentity(); }, []);
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
  if (checking) return <main className="main" role="status">사용자 권한을 확인하고 있습니다.</main>;
  if (!actor) return <main className="main">
    <section className="panel" aria-label="인증">
      <h1>SOS Platform 접근 인증</h1>
      <p>현재 서비스에서 발급받은 Bearer 토큰을 입력하세요.
        토큰은 브라우저 메모리에만 보관하며, 다시 접속하면 재인증이 필요합니다.</p>
      <form onSubmit={event => void authenticate(event)}>
        <label>액세스 토큰 <input type="password" value={token}
          onChange={event => setToken(event.target.value)} autoComplete="off"
          aria-label="액세스 토큰" required /></label>
        <button disabled={submitting || !token}>인증</button>
      </form>
      {message && <p role="alert">{message}</p>}
      <button type="button" className="secondary" onClick={() => void checkIdentity()}>
        연결 다시 확인
      </button>
    </section>
  </main>;
  const canViewDataset = actor.roles.some(r => ["viewer","researcher","administrator"].includes(r));
  return <>
    {canViewDataset ? <Workspace actor={actor}
      onSignOut={() => { setAccessToken(null); setActor(null); }} /> :
      <main className="main"><h1>운영 진단</h1><StoragePanel /><AuditPanel /></main>}
  </>;
}
createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
