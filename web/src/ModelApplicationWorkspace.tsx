import { useEffect, useRef, useState } from "react";
import { MLScoringPanel } from "./MLScoringPanel";
import { ModelLabPlayground, observationInputForPlayground, type ObservationExperiment,
  type PlaygroundRun } from "./ModelLabPlayground";
import { activityFeatureLabel } from "./ActivityFeatureLabels";
import type { MLResourceKind } from "./PlatformNavigation";
import type { Dataset, ModelRevision } from "./types";

/** 진입 위치와 관계없이 같은 모델 적용·관측값 실험·결과 복귀 경험을 사용한다. */
export function ModelApplicationWorkspace({ selectedDatasetId, onSelectDataset, model, datasets, canRun, canCancel, onOpenResource,
  hasMoreDatasets = false, onLoadMoreDatasets, canUpload = false, onDatasetRegistered, onResultViewChange,
  onInputReadyChange, onPlaygroundRun, datasetAnalysisRequest = 0 }: {
  selectedDatasetId?: string;
  onSelectDataset?: (id: string) => void;
  model: ModelRevision; datasets: Dataset[]; canRun: boolean; canCancel: boolean;
  onOpenResource: (kind: MLResourceKind, id: string) => void;
  hasMoreDatasets?: boolean; onLoadMoreDatasets?: () => Promise<void>;
  canUpload?: boolean; onDatasetRegistered?: (dataset: Dataset) => void;
  onResultViewChange?: (visible: boolean) => void;
  onInputReadyChange?: (datasetId: string, ready: boolean) => void;
  onPlaygroundRun?: (run: PlaygroundRun) => void;
  /** 동일 Dataset 재선택도 분석 화면을 여는 명시적 요청으로 처리한다. */
  datasetAnalysisRequest?: number;
}) {
  const [usage, setUsage] = useState<"dataset" | "playground">(selectedDatasetId ? "dataset" : "playground");
  useEffect(() => { if (selectedDatasetId) setUsage("dataset"); }, [selectedDatasetId, datasetAnalysisRequest]);
  const [observationInput, setObservationInput] = useState<ObservationExperiment | null>(null);
  const [resultVisible, setResultVisible] = useState(false);
  const requestNumber = useRef(0);
  const playgroundPanel = useRef<HTMLDivElement>(null);
  const datasetPanel = useRef<HTMLDivElement>(null);
  const resultHandler = useRef(onResultViewChange);
  resultHandler.current = onResultViewChange;
  const focused = usage === "dataset" && resultVisible;
  useEffect(() => { resultHandler.current?.(focused); }, [focused]);
  useEffect(() => {
    if (usage !== "playground" || !observationInput) return;
    const frame = requestAnimationFrame(() => {
      const target = playgroundPanel.current?.querySelector<HTMLElement>(".model-lab-playground");
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    });
    return () => cancelAnimationFrame(frame);
  }, [usage, observationInput?.id]);
  function backToObservation() {
    setUsage("dataset");
    requestAnimationFrame(() => {
      const target = datasetPanel.current?.querySelector<HTMLElement>(".scoring-observation-detail");
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    });
  }
  function tryObservation(features: Record<string, number | null>, rowIndex: number, score: number,
    scoringResultId: string, modelRevisionId: string): string | null {
    if (modelRevisionId !== model.id) return "선택한 관측값과 모델 판본이 일치하지 않습니다.";
    const prepared = observationInputForPlayground(model, features);
    if (!prepared.fields) return `실험에 사용할 수 없는 입력 ${prepared.invalid.length}개: ` +
      prepared.invalid.slice(0, 3).map(activityFeatureLabel).join(", ");
    setObservationInput({ id: `${scoringResultId}:${rowIndex}:${++requestNumber.current}`,
      modelRevisionId, scoringResultId, rowIndex, originalScore: score, fields: prepared.fields });
    setUsage("playground");
    return null;
  }
  return <div className={`model-application${focused ? " result-focused" : ""}`}>
    <div className="model-lab-start-choice">
      <div className="model-lab-usage-tabs" role="group" aria-label="분석할 데이터">
        <button type="button" className="secondary" aria-pressed={usage === "playground"}
          onClick={() => setUsage("playground")}>입력 실험</button>
        <button type="button" className="secondary" aria-pressed={usage === "dataset"}
          onClick={() => setUsage("dataset")}>Dataset 분석</button>
      </div>
      <p className="hint">{usage === "playground" ? "한 관측값의 특징을 바꾸고, 계산 전후 점수를 비교합니다." : "Dataset 전체를 점수화하고, 관심 관측값을 입력 실험으로 가져옵니다."}</p>
    </div>
    <div hidden={usage !== "playground"} ref={playgroundPanel}>
      <ModelLabPlayground model={model} canRun={canRun} canCancel={canCancel}
        observationInput={observationInput} onBackToAnalysis={backToObservation} onLatestRun={onPlaygroundRun} />
    </div>
    <div hidden={usage !== "dataset"} ref={datasetPanel}>
      <MLScoringPanel selectedDatasetId={selectedDatasetId} onSelectDataset={onSelectDataset} model={model} datasets={datasets} hasMoreDatasets={hasMoreDatasets}
        onLoadMoreDatasets={onLoadMoreDatasets} canRun={canRun} canCancel={canCancel}
        onOpenResult={id => onOpenResource("scoring", id)} onOpenResource={onOpenResource}
        canUpload={canUpload} onDatasetRegistered={onDatasetRegistered}
        onResultViewChange={setResultVisible} onInputReadyChange={onInputReadyChange}
        onTryObservation={tryObservation} />
    </div>
  </div>;
}
