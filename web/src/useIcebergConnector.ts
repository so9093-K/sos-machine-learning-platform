import { useRef, useState } from "react";
import { jsonPost, request, submitJob } from "./api";
import type { Dataset, Job, Snapshot } from "./types";

/** Iceberg 조회·연결과 선택 Dataset 적재를 독립 상태로 관리한다. */
export function useIcebergConnector({
  selected, onError, withFeedback, onConnected, onImported,
}: {
  selected: string;
  onError: (cause: unknown) => void;
  withFeedback: (action: () => Promise<void>) => Promise<void>;
  onConnected: (dataset: Dataset, success: string, stale: string) => Promise<void>;
  onImported: (job: Job, message: string) => Promise<void>;
}) {
  const [existingTableName, setExistingTableName] = useState("");
  const [importTableName, setImportTableName] = useState("");
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [snapshotId, setSnapshotId] = useState("");
  const [snapshotsLoading, setSnapshotsLoading] = useState(false);
  const snapshotRequestVersion = useRef(0);

  function changeExistingTableName(value: string) {
    ++snapshotRequestVersion.current;
    setExistingTableName(value);
    setSnapshots([]); setSnapshotId(""); setSnapshotsLoading(false);
  }
  async function loadSnapshots() {
    if (!existingTableName.trim()) return;
    const version = ++snapshotRequestVersion.current;
    setSnapshotsLoading(true);
    onError(null);
    try {
      const items = await request<Snapshot[]>(
        `/api/iceberg/${encodeURIComponent(existingTableName)}/snapshots`);
      if (version !== snapshotRequestVersion.current) return;
      setSnapshots(items);
      setSnapshotId(items[0]?.snapshot_id ?? "");
    } catch (cause) {
      if (version !== snapshotRequestVersion.current) return;
      setSnapshots([]); setSnapshotId("");
      onError(cause);
    } finally {
      if (version === snapshotRequestVersion.current) setSnapshotsLoading(false);
    }
  }
  async function connectExisting() {
    await withFeedback(async () => {
      const data = await jsonPost<Dataset>("/api/datasets/iceberg",
        { table_name: existingTableName, ...(snapshotId ? { snapshot_id: snapshotId } : {}) });
      await onConnected(data,
        "기존 Iceberg 테이블의 스냅샷을 연결했습니다.",
        "Iceberg 스냅샷은 정상 연결됐습니다. 목록 새로고침에 실패했지만 다시 연결할 필요는 없습니다.");
    });
  }
  async function importIceberg() {
    if (!selected) return;
    await withFeedback(async () => {
      const job = await submitJob<Job>("/api/iceberg/import",
        { dataset_id: selected, table_name: importTableName });
      await onImported(job, "Iceberg 적재 Job이 등록되었습니다. 완료 후 데이터셋 목록에서 확인할 수 있습니다.");
    });
  }

  return {
    existingTableName, changeExistingTableName, importTableName, setImportTableName,
    snapshots, snapshotId, setSnapshotId, snapshotsLoading,
    loadSnapshots, connectExisting, importIceberg,
  };
}
