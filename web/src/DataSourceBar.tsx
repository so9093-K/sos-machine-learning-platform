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
  /** URL로 지정했지만 목록에 아직 없는 Dataset의 표시 이름. */
  pendingLabel?: string;
};

const mebibytes = (size: number) => (size / (1024 * 1024)).toFixed(2);

/** 데이터 영역 상단의 Dataset 선택과 등록·연결. 좁은 화면에서도 본문에서 바로 쓴다. */
export function DataSourceBar(props: Props) {
  const {
    datasets, selected, canWrite, busy, loading = false, readError = false, uploadRef, uploadPolicy,
    onFile, onExample, onChoose, existingTableName, onChangeExistingName,
    snapshots, snapshotId, onSnapshotId, snapshotsLoading,
    onLoadSnapshots, onConnectExisting, hasMore, onLoadMore, pendingLabel,
  } = props;
  return <div className="data-source-bar">
    {loading && datasets.length === 0 && <p className="muted" role="status">Dataset 조회 중…</p>}
    {!loading && !readError && datasets.length === 0 && <p className="muted">등록된 Dataset이 없습니다.</p>}
    {datasets.length > 0 && <label className="data-source-select">Dataset
      <select aria-label="현재 Dataset" value={selected} onChange={event => onChoose(event.target.value)}>
        <option value="">Dataset 선택</option>
        {pendingLabel && <option value={selected}>{pendingLabel}</option>}
        {datasets.map(item => <option key={item.id} value={item.id}>
          {item.name}{item.kind === "iceberg" ? " · Iceberg" : ""}</option>)}
      </select>
    </label>}
    {hasMore && <button type="button" className="secondary" disabled={busy} onClick={onLoadMore}>Dataset 더 보기</button>}
    {canWrite && <button type="button" className="secondary" onClick={onExample}>예제 데이터</button>}
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
