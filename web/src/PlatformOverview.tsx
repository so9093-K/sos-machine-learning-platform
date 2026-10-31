/** 제품 영역과 실제 모델 제공 상태를 함께 안내한다. */
import { useEffect, useState } from "react";
import { formatError, request } from "./api";
import type { ModelOfferingPage } from "./types";
import type { WorkspaceTab } from "./PlatformNavigation";

type DatasetLink = { id: string; name: string; kind: string };
type Props = {
  brandName: string;
  datasets: DatasetLink[];
  canViewModels?: boolean;
  canWrite?: boolean;
  canPrepare?: boolean;
  onNavigate: (tab: WorkspaceTab) => void;
  onOpenLearningType: (id: string) => void;
  onOpenDataset: (id: string) => void;
};

const kindLabels: Record<string, string> = { file: "파일", iceberg: "Iceberg 테이블" };

export function PlatformOverview({ brandName, datasets, onNavigate, onOpenLearningType, onOpenDataset, canViewModels = true,
  canWrite = true, canPrepare = true }: Props) {
  const [modelState, setModelState] = useState<"loading" | "ready" | "empty" | "error">("loading");
  const [modelError, setModelError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (!canViewModels) return;
    let active = true;
    setModelState("loading"); setModelError("");
    void request<ModelOfferingPage>("/api/ml/offered-models?limit=1")
      .then(page => { if (active) setModelState(page.items.length ? "ready" : "empty"); })
      .catch(cause => { if (active) { setModelState("error"); setModelError(formatError(cause)); } });
    return () => { active = false; };
  }, [reload, canViewModels]);
  return <div className="platform-home">
    <section className="platform-intro">
      <span className="eyebrow">{brandName} · EXPLORE · UNDERSTAND · APPLY</span>
      <p>데이터를 이해하고, 준비된 모델을 내 데이터에 적용해 결과와 근거를 확인합니다.</p>
    </section>
    <section aria-labelledby="available-work-heading">
      <h2 className="platform-section-title" id="available-work-heading">주요 기능</h2>
      <div className="platform-destinations">
        <article className="panel platform-destination">
          <h3>데이터 탐색</h3>
          <p>Dataset을 등록하고 구조·품질·분포를 확인합니다.</p>
          <button type="button" onClick={() => onNavigate("understanding")}>
            데이터 탐색 시작 →
          </button>
        </article>
        <article className="panel platform-destination">
          <h3>모델 활용</h3>
          <p>제공된 모델의 목적과 입력 조건을 확인하고 데이터에 적용합니다.</p>
          {!canViewModels ? <p>모델 조회 권한 없음</p> : <>
            <p role="status">{modelState === "loading" ? "모델 제공 상태 확인 중…"
              : modelState === "ready" ? "제공 모델 있음 · 목적·입력 조건 확인 후 선택"
              : modelState === "empty" ? "제공 모델 없음 · 개발·배포 과정에서 모델을 공급해야 합니다."
              : "모델 제공 상태 확인 실패"}</p>
            {modelState === "error" && <p role="alert">{modelError}</p>}
            {(modelState === "empty" || modelState === "error") &&
              <button type="button" className="secondary" onClick={() => setReload(value => value + 1)}>제공 상태 재조회</button>}
            <button type="button" onClick={() => onNavigate("ml")}>모델 목록 →</button>
          </>}
        </article>
      </div>
    </section>
    <section className="panel" aria-labelledby="continue-data-heading">
      <div className="panel-head"><div>
        <h2 id="continue-data-heading">최근 Dataset</h2>
        <p>이미 등록한 Dataset을 탐색하거나 분석에 활용할 수 있습니다.</p>
      </div>{canPrepare && <button className="secondary" type="button"
        onClick={() => onNavigate("preparation")}>데이터 준비 →</button>}</div>
      {datasets.length === 0 ? <p className="muted">{canWrite
        ? "현재 조회한 Dataset이 없습니다. 데이터 탐색에서 파일을 등록하거나 Iceberg 테이블을 연결하세요."
        : "이 계정에서 조회할 수 있는 Dataset이 없습니다. 조회 전용 계정은 이 계정 소유의 Dataset만 볼 수 있으며 새 Dataset은 등록할 수 없습니다."}</p> :
        <div className="platform-compact-list">
          {datasets.slice(0, 5).map(dataset => <button key={dataset.id} type="button"
            className="platform-list-link ghost" onClick={() => onOpenDataset(dataset.id)}>
            <span className="platform-resource-name">{dataset.name}</span>
            <small>{kindLabels[dataset.kind] ?? dataset.kind} · 탐색 →</small>
          </button>)}
        </div>}
    </section>
    <section className="platform-outlook">
      <h2>ML 분석</h2>
      <p>지원하는 학습 유형·분석 과제를 확인하고 활동 이상 탐지 모델을 데이터에 적용합니다.</p>
      <div className="platform-outlook-actions">
        <button className="secondary" type="button" onClick={() => onOpenLearningType("unsupervised")}>비지도 학습 →</button>
        <button className="secondary" type="button" onClick={() => onNavigate("topic")}>활동 이상 탐지 Playground →</button>
        <button className="secondary" type="button" onClick={() => onNavigate("jobs")}>작업 이력 →</button>
      </div>
    </section>
  </div>;
}
