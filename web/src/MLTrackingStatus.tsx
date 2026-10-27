import { useEffect, useState } from "react";
import { ApiError, formatError, request } from "./api";
import { mlflowRunUrl } from "./operator-tools";
import type { MLflowExportStatus } from "./types";

type Props = { jobId: string; operatorLinks?: boolean };

const TERMINAL = new Set(["Disabled", "Succeeded"]);

/** Job 결과와 별개인 MLflow Run 기록 상태를 표시한다. 학습은 Registry 등록까지 포함한다. */
export function MLTrackingStatus({ jobId, operatorLinks = false }: Props) {
  const [status, setStatus] = useState<MLflowExportStatus | null>(null);
  const [error, setError] = useState("");
  const [retryRevision, setRetryRevision] = useState(0);

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
          // 인증·권한·존재하지 않는 Job은 재시도해도 해결되지 않는다.
          const permanent = exc instanceof ApiError && exc.status >= 400 &&
            exc.status < 500 && exc.status !== 429;
          if (permanent) setStatus(null);
          else timer = setTimeout(() => void poll(), 5000);
        }
      }
    }
    void poll();
    return () => { active = false; if (timer) clearTimeout(timer); };
  }, [jobId, retryRevision]);

  return <>
    <MLTrackingDetails status={status} error={error} />
    {operatorLinks && status?.run_id && <p className="hint">
      <a href={mlflowRunUrl(status.run_id)} target="_blank" rel="noopener noreferrer">
        MLflow에서 이 Run 열기 ↗</a>
    </p>}
    {error && <button type="button" className="secondary"
      onClick={() => { setError(""); setRetryRevision(value => value + 1); }}>
      MLflow 기록 다시 조회
    </button>}
  </>;
}

/** 조회 결과의 대기·재시도·완료와 통신 오류를 분리해 표시한다. */
export function MLTrackingDetails({ status, error }: {
  status: MLflowExportStatus | null; error: string;
}) {
  const retryDate = status?.state === "Retry" && status.retry_after
    ? new Date(status.retry_after) : null;
  const retryAt = retryDate && Number.isFinite(retryDate.getTime())
    ? retryDate.toLocaleString("ko-KR") : null;
  const registry404 = status?.last_error_code === "SOS-MLFLOW-HTTP-404-registered-models_get";
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
    {status?.state === "Retry" && <div className="pipeline-readiness-blocked" role="alert">
      <strong>{registry404 ? "모델 Registry 등록이 완료되지 않았습니다." :
        "MLflow 기록 또는 모델 등록에 실패했습니다."}</strong>
      {status.last_error_code && <p>오류 코드: {status.last_error_code}</p>}
      {retryAt && <p>다음 자동 재시도 기준 시각: {retryAt}</p>}
      <p>{registry404
        ? "등록할 모델을 조회하는 과정에서 MLflow가 404를 반환했습니다. 신규 모델 생성 순서 또는 Registry 상태를 운영자가 확인해야 합니다."
        : "MLflow 전송 또는 모델 Registry 상태를 확인해야 합니다."}
        {" "}기존 Job 결과는 보존됩니다. 동일 학습을 다시 제출해 해결하려 하지 마세요.</p>
      {status.run_id && <p className="hint">연결된 MLflow Run ID: <code>{status.run_id}</code></p>}
    </div>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
