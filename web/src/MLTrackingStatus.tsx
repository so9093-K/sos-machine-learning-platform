import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import type { MLflowExportStatus } from "./types";

type Props = { jobId: string };

const TERMINAL = new Set(["Disabled", "Succeeded"]);

/** Job 결과와 별개인 MLflow Run 기록 상태를 표시한다. 학습은 Registry 등록까지 포함한다. */
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
    <strong>MLflow 실행 기록</strong>
    {status && <p role="status">
      {status.state === "Disabled"
        ? "MLflow 관리 기록이 비활성화됐습니다. SOS 실행 결과는 보존됩니다."
        : status.state === "Succeeded"
          ? `MLflow 기록 완료 · Run ID ${status.run_id ?? "미확인"}`
          : status.state === "Retry"
            ? `MLflow 기록·등록 재시도 대기 · 시도 ${status.attempts}회`
            : status.state === "Sending" ? "외부 전송 중" : "MLflow 기록 대기"}
    </p>}
    {status?.last_error_code && status.state === "Retry" &&
      <p className="hint">MLflow 연동 오류 코드: {status.last_error_code}. SOS Job 결과는 바뀌지 않으며 학습 모델은 등록 전 사용할 수 없습니다.</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
