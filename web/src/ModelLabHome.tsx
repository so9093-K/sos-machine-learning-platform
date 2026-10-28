import { useEffect, useState } from "react";
import { request } from "./api";
import type { MLflowModelVersionPage } from "./types";

type ModelAvailability = "checking" | "available" | "none" | "unknown";

/** 기본 모델 그룹의 첫 Registry 페이지로 사용 가능한 모델의 존재만 확인한다.
 * 조회 실패나 남은 페이지가 있으면 빈 목록으로 단정하지 않는다.
 */
function useModelAvailability(): ModelAvailability {
  const [state, setState] = useState<ModelAvailability>("checking");
  useEffect(() => {
    let active = true;
    void request<MLflowModelVersionPage>("/api/ml/registry/models?registry_key=default")
      .then(page => {
        if (!active) return;
        if (page.items.some(item => item.status === "READY")) setState("available");
        else setState(page.next_page_token ? "unknown" : "none");
      })
      .catch(() => { if (active) setState("unknown"); });
    return () => { active = false; };
  }, []);
  return state;
}

/** 활동 이상 탐지 주제: 처리 단계가 아니라 모델의 목적과 실제 사용 행동을 보여준다. */
export function ModelLabHome({ onUse, onLearn, canLearn = true }: {
  onUse: () => void; onLearn: () => void; canLearn?: boolean;
}) {
  const availability = useModelAvailability();
  // 사용할 모델이 없으면 새 학습을, 있으면 모델 사용을 주요 행동으로 둔다. 확인 전에는 어느 쪽도 강조하지 않는다.
  const useClass = availability === "available" ? undefined : "secondary";
  const learnClass = availability === "none" ? undefined : "secondary";
  return <section className="model-lab-home" aria-labelledby="model-lab-home-heading">
    <div className="model-lab-home-lead">
      <div className="model-lab-home-copy">
        <span className="eyebrow">비지도 학습 · 이상 탐지 · Isolation Forest</span>
        <h2 id="model-lab-home-heading">평소와 다른 활동 탐지</h2>
        <p>시간대별 사용자·호스트 활동의 특징을 모델이 점수로 평가합니다.
          점수가 높은 관측치를 찾아 상세 활동 특징을 확인할 수 있습니다.</p>
      </div>
      <div className="model-lab-home-visual">
        <svg viewBox="0 0 520 256" role="img" aria-labelledby="model-lab-visual-title model-lab-visual-desc"
          className="model-lab-home-chart">
          <title id="model-lab-visual-title">활동의 평소 패턴과 변화가 큰 구간</title>
          <desc id="model-lab-visual-desc">완만하게 변화하는 활동선과 눈에 띄게 높아진 한 구간을 대비시킨 개념 일러스트레이션</desc>
          <rect x="1" y="1" width="518" height="254" rx="18" fill="currentColor" fillOpacity="0.02" stroke="currentColor" strokeOpacity=".13"/>
          <text x="28" y="33" fontSize="13" fill="currentColor" opacity=".78" fontWeight="600">시간대별 활동 패턴</text>
          {[73,112,151,190].map(y => <line key={y} x1="31" x2="488" y1={y} y2={y}
            stroke="currentColor" strokeOpacity=".12"/>)}
          <rect x="331" y="57" width="70" height="151" rx="10" fill="#E9AB5D" fillOpacity=".13"/>
          <path d="M32 160 C49 155 57 168 72 159 S99 153 114 164 S142 153 157 157 S187 167 202 161 S229 151 245 163 S274 159 289 154 S315 162 330 156 L349 147 L365 77 L381 96 L396 154 S424 164 439 158 S469 152 488 163"
            stroke="#7FB6E4" strokeWidth="3.5" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
          <circle cx="365" cy="77" r="7" fill="#F0B46A" stroke="#FFF1D9" strokeWidth="2"/>
          <path d="M365 67 V54" stroke="#F0B46A" strokeWidth="1.7"/>
          <rect x="316" y="35" width="146" height="23" rx="11" fill="#E9AB5D" fillOpacity=".18"/>
          <text x="389" y="51" textAnchor="middle" fontSize="11" fontWeight="650" fill="currentColor">변화가 큰 시간대</text>
          <text x="34" y="230" fontSize="12" fill="currentColor" opacity=".65">평소의 변화</text>
          <text x="486" y="230" fontSize="12" fill="currentColor" opacity=".65" textAnchor="end">시간 →</text>
        </svg>
        <p className="model-lab-home-illustration-note">모델 원리를 설명하는 개념 그림 · 실제 분석 결과가 아닙니다.</p>
        <div className="model-lab-home-visual-foot">
          <span><i className="model-lab-chart-key normal" aria-hidden="true"/>일반적인 활동 변화</span>
          <span><i className="model-lab-chart-key unusual" aria-hidden="true"/>눈에 띄는 변화</span>
        </div>
      </div>
    </div>
    <div className="model-lab-home-choices" aria-label="활동 이상 탐지 시작">
      <article className="model-lab-choice">
        <span className="model-lab-choice-icon" aria-hidden="true">↗</span>
        <h3>모델 적용</h3>
        <p>이미 학습한 모델을 데이터에 적용해 이상 점수와 검토할 관측치를 확인합니다.</p>
        <button type="button" className={useClass} onClick={onUse}>모델 적용 →</button>
      </article>
      <article className="model-lab-choice">
        <span className="model-lab-choice-icon" aria-hidden="true">✳</span>
        <h3>모델 학습</h3>
        <p>준비된 예제 또는 내 데이터로 모델을 학습하고, 생성된 모델과 Test 결과를 살펴봅니다.</p>
        {canLearn ? <button type="button" className={learnClass} onClick={onLearn}>모델 학습 →</button>
          : <p>현재 계정에는 모델 학습 권한이 없습니다. 등록된 모델과 이전 결과를 조회할 수 있습니다.</p>}
      </article>
    </div>
    {canLearn && availability !== "available" && <p className="model-lab-home-note">{availability === "none"
      ? "기본 모델 그룹에 사용할 수 있는 모델이 아직 없습니다. 먼저 예제로 모델을 학습해 보세요."
      : "이미 학습한 모델이 없어도 괜찮습니다. 먼저 예제로 모델을 학습할 수 있습니다."}</p>}
  </section>;
}
