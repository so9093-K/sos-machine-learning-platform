import { useEffect, useState } from "react";
import { formatError, request, uploadFile } from "./api";
import type { Dataset, UploadPolicy } from "./types";

type UploadProgress = {
  name: string; loaded: number; total: number;
  phase: "transferring" | "registering";
};
const mebibytes = (size: number) => (size / (1024 * 1024)).toFixed(2);

/** 파일 Dataset 등록 흐름. 정책 검사·전송 진행·구분 텍스트 확인을 한곳에서 관리한다.
 *
 *  HTTP 201이 등록 성공의 정본이며, 이후 화면 갱신은 onRegistered가 맡는다.
 */
export function useDatasetUpload({ canWrite, withFeedback, onRejected, onRegistered }: {
  canWrite: boolean;
  withFeedback: (action: () => Promise<void>) => Promise<void>;
  onRejected: (message: string) => void;
  onRegistered: (data: Dataset) => Promise<void>;
}) {
  const [policy, setPolicy] = useState<UploadPolicy | null>(null);
  const [policyError, setPolicyError] = useState("");
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [pendingDelimited, setPendingDelimited] = useState<File | null>(null);

  useEffect(() => {
    if (!canWrite) return;
    request<UploadPolicy>("/api/datasets/upload-policy")
      .then(value => { setPolicy(value); setPolicyError(""); })
      .catch(e => setPolicyError(formatError(e)));
  }, [canWrite]);

  async function upload(file: File) {
    if (policy && !policy.supported_extensions.some(ext =>
      file.name.toLowerCase().endsWith(ext.toLowerCase()))) {
      onRejected("지원하지 않는 파일 형식입니다. CSV·Parquet 또는 탭으로 구분된 TSV·TXT를 선택하세요.");
      return;
    }
    if (policy && file.size > policy.max_size_bytes) {
      onRejected(`파일 크기 ${mebibytes(file.size)} MiB가 최대 ${mebibytes(policy.max_size_bytes)} MiB를 초과했습니다.`);
      return;
    }
    setProgress({ name: file.name, loaded: 0, total: file.size, phase: "transferring" });
    await withFeedback(async () => {
      const data = await uploadFile<Dataset>(
        "/api/datasets/files", file,
        (loaded, total) => setProgress(previous => previous ? { ...previous, loaded, total } : null),
        () => setProgress(previous => previous ? { ...previous, phase: "registering" } : null),
      );
      await onRegistered(data);
      // 등록을 확정한 뒤에만 입력 확인 화면을 닫는다. 4xx·네트워크 오류는 수정·재시도 가능해야 한다.
      setPendingDelimited(null);
    });
    setProgress(null);
  }

  /** 구분 텍스트는 헤더 유무를 확인한 뒤 등록하고, 나머지 형식은 바로 등록한다. */
  function choose(file: File): "confirm" | "upload" {
    if (/\.(tsv|txt)$/i.test(file.name)) { setPendingDelimited(file); return "confirm"; }
    void upload(file);
    return "upload";
  }

  return { policy, policyError, progress, pendingDelimited, upload, choose,
    cancelDelimited: () => setPendingDelimited(null) };
}

export function UploadProgressStatus({ progress }: { progress: UploadProgress }) {
  return <div className="upload-status" role="status" aria-live="polite">
    <strong>{progress.name}</strong>
    <span>{progress.phase === "transferring"
      ? `전송 중 · ${mebibytes(progress.loaded)} / ${mebibytes(progress.total)} MiB`
      : "전송 완료 · 서버에서 검증하고 Dataset을 등록하고 있습니다."}</span>
    {progress.phase === "transferring" && progress.total > 0 &&
      <progress max={progress.total} value={progress.loaded} aria-label="파일 전송 진행률" />}
  </div>;
}
