import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import type { MLflowExportStatus } from "./types";

type Props = { jobId: string };

const TERMINAL = new Set(["Disabled", "Succeeded"]);

/** Job 성공과 별개인 MLflow 관리·모델 등록 상태를 표시한다. */
export function MLTrackingStatus({ jobId }: Props) {
  const [status, setStatus] = useState<MLflowExportStatus | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const next = await request<MLflowExportStatus>(
          `/api/ml/tracking/jobs/${encodeURIComponent(jobId)}`,
        );
        if (active) setStatus(next);
        if (active && !TERMINAL.has(next.state)) timer = setTimeout(() => void poll(), 5000);
      } catch (exc) {
        if (active) {
          setError(formatError(exc));
          timer = setTimeout(() => void poll(), 5000);
        }
      }
    }
    void poll();
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [jobId]);

  return <div className="steps" aria-label="MLflow 추적 상태">
    <strong>MLflow 실행 기록 및 모델 등록</strong>
    {status && <p role="status">
      {status.state === "Disabled"
        ? "관리 서버가 연결되지 않았습니다. 학습 결과는 보존되지만 모델 등록·사용은 불가능합니다."
        : status.state === "Succeeded"
          ? `MLflow 등록 완료 · Run ID ${status.run_id ?? "미확인"}`
          : status.state === "Retry"
            ? `기록·모델 등록 재시도 대기 · 시도 ${status.attempts}회`
            : status.state === "Sending" ? "외부 전송 중" : "MLflow 등록 대기"}
    </p>}
    {status?.last_error_code && status.state === "Retry" &&
      <p className="hint">MLflow 등록 오류 코드: {status.last_error_code}. 학습 성공은 유지되지만 등록 전 모델은 사용할 수 없습니다.</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
