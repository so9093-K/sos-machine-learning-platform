import type { RefObject } from "react";
import type { Dataset, UploadPolicy } from "./types";

type SnapshotChoice = { snapshot_id: string | number; timestamp_ms: number };
type Props = {
  datasets: Dataset[];
  loading?: boolean;
  readError?: boolean;
  selected: string;
  canWrite: boolean;
  busy: boolean;
  uploadRef: RefObject<HTMLInputElement | null>;
  uploadPolicy: UploadPolicy | null;
  onFile: (file: File) => void;
  onExample: () => void;
  onChoose: (id: string) => void;
  existingTableName: string;
  onChangeExistingName: (value: string) => void;
  snapshots: SnapshotChoice[];
  snapshotId: string;
  onSnapshotId: (id: string) => void;
  snapshotsLoading: boolean;
  onLoadSnapshots: () => void;
  onConnectExisting: () => void;
  hasMore: boolean;
  onLoadMore: () => void;
};

const mebibytes = (size: number) => (size / (1024 * 1024)).toFixed(2);

/** 데이터 등록·선택 조작은 데이터 영역에서만 제공한다. */
export function DataSourceSidebar(props: Props) {
  const {
    datasets, selected, canWrite, busy, loading = false, readError = false, uploadRef, uploadPolicy,
    onFile, onExample, onChoose, existingTableName, onChangeExistingName,
    snapshots, snapshotId, onSnapshotId, snapshotsLoading,
    onLoadSnapshots, onConnectExisting, hasMore, onLoadMore,
  } = props;
  return <div className="data-sidebar">
    <div className="section-title">DATASETS</div>
    <nav className="dataset-nav" aria-label="데이터셋 목록">
      {loading && <p className="muted" role="status">Dataset 조회 중…</p>}
      {!loading && !readError && datasets.length === 0 && <p className="muted">등록된 데이터가 없습니다.</p>}
      {datasets.map(item => <button key={item.id}
        className={`dataset ${selected === item.id ? "active" : ""}`}
        onClick={() => onChoose(item.id)}>
        <span className="dataset-name">{item.name}</span>
        <span className="dataset-kind">{item.kind === "iceberg" ? "◆ Iceberg" : "◫ File"}</span>
      </button>)}
    </nav>
    {hasMore && <button className="job-navigation secondary" disabled={busy} onClick={onLoadMore}>
      이전 데이터셋 더 불러오기
    </button>}
    <button className="job-navigation secondary" onClick={onExample}>예제 데이터</button>
    {canWrite && <details className="data-import-controls">
      <summary>데이터 추가·연결</summary>
      <div className="data-import-actions">
        <label className="upload" role="button" tabIndex={0}
          aria-label="CSV·Parquet·TSV·TXT 파일 업로드" aria-disabled={busy}
          onKeyDown={event => {
            if (busy || !["Enter", " "].includes(event.key)) return;
            event.preventDefault(); uploadRef.current?.click();
          }}>
          + 파일 가져오기 (CSV·Parquet·TSV·TXT)
          {uploadPolicy && <small>최대 {mebibytes(uploadPolicy.max_size_bytes)} MiB</small>}
          <input ref={uploadRef} type="file"
            accept={uploadPolicy?.supported_extensions.join(",") ?? ".csv,.parquet,.tsv,.txt"}
            disabled={busy}
            onChange={event => {
              const file = event.target.files?.[0];
              if (file) onFile(file);
              event.target.value = "";
            }} />
        </label>
        <div className="connect-existing">
          <label>기존 Iceberg 테이블
            <input aria-label="Iceberg 테이블 이름" value={existingTableName}
              onChange={event => onChangeExistingName(event.target.value)} placeholder="sensor_data" />
          </label>
          <button className="secondary" disabled={busy || snapshotsLoading || !existingTableName.trim()}
            onClick={onLoadSnapshots}>{snapshotsLoading ? "조회 중" : "Snapshot 조회"}</button>
          {snapshots.length > 0 && <label>사용할 Snapshot
            <select aria-label="Iceberg Snapshot 선택" value={snapshotId}
              onChange={event => onSnapshotId(event.target.value)}>
              {snapshots.map(item => <option key={item.snapshot_id} value={item.snapshot_id}>
                {item.snapshot_id} · {new Date(item.timestamp_ms).toLocaleString("ko-KR")}
              </option>)}
            </select>
          </label>}
          <button className="secondary" disabled={busy || !existingTableName.trim()}
            onClick={onConnectExisting}>
            {snapshotId ? "선택한 Snapshot 연결" : "최신 Snapshot 연결"}
          </button>
        </div>
      </div>
    </details>}
  </div>;
}
