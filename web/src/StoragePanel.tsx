import { useEffect, useState } from "react";
import { request } from "./api";
import type { IcebergReference, StorageInspection } from "./types";

const statusLabel = {
  protected: "보호 중",
  waiting: "관측 대기",
  candidate: "정리 검토 후보",
  unknown: "확인 필요",
  missing: "Snapshot 누락",
} as const;

export function StoragePanel() {
  const [pages, setPages] = useState<StorageInspection[]>([]);
  const [snapshots, setSnapshots] = useState<IcebergReference[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const current = pages[pages.length - 1];
  const cursor = current?.next_cursor ?? null;

  async function inspect(next: string | null = null) {
    setLoading(true); setError("");
    try {
      const url = next ? `/api/storage/inspection?cursor=${encodeURIComponent(next)}`
        : "/api/storage/inspection";
      const result = await request<StorageInspection>(url);
      setPages(previous => next ? [...previous, result] : [result]);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  async function inspectSnapshots() {
    setLoading(true); setError("");
    try {
      const result = await request<IcebergReference[]>("/api/storage/iceberg-references");
      setSnapshots(result);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void inspect(); }, []);

  const policy = current?.policy;
  const counts = pages.reduce((total, page) => {
    for (const status of Object.keys(page.counts)) {
      total[status] = (total[status] ?? 0) + (page.counts[status] ?? 0);
    }
    return total;
  }, {} as Record<string, number>);

  return <section className="panel" aria-label="저장소 상태">
    <div className="panel-head">
      <div><h3>Storage Health</h3>
        <p>객체 참조·보존 상태를 진단합니다. 이 화면과 API는 객체를 삭제하지 않습니다.</p>
      </div>
      <button className="secondary" disabled={loading} onClick={() => void inspect()}>
        객체 다시 검사
      </button>
    </div>
    {error && <p className="job-error" role="alert">검사 실패 · {error} · 삭제는 수행되지 않았습니다.</p>}
    {loading && <p role="status">저장소 상태를 확인하고 있습니다.</p>}
    {policy && <>
      <h4>적용 중인 보존 정책</h4>
      <p className="hint">환경 설정을 변경하면 다음 실행부터 반영됩니다. 안전 검증 조건은 해제할 수 없습니다.</p>
      <div className="cards">
        <div className="stat"><span>자동 삭제</span><strong>비활성화</strong></div>
        <div className="stat"><span>미참조 유예</span>
          <strong>{policy.values.grace_days}일</strong>
          <small>{policy.sources.grace_days === "environment" ? "환경변수 설정" : "시스템 기본값"}</small>
        </div>
        <div className="stat"><span>반복 관측</span>
          <strong>{policy.values.min_observations}회</strong>
          <small>{policy.values.observation_hours}시간 이상 간격</small>
        </div>
      </div>
      <details><summary>고급 정책과 설정 출처</summary>
        <table><thead><tr><th>설정</th><th>유효 값</th><th>출처</th></tr></thead>
          <tbody>{Object.entries(policy.values).map(([name, value]) =>
            <tr key={name}><td>{name}</td><td>{value}</td>
              <td>{policy.sources[name] === "environment" ? "환경변수" : "기본값"}</td></tr>)}
          </tbody></table>
        <p className="hint">정책은 서버 환경변수 SOS_OBJECT_*로 관리됩니다.
          이 화면에서는 설정을 변경하거나 삭제를 실행할 수 없습니다.</p>
      </details>
    </>}
    <h4>객체 대조 · 확인한 페이지 기준</h4>
    <div className="cards">
      {(["protected", "waiting", "candidate", "unknown"] as const).map(status =>
        <div className="stat" key={status}><span>{statusLabel[status]}</span>
          <strong>{counts[status] ?? 0}</strong></div>)}
    </div>
    <p className="hint">결과는 조회한 페이지의 관측값입니다. S3 목록을 계속 탐색해야 전체 규모를 알 수 있으며,
      등록되지 않은 객체는 안전을 위해 확인 필요로 분류합니다.</p>
    {pages.map((page, index) => <details key={index}>
      <summary>{page.scope} · {page.items.length}개 검사</summary>
      {page.items.length === 0 && <p className="muted">이 페이지에는 객체가 없습니다.</p>}
      <div className="job-list">{page.items.map(item =>
        <article key={item.location} className="job">
          <strong>{statusLabel[item.status]}</strong>
          <p className="muted">{item.reason}</p>
          <small>{item.location} · {item.size_bytes.toLocaleString()} B</small>
        </article>)}</div>
    </details>)}
    {cursor && <button className="secondary" disabled={loading} onClick={() => void inspect(cursor)}>
      다음 객체 페이지 확인 →
    </button>}
    <div className="panel-head"><div><h4>Iceberg Snapshot 참조</h4>
      <p>고정 Dataset이 가리키는 Snapshot 존재 여부를 Catalog에서 직접 확인합니다.</p></div>
      <button className="secondary" disabled={loading} onClick={() => void inspectSnapshots()}>
        Snapshot 검사
      </button></div>
    {snapshots && (snapshots.length === 0 ? <p className="muted">등록된 Iceberg 참조가 없습니다.</p>
      : <div className="job-list">{snapshots.map(item =>
        <article className="job" key={item.dataset_id}>
          <strong>{item.table_name} · {statusLabel[item.status]}</strong>
          <p className="muted">{item.reason}</p><small>Snapshot {item.snapshot_id}</small>
        </article>)}</div>)}
  </section>;
}
