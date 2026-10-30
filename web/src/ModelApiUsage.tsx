import { useEffect, useState } from "react";
import { exampleValues, type PlaygroundRun } from "./ModelLabPlayground";
import { formatPlaygroundScore } from "./PlaygroundResult";
import type { ModelRevision } from "./types";

type Step = { title: string; result: string; command: string; note?: string };

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

/** 단일 입력 예시의 출처. 실제로 계산한 입력 → 학습 기준값 → 값 없는 형식 순서로 고른다.
 *  기준값이 없는 모델에 임의 값을 채워 실행 가능한 예시처럼 보이지 않게 한다.
 */
function singleInputExample(model: ModelRevision, lastRun?: PlaygroundRun) {
  if (lastRun) return {
    body: JSON.stringify({ model_revision_id: model.id,
      features: Object.fromEntries(model.features.map(name => [name, lastRun.features[name] ?? null])) }),
    source: `Playground에서 마지막으로 계산한 입력입니다(이상 점수 ${formatPlaygroundScore(lastRun.score)}). ` +
      "같은 모델에 같은 입력을 보내면 같은 점수를 받습니다.",
  };
  const reference = exampleValues(model);
  if (reference) return {
    body: JSON.stringify({ model_revision_id: model.id,
      features: Object.fromEntries(model.features.map(name => [name, Number(reference[name])])) }),
    source: "아래 값은 이 모델의 학습 기준값입니다. Playground의 값 직접 입력에서 계산하면 그 입력으로 바뀝니다.",
  };
  return {
    body: `{"model_revision_id": "${model.id}", "features": {${model.features.map(name => `"${name}": <값>`).join(", ")}}}`,
    source: "이 모델에는 학습 기준값 기록이 없어 값을 채우지 않았습니다. <값>을 실제 숫자로 바꾸거나, " +
      "Playground의 값 직접 입력에서 계산하면 그 입력으로 채워집니다.",
  };
}

/** 화면에서 해본 점수 계산을 외부 시스템에서 같은 계약으로 요청하는 방법.
 *
 *  선택한 모델 ID, 입력 구조가 확인된 Dataset ID, Playground에서 계산한 입력을 채운다.
 */
export function ModelApiUsage({ model, datasetId, lastRun }: {
  model: ModelRevision; datasetId?: string; lastRun?: PlaygroundRun;
}) {
  const [flow, setFlow] = useState<"dataset" | "single">("dataset");
  // 값 직접 입력으로 계산하면 그 입력을 재현하는 연동 예시를 바로 보여준다.
  useEffect(() => { if (lastRun) setFlow("single"); }, [lastRun]);
  const origin = typeof window === "undefined" ? "http://127.0.0.1:9601" : window.location.origin;
  const auth = `-H "Authorization: Bearer $SOS_TOKEN"`;
  const single = singleInputExample(model, lastRun);
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
  const singleSteps: Step[] = [
    { title: "관측 1건 점수 요청", result: `모델 입력 Feature ${model.features.length}개를 모두 보냅니다. ${single.source}`,
      command: `curl -X POST "$SOS_API/api/ml/playground/jobs" ${auth} \\\n  -H "Idempotency-Key: activity-check-001" -H "Content-Type: application/json" \\\n` +
        `  -d '${single.body}'` },
    { title: "점수 확인", result: "status가 Succeeded이면 result.anomaly_score가 이 관측의 이상 점수입니다.",
      command: `curl "$SOS_API/api/ml/playground/jobs/$JOB_ID" ${auth}` },
  ];
  const steps = flow === "dataset" ? datasetSteps : singleSteps;
  return <section className="panel api-usage" aria-labelledby="api-usage-heading">
    <div className="panel-head"><div>
      <h2 id="api-usage-heading">서비스 연동</h2>
      <p>화면에서 해본 계산을 API로 그대로 요청합니다. 결과를 검토 대기열의 우선순위나 정기 점검 알림에 활용할 수 있습니다.</p>
    </div></div>
    <div className="api-usage-flows" role="group" aria-label="연결 방식">
      <button type="button" className="secondary" aria-pressed={flow === "dataset"}
        onClick={() => setFlow("dataset")}>Dataset 전체 점수</button>
      <button type="button" className="secondary" aria-pressed={flow === "single"}
        onClick={() => setFlow("single")}>관측 1건 즉시 점수</button>
    </div>
    <CodeBlock label="API 접속 정보" code={`export SOS_API="${origin}"\nexport SOS_TOKEN="<발급받은 토큰>"`} />
    <ol className="api-steps">{steps.map(step => <li key={step.title}>
      <strong>{step.title}</strong>
      <CodeBlock label={step.title} code={step.command} />
      <p className="hint">{step.result}{step.note ? ` ${step.note}` : ""}</p>
    </li>)}</ol>
    <a className="api-reference-link" href="/docs" target="_blank" rel="noreferrer">API Reference · 요청·응답 전체 형식 ↗</a>
  </section>;
}
