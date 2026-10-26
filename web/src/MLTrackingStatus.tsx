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
        if (active) { setStatus(next); setError(""); }
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

  return <MLTrackingDetails status={status} error={error} />;
}

/** 조회 결과의 대기·재시도·완료와 통신 오류를 분리해 표시한다. */
export function MLTrackingDetails({ status, error }: {
  status: MLflowExportStatus | null; error: string;
}) {
  return <div className="steps" aria-label="MLflow 추적 상태">
    <strong>MLflow 실행 기록</strong>
    {status && <p role="status">
      {status.state === "Disabled"
        ? "MLflow 관리 기록이 비활성화됐습니다. 플랫폼 실행 결과는 보존됩니다."
        : status.state === "Succeeded"
          ? `MLflow 기록 완료 · Run ID ${status.run_id ?? "미확인"}`
          : status.state === "Retry"
            ? `MLflow 기록·모델 등록 실패 · 자동 재시도 대기 (시도 ${status.attempts}회)`
            : status.state === "Sending" ? "MLflow 기록·등록 전송 중" : "MLflow 기록 대기"}
    </p>}
    {status?.last_error_code && status.state === "Retry" &&
      <div className="pipeline-readiness-blocked" role="alert">
        <strong>모델이 아직 Registry에 등록되지 않았습니다.</strong>
        <p>오류 코드: {status.last_error_code}</p>
        <p>{status.last_error_code === "SOS-MLFLOW-HTTP-404-registered-models_get"
          ? "등록할 모델 이름을 조회하는 과정에서 MLflow가 404를 반환했습니다. 첫 등록 순서 또는 Registry 상태를 운영자가 확인해야 합니다."
          : "MLflow 등록 서버·모델 판본·응답 상태를 운영자가 확인해야 합니다."}
          {" "}학습 결과는 유지됩니다. 같은 학습을 새로 제출해 해결하려 하지 마세요.</p>
      </div>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
