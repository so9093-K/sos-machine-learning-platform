/** 한 번의 버튼 클릭보다 먼저 주제·데이터·실행의 의미를 이해하도록 돕는 첫 화면. */
import { useState } from "react";
import { modelLabLabels } from "./ModelLabTaxonomy";
import type { Example, PipelineDefinition } from "./types";

type Phase = "registering" | "checking" | "submitting" | null;

export function ModelLabIntroduction({ definition, example, examplesLoading,
  exampleBusy, examplePhase, canCreateDataset, canRun, onStartExample, onOwnData }: {
  definition: PipelineDefinition | null;
  example?: Example;
  examplesLoading: boolean;
  exampleBusy: boolean;
  examplePhase: Phase;
  canCreateDataset: boolean;
  canRun: boolean;
  onStartExample: () => void;
  onOwnData: () => void;
}) {
  const [showExample, setShowExample] = useState(false);
  const labels = modelLabLabels(definition ?? {
    learning_type: "unsupervised", task_type: "anomaly_detection", topic: "activity_anomaly",
  });
  return <section className="platform-intro pipeline-hero model-lab-introduction"
    aria-labelledby="model-lab-topic-heading">
    <div className="model-lab-topic-trail" aria-label="모델 실험실 주제 분류">
      {labels.map((label, index) => <span key={index}>
        {index > 0 && <span aria-hidden="true"> / </span>}{label}
      </span>)}
    </div>
    <div className="model-lab-intro-header">
      <div>
        <span className="eyebrow">주제 알아보기</span>
        <h2 id="model-lab-topic-heading">활동 이상 탐지</h2>
        <p>시간 구간별 사용자·호스트 활동의 패턴을 살펴보고, 상대적으로 특이한
          관측치를 찾아 모델의 판단 근거를 탐구합니다.</p>
      </div>
      <div className="model-lab-topic-tags" aria-label="분석 정보">
        <span>{labels[0]}</span><span>{labels[1]}</span>
        <span>Isolation Forest</span>
      </div>
    </div>
    <div className="model-lab-concept" aria-label="활동 이상 탐지 개념 설명">
      <div className="model-lab-concept-intro">
        <strong>어떤 과정을 경험하나요?</strong>
        <p>입력으로 사용할 것은 원시 이벤트가 아니라 시간 구간별 집계 Feature입니다.
          모델의 높은 점수는 검토할 신호이지 실제 사건의 확정이나 정확도 보증이 아닙니다.</p>
      </div>
      <svg className="model-lab-concept-graphic" viewBox="0 0 620 146" role="img"
        aria-label="활동 Feature 입력, 패턴 학습, 이상 점수 산출, 상위 관측치 탐구의 개념 흐름">
        <defs><marker id="model-lab-arrow" markerWidth="8" markerHeight="8" refX="5" refY="3" orient="auto">
          <path d="M0 0 L6 3 L0 6 Z" fill="currentColor" opacity="0.65"/>
        </marker></defs>
        {[0,1,2,3].map(index => {
          const x = 8 + index * 155;
          const labels = [
            ["시간 구간", "활동 Feature"],
            ["Isolation Forest", "패턴 학습"],
            ["이상 점수", "높을수록 특이"],
            ["관측치 탐구", "Feature·설명"],
          ];
          return <g key={index}>
            <rect x={x} y="18" width="136" height="92" rx="11"
              fill="currentColor" fillOpacity="0.045" stroke="currentColor" strokeOpacity=".21"/>
            <text x={x+68} y="53" textAnchor="middle" fill="currentColor" fontSize="13"
              fontWeight="600">{labels[index][0]}</text>
            <text x={x+68} y="77" textAnchor="middle" fill="currentColor"
              fontSize="11" opacity=".72">{labels[index][1]}</text>
            {index < 3 && <path d={`M${x+139} 65 H${x+153}`} stroke="currentColor"
              strokeOpacity=".6" strokeWidth="1.6" markerEnd="url(#model-lab-arrow)"/>}
          </g>;
        })}
        <text x="10" y="137" fontSize="11" fill="currentColor" opacity=".65">
          이해를 돕는 개념도 · 실제 모델 결과나 시간별 분석 값이 아닙니다
        </text>
      </svg>
    </div>
    <div className="model-lab-entry-actions">
      <div className="model-lab-entry-card">
        <span className="eyebrow">처음 사용한다면</span>
        <h3>예제로 알아보기</h3>
        <p>합성 데이터의 구성과 분석 결과의 의미를 확인한 다음 모델 학습을 선택합니다.</p>
        <button type="button" className="secondary" aria-expanded={showExample}
          aria-controls="model-lab-example-preview"
          onClick={() => setShowExample(open => !open)}>
          {showExample ? "예제 설명 접기" : "예제 구성 살펴보기 →"}
        </button>
      </div>
      <div className="model-lab-entry-card">
        <span className="eyebrow">데이터가 준비되어 있다면</span>
        <h3>내 데이터로 실험</h3>
        <p>현재 분석의 필수 Feature와 시간 컬럼을 확인하고, 원하는 Dataset으로 시작합니다.</p>
        <button type="button" className="secondary" onClick={onOwnData}>
          내 Dataset 입력 확인하기 →
        </button>
      </div>
    </div>
    {showExample && <section id="model-lab-example-preview" className="model-lab-example-preview"
      aria-label="예제 데이터 및 실행 내용">
      <h3>학습 예제 확인</h3>
      {examplesLoading && <p role="status">예제 정보를 불러오고 있습니다.</p>}
      {!examplesLoading && !example && <p role="alert">예제를 찾지 못했습니다. 목록 조회 후 다시 확인하세요.</p>}
      {example && <>
        <p>{example.description}</p>
        <div className="model-lab-example-facts">
          <div><span>합성 관측치</span><strong>{example.row_count.toLocaleString("ko-KR")}개</strong></div>
          <div><span>필요한 활동 Feature</span>
            <strong>{definition ? `${definition.required_features.length}개` : "확인 중"}</strong></div>
          <div><span>실행 구간</span><strong>Train · Calibration · Test</strong></div>
        </div>
        <p><strong>예제의 대표 컬럼:</strong> {example.columns.join(" · ")}
          <span className="model-lab-example-footnote"> · 전체 컬럼 목록은 아닙니다.</span></p>
        <p><strong>실행 시:</strong> 예제 Dataset을 명시적으로 등록(이미 등록했다면 재사용)하고
          입력 계약 확인 후 새 Pipeline Job을 제출합니다. 결과에서 학습 점수·판정 기준·
          Test 후보를 살펴볼 수 있습니다.</p>
        <p className="model-lab-example-caution">이 데이터는 합성 예제입니다.
          실제 침해 사건이나 탐지 정확도를 증명하지 않습니다. 설명을 보는 동안 Dataset이나 Job은 생성되지 않습니다.</p>
      </>}
      <button id="pipeline-example-start" type="button"
        disabled={!example || examplesLoading || !canCreateDataset || !canRun || exampleBusy}
        onClick={onStartExample}>
        {examplePhase === "registering" ? "예제 데이터 준비 중…" :
         examplePhase === "checking" ? "학습 입력 확인 중…" :
         examplePhase === "submitting" ? "학습 요청 중…" : "확인한 예제로 모델 학습 실행 →"}
      </button>
      {examplePhase && <p role="status">{
        examplePhase === "registering" ? "예제 데이터를 등록하거나 기존 판본을 확인합니다." :
        examplePhase === "checking" ? "입력 조건을 확인합니다." : "학습 Job을 제출하고 있습니다."
      }</p>}
      {!canCreateDataset && <p className="hint">Dataset 생성 권한이 필요합니다.</p>}
      {!canRun && <p className="hint">작업 실행 권한이 필요합니다.</p>}
    </section>}
  </section>;
}
