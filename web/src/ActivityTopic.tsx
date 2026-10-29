import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import { activityFeatureLabel } from "./ActivityFeatureLabels";
import { activityFeatureGroups } from "./ActivityFeatureGroups";
import { ModelCatalog } from "./ModelCatalog";
import { isActivityModelCompatible } from "./ModelLabTaxonomy";
import type { PipelineDefinition } from "./types";

const DEFINITION_ID = "activity-anomaly-if";

/** 분석 주제의 목적·입력 조건·해석을 먼저 보이고, 입력 계약이 맞는 제공 모델로 연결한다.
 *  모델 적용 자체는 공통 모델 적용 화면에서 수행한다.
 */
export function ActivityTopic({ canRun, datasetId, datasetName, onClearDataset, onOpenModel, onScoreModel,
  onOpenPipelines }: {
  canRun: boolean;
  datasetId?: string;
  datasetName?: string;
  onClearDataset: () => void;
  onOpenModel: (id: string) => void;
  onScoreModel: (id: string) => void;
  onOpenPipelines?: () => void;
}) {
  const [definition, setDefinition] = useState<PipelineDefinition | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setError("");
    void request<PipelineDefinition[]>("/api/pipelines").then(items => {
      const found = items.find(item => item.id === DEFINITION_ID);
      if (!active) return;
      if (found) setDefinition(found); else setError("활동 이상 탐지 입력 계약을 찾을 수 없습니다.");
    }).catch(cause => { if (active) setError(formatError(cause)); });
    return () => { active = false; };
  }, [retry]);
  const required = new Set(definition?.required_features ?? []);
  const grouped = activityFeatureGroups.map(group => ({
    ...group, features: group.features.filter(name => required.has(name)),
  })).filter(group => group.features.length > 0);
  const ungrouped = [...required].filter(name =>
    !activityFeatureGroups.some(group => (group.features as readonly string[]).includes(name)));
  return <div className="activity-topic">
    <section className="panel" aria-labelledby="activity-topic-heading">
      <h2 id="activity-topic-heading">분석 개요</h2>
      <p>사용자·호스트 단위로 집계한 활동 Feature에서 평소 패턴과 다른 관측값을 찾습니다.
        제공된 모델을 내 Dataset에 적용하면 관측값마다 이상 점수를 계산하고, 점수가 높은 관측값의 입력과 모델 반응을 조사할 수 있습니다.</p>
      <dl className="evidence-facts">
        <div><dt>모델 적용 입력</dt><dd>{definition
          ? `모델이 요구하는 숫자형 활동 Feature ${definition.required_features.length}개와 기록된 자료형 · 관측 시각 컬럼은 선택`
          : "입력 조건 확인 중"}</dd></div>
        {onOpenPipelines && definition && <div><dt>새 모델 학습 입력</dt>
          <dd>{`위 Feature와 시간 분할용 UTC 시각 컬럼 ${definition.required_time_column} · 등록 파이프라인 실행 조건`}</dd></div>}
        <div><dt>결과</dt><dd>관측값별 이상 점수 · 점수 분포 · 관측값 입력과 기여도</dd></div>
      </dl>
      {definition && <details className="activity-topic-features">
        <summary>필수 Feature {definition.required_features.length}개</summary>
        {grouped.map(group => <div key={group.id}>
          <strong>{group.title} · {group.features.length}개</strong>
          <p>{group.features.map(activityFeatureLabel).join(" · ")}</p>
        </div>)}
        {ungrouped.length > 0 && <div><strong>그 밖의 Feature · {ungrouped.length}개</strong>
          <p>{ungrouped.map(activityFeatureLabel).join(" · ")}</p></div>}
      </details>}
      <p className="hint">점수는 학습 데이터와 얼마나 다른지를 나타내며 보안 사고의 확정이나 발생 확률이 아닙니다.
        원시 로그에서 Feature를 자동으로 만들지 않습니다.</p>
      {error && <div role="alert"><p>입력 조건 조회 실패 · {error}</p>
        <button type="button" className="secondary" onClick={() => setRetry(value => value + 1)}>다시 조회</button></div>}
      {onOpenPipelines && <button type="button" className="secondary" onClick={onOpenPipelines}>등록 파이프라인 운영 →</button>}
    </section>
    {definition && <ModelCatalog title="활동 이상 탐지 모델" canRun={canRun}
      accepts={model => isActivityModelCompatible(model, definition)}
      emptyMessage="입력 계약이 맞는 제공 모델이 아직 없습니다. 모델이 제공되면 이곳에서 선택해 데이터에 적용할 수 있습니다."
      datasetId={datasetId} datasetName={datasetName} onClearDataset={onClearDataset}
      onOpenModel={onOpenModel} onScoreModel={onScoreModel} />}
  </div>;
}
