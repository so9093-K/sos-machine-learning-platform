import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import type { MLflowExportStatus } from "./types";

type Props = { jobId: string };

const TERMINAL = new Set(["Disabled", "Succeeded"]);

/** SOS Job과 독립적인 외부 추적 상태를 표시한다. */
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
    <strong>MLflow Tracking (선택 사항)</strong>
    {status && <p role="status">
      {status.state === "Disabled"
        ? "비활성: SOS 결과는 정상적으로 보존됩니다."
        : status.state === "Succeeded"
          ? `전송 완료 · Run ID ${status.run_id ?? "미확인"}`
          : status.state === "Retry"
            ? `전송 재시도 대기 · 시도 ${status.attempts}회`
            : status.state === "Sending" ? "외부 전송 중" : "외부 전송 대기"}
    </p>}
    {status?.last_error_code && status.state === "Retry" &&
      <p className="hint">Tracking 오류 코드: {status.last_error_code}. 학습·평가 결과의 성공 상태에는 영향을 주지 않습니다.</p>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
