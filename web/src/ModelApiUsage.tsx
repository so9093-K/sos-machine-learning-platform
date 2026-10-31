import { useEffect, useRef, useState } from "react";
import { formatError, jsonPost } from "./api";
import { exampleValues, type PlaygroundRun } from "./ModelLabPlayground";
import { formatPlaygroundScore } from "./PlaygroundResult";
import type { ModelRevision, Prediction, ServingEndpoint } from "./types";

type Step = { title: string; result: string; command: string; note?: string };
type Flow = "online" | "dataset";

function CodeBlock({ code, label }: { code: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return <div className="api-code">
    <pre aria-label={label}><code>{code}</code></pre>
    <button type="button" className="ghost" onClick={() => {
      void navigator.clipboard?.writeText(code).then(() => {
        setCopied(true); setTimeout(() => setCopied(false), 1500);
      }).catch(() => setCopied(false));
    }}>{copied ? "복사됨" : "복사"}</button>
  </div>;
}

/** 온라인 추론 예시 입력의 출처. 실제로 계산한 입력 → 학습 기준값 → 값 없는 형식 순서로 고른다.
 *  기준값이 없는 모델에 임의 값을 채워 실행 가능한 예시처럼 보이지 않게 한다.
 */
function exampleInput(model: ModelRevision, lastRun?: PlaygroundRun) {
  if (lastRun) return {
    features: Object.fromEntries(model.features.map(name => [name, lastRun.features[name] ?? null])),
    source: `Playground에서 마지막으로 계산한 입력입니다(이상 점수 ${formatPlaygroundScore(lastRun.score)}). ` +
      "같은 판본에 같은 입력을 보내면 같은 점수를 받습니다.",
  };
  const reference = exampleValues(model);
  if (reference) return {
    features: Object.fromEntries(model.features.map(name => [name, Number(reference[name])])),
    source: "아래 값은 학습 중앙값으로 만든 합성 입력입니다(정수 항목 반올림). 값 직접 입력에서 계산하면 그 입력으로 바뀝니다.",
  };
  return {
    features: null,
    source: "이 모델에는 학습 기준값 기록이 없어 값을 채우지 않았습니다. <값>을 실제 숫자로 바꾸거나, " +
      "값 직접 입력에서 계산하면 그 입력으로 채워집니다.",
  };
}

/** Endpoint가 지금 이 판본으로 요청을 처리한다. 전환 대상으로만 준비 중이면 아직 다른 판본이 계산한다. */
export function servesModel(endpoint: ServingEndpoint | null, modelId: string): boolean {
  return Boolean(endpoint?.ready && endpoint.active_model_revision_id === modelId);
}

/** 화면에서 해본 계산을 외부 시스템에서 같은 계약으로 요청하는 방법.
 *
 *  한 건씩 바로 받는 온라인 추론(서빙 Endpoint)과 Dataset 전체 점수(비동기 Job)를 안내한다.
 *  선택한 모델의 Endpoint, 입력 구조가 확인된 Dataset ID, Playground에서 계산한 입력을 채운다.
 */
export function ModelApiUsage({ model, datasetId, lastRun, endpoint, endpointError, onRecheckEndpoint, canInfer }: {
  model: ModelRevision; datasetId?: string; lastRun?: PlaygroundRun;
  /** 이 판본을 서빙하거나 이 판본으로 전환 중인 Endpoint. 없으면 온라인 API가 아직 없다. */
  endpoint: ServingEndpoint | null;
  /** Endpoint 상태를 확인하지 못한 이유. */
  endpointError: string;
  onRecheckEndpoint: () => void;
  canInfer: boolean;
}) {
  const [flow, setFlow] = useState<Flow>("online");
  const [trial, setTrial] = useState<{ busy: boolean; error: string; result: Prediction | null }>(
    { busy: false, error: "", result: null });
  // 입력 실험으로 계산하면 그 입력을 재현하는 온라인 예시를 바로 보여준다.
  useEffect(() => { if (lastRun) setFlow("online"); }, [lastRun]);
  const origin = typeof window === "undefined" ? "http://127.0.0.1:9601" : window.location.origin;
  const auth = `-H "Authorization: Bearer $SOS_TOKEN"`;
  const input = exampleInput(model, lastRun);
  const ready = servesModel(endpoint, model.id);
  // 모델·Endpoint·입력이 바뀌면 이전 응답을 지우고, 이전 요청의 늦은 응답이 현재 결과를 덮지 않게 한다.
  const trialKey = `${model.id}|${endpoint?.id ?? ""}|${JSON.stringify(input.features)}`;
  const trialSeq = useRef(0);
  useEffect(() => {
    trialSeq.current += 1;
    setTrial({ busy: false, error: "", result: null });
  }, [trialKey]);
  const onlineBody = input.features ? JSON.stringify({ inputs: [input.features] })
    : `{"inputs": [{${model.features.map(name => `"${name}": <값>`).join(", ")}}]}`;
  const onlineSteps: Step[] = endpoint ? [
    { title: "온라인 추론 요청", result: `관측값마다 모델 입력 Feature ${model.features.length}개를 보냅니다(한 번에 최대 100건). ${input.source}`,
      command: `curl -X POST "$SOS_API/api/serving/endpoints/${endpoint.id}/predict" ${auth} \\\n` +
        `  -H "Content-Type: application/json" \\\n  -d '${onlineBody}'`,
      note: "응답의 outputs[i].anomaly_score가 i번째 관측의 이상 점수이고 model_revision_id는 계산한 판본입니다. 요청은 Job으로 남지 않습니다." },
  ] : [];
  const datasetSteps: Step[] = [
    { title: "데이터 등록", result: "응답의 id가 Dataset ID입니다. 이미 등록한 Dataset이면 건너뜁니다.",
      command: `curl -X POST "$SOS_API/api/datasets/files" ${auth} \\\n  -F "file=@activity.parquet"` },
    { title: "점수 계산 요청", result: "응답의 id로 진행 상태를 확인합니다. Idempotency-Key는 요청마다 새 값을 쓰고, 응답을 받지 못해 같은 요청을 다시 보낼 때만 같은 값을 씁니다.",
      command: `curl -X POST "$SOS_API/api/ml/scoring/jobs" ${auth} \\\n  -H "Idempotency-Key: activity-score-001" -H "Content-Type: application/json" \\\n` +
        `  -d '{"model_revision_id": "${model.id}", "dataset_id": "${datasetId || "$DATASET_ID"}"}'`,
      note: datasetId ? undefined : "$DATASET_ID에는 이 모델의 입력 조건과 맞는 Dataset의 ID를 넣습니다." },
    { title: "완료 확인", result: "status가 Succeeded이면 result.scoring_result_id로 결과를 조회합니다.",
      command: `curl "$SOS_API/api/ml/scoring/jobs/$JOB_ID" ${auth}` },
    { title: "점수 상위 관측 조회", result: "items는 anomaly_score가 높은 순서이며 관측의 입력값과 관측 시각을 함께 줍니다. next_cursor로 다음 페이지를 받습니다.",
      command: `curl "$SOS_API/api/ml/scoring/results/$RESULT_ID/observations?limit=25" ${auth}` },
  ];
  const steps = flow === "online" ? onlineSteps : datasetSteps;
  const trialScore = trial.result?.outputs[0]?.anomaly_score;

  async function sendTrial() {
    if (!endpoint || !input.features) return;
    const seq = ++trialSeq.current;
    setTrial({ busy: true, error: "", result: null });
    try {
      const result = await jsonPost<Prediction>(
        `/api/serving/endpoints/${encodeURIComponent(endpoint.id)}/predict`, { inputs: [input.features] });
      if (seq === trialSeq.current) setTrial({ busy: false, error: "", result });
    } catch (cause) {
      if (seq !== trialSeq.current) return;
      setTrial({ busy: false, error: formatError(cause), result: null });
      // 요청 사이에 준비 상태가 바뀌었을 수 있으므로 Endpoint 상태를 다시 확인한다.
      onRecheckEndpoint();
    }
  }

  return <section className="panel api-usage" aria-labelledby="api-usage-heading">
    <div className="panel-head"><div>
      <h2 id="api-usage-heading">서비스 연동</h2>
      <p>화면에서 해본 계산을 API로 그대로 요청합니다. 결과를 검토 대기열의 우선순위나 정기 점검 알림에 활용할 수 있습니다.</p>
    </div></div>
    <div className="api-usage-flows" role="group" aria-label="연결 방식">
      <button type="button" className="secondary" aria-pressed={flow === "online"}
        onClick={() => setFlow("online")}>온라인 추론</button>
      <button type="button" className="secondary" aria-pressed={flow === "dataset"}
        onClick={() => setFlow("dataset")}>Dataset 전체 점수</button>
    </div>
    {flow === "online" && !endpoint && <div className="api-online-state">
      <p className="muted" role={endpointError ? "alert" : undefined}>{endpointError
        ? `온라인 API 상태를 확인하지 못했습니다. ${endpointError}`
        : "이 모델은 아직 온라인 API로 배포되지 않았습니다. 모델 공급 과정에서 배포되면 이곳에서 호출 방법과 실제 요청을 제공합니다. " +
          "지금은 Dataset 전체 점수를 API로 요청할 수 있습니다."}</p>
      <button type="button" className="secondary" onClick={onRecheckEndpoint}>다시 확인</button>
    </div>}
    {flow === "online" && endpoint && !ready && <p className="muted" role="status">
      온라인 API를 준비하고 있습니다. 준비되면 이 화면이 자동으로 바뀌고 아래 요청으로 바로 계산할 수 있습니다.</p>}
    {steps.length > 0 && <>
      <CodeBlock label="API 접속 정보" code={`export SOS_API="${origin}"\nexport SOS_TOKEN="<발급받은 토큰>"`} />
      <ol className="api-steps">{steps.map(step => <li key={step.title}>
        <strong>{step.title}</strong>
        <CodeBlock label={step.title} code={step.command} />
        <p className="hint">{step.result}{step.note ? ` ${step.note}` : ""}</p>
      </li>)}</ol>
    </>}
    {flow === "online" && endpoint && canInfer && input.features && <div className="api-trial">
      <button type="button" className="secondary" disabled={!ready || trial.busy}
        onClick={() => void sendTrial()}>{trial.busy ? "요청 중…" : "이 요청 보내기"}</button>
      {trial.result && typeof trialScore === "number" && <p role="status">
        응답 · 이상 점수 <strong>{formatPlaygroundScore(trialScore)}</strong> · 판본 {trial.result.model_revision_id.slice(0, 8)}
        {lastRun && lastRun.modelRevisionId === trial.result.model_revision_id && (trialScore === lastRun.score
          ? " · Playground 계산과 같은 점수입니다."
          : ` · Playground 계산(${formatPlaygroundScore(lastRun.score)})과 다릅니다.`)}</p>}
      {trial.error && <p role="alert">요청 실패 · {trial.error}</p>}
    </div>}
    <a className="api-reference-link" href="/docs" target="_blank" rel="noreferrer">API Reference · 요청·응답 전체 형식 ↗</a>
  </section>;
}
