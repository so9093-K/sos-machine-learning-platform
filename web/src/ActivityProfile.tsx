import { useEffect, useState, type ReactNode } from "react";
import { activityFeatureLabel } from "./ActivityFeatureLabels";
import { formatFeatureValue } from "./feature-format";
import type { ModelRevision } from "./types";

type Range = { min: number; p10: number; p50: number; p90: number; max: number };
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const featured = ["io_bytes_per_sec", "net_bytes_per_sec", "cnt_file_create", "cnt_proc_exec",
  "ratio_offhour_activity", "avg_filename_entropy"];

/** 실제 학습 때 기록한 분위수만 쓴다. 중앙값만 있는 판본에서 분포를 추정하지 않는다. */
export function trainingFeatureRanges(model: ModelRevision): Record<string, Range> | null {
  const profile = model.runtime.feature_training_profile as {
    method?: unknown; input_space?: unknown; row_count?: unknown; features?: Record<string, Range>;
  } | undefined;
  if (profile?.method !== "linear_quantiles_v1" || profile.input_space !== "model_input_after_preprocessing" ||
      !finite(profile.row_count) || !Number.isInteger(profile.row_count) || profile.row_count < 1 || !profile.features) return null;
  if (!model.features.every(name => {
    const row = profile.features?.[name];
    return row && [row.min, row.p10, row.p50, row.p90, row.max].every(finite) &&
    row.min <= row.p10 && row.p10 <= row.p50 && row.p50 <= row.p90 && row.p90 <= row.max;
  })) return null;
  return profile.features;
}

function referenceMedian(model: ModelRevision, name: string): number | null {
  const values = model.runtime.feature_reference_medians;
  const value = Array.isArray(values) ? values[model.features.indexOf(name)] : null;
  return finite(value) ? value : null;
}

/** 큰 차이를 먼저 고르되 모델 기여도나 희귀도 순위로 해석하지 않는다. 편집 중에는 선택을 고정한다. */
export function profileFeatureNames(model: ModelRevision, values: Record<string, number | null>, limit = 6): string[] {
  const ranges = trainingFeatureRanges(model);
  const priority = (name: string) => featured.includes(name) ? featured.indexOf(name) : featured.length + model.features.indexOf(name);
  const distance = (name: string) => {
    const value = values[name];
    const median = ranges?.[name].p50 ?? referenceMedian(model, name);
    if (!finite(value) || median === null) return 0;
    const range = ranges?.[name];
    const scale = range && range.p90 > range.p10 ? range.p90 - range.p10 : Math.max(Math.abs(value), Math.abs(median), 1);
    return Math.abs(value - median) / scale;
  };
  return [...model.features].sort((a, b) => distance(b) - distance(a) || priority(a) - priority(b)).slice(0, limit);
}

export function ActivityProfile({ model, values, names, editing = false, control, changedNames = [], activeName, onEdit }: {
  model: ModelRevision; values: Record<string, number | null>; names?: string[]; editing?: boolean;
  control?: (name: string) => ReactNode;
  changedNames?: string[];
  activeName?: string;
  onEdit?: (name: string) => void;
}) {
  const [changedPage, setChangedPage] = useState(0);
  const ranges = trainingFeatureRanges(model);
  const selected = names ?? profileFeatureNames(model, values);
  const extraChanged = changedNames.filter(name => model.features.includes(name) && !selected.includes(name));
  const activeIndex = extraChanged.indexOf(activeName ?? "");
  useEffect(() => {
    // 여러 항목을 수정해도 방금 편집한 항목을 숨기지 않는다. 기본 여섯 항목은 재정렬하지 않는다.
    if (activeIndex >= 0) setChangedPage(Math.floor(activeIndex / 6));
  }, [activeName, activeIndex]);
  const pageCount = Math.max(1, Math.ceil(extraChanged.length / 6));
  const currentPage = Math.min(changedPage, pageCount - 1);
  const row = (name: string, editable: boolean) => {
      const value = values[name];
      const range = ranges?.[name];
      const median = range?.p50 ?? referenceMedian(model, name);
      // 항목마다 자기 단위의 범위를 쓴다. 서로 다른 단위의 원시 수치를 같은 축에 올리지 않는다.
      const span = range ? range.p90 - range.p10 : 0;
      const padding = span > 0 ? span / 4 : Math.max(Math.abs(median ?? 0) / 10, 1);
      const low = range ? range.p10 - padding : 0;
      const width = span + padding * 2;
      const position = (number: number) => Math.max(1, Math.min(99, (number - low) / width * 100));
      const direction = range && finite(value) ? value < range.p10 ? -1 : value > range.p90 ? 1 : 0 : 0;
      const excess = range && finite(value) && direction ? direction < 0 ? range.p10 - value : value - range.p90 : null;
      const relativeExcess = excess !== null && span > 0 && Number.isFinite(excess / span) ? excess / span : null;
      const difference = finite(value) && median !== null && Number.isFinite(value - median) ? value - median : null;
      return <div className={`activity-profile-row${changedNames.includes(name) ? " is-changed" : ""}`} key={name}>
        <div className="activity-profile-feature"><strong title={name}>{activityFeatureLabel(name)}</strong>
          {changedNames.includes(name) && <span className="activity-profile-changed-label">수정</span>}
          {editable && control ? control(name) : <div className="activity-profile-current">
            <strong className="activity-profile-value">{finite(value) ? formatFeatureValue(name, value) : editing ? "입력 확인" : "결측값"}</strong>
            {onEdit && <button type="button" className="ghost"
              aria-label={activityFeatureLabel(name) + " 입력 편집"} onClick={() => onEdit(name)}>입력 편집</button>}
          </div>}</div>
        {range && <div className="activity-profile-scale" role="img"
          title={`학습 하위 10% 지점 ${formatFeatureValue(name, range.p10)} · 하위 90% 지점 ${formatFeatureValue(name, range.p90)}`}
          aria-label={`${activityFeatureLabel(name)} · 학습 하위 10% 지점 ${range.p10}, 중앙값 ${range.p50}, 하위 90% 지점 ${range.p90}, 현재 ${finite(value) ? value : "유효한 값 없음"}`}>
          <span className="activity-profile-band" style={{ left: `${position(range.p10)}%`, width: `${span / width * 100}%` }} />
          <span className="activity-profile-median" style={{ left: `${position(range.p50)}%` }} />
          {finite(value) && <span className={`activity-profile-dot${direction ? direction < 0 ? " outside-low" : " outside-high" : ""}`}
            style={{ left: `${position(value)}%` }} />}
        </div>}
        {range && <div className="activity-profile-quantiles">
          <span>P10 <strong>{formatFeatureValue(name, range.p10)}</strong></span>
          <span>P50 · 중앙값 <strong>{formatFeatureValue(name, range.p50)}</strong></span>
          <span>P90 <strong>{formatFeatureValue(name, range.p90)}</strong></span>
        </div>}
        <div className="activity-profile-caption">
          {!range && <span>학습 중앙값 {median === null ? "기록 없음" : formatFeatureValue(name, median)}</span>}
          <span>{difference === null ? "비교 불가" : difference === 0 ? "중앙값과 동일" :
            `중앙값 차이 ${difference > 0 ? "+" : ""}${formatFeatureValue(name, difference)}`}</span>
        </div>
        {direction !== 0 && excess !== null && finite(excess) && <p className="activity-profile-excess">
          <strong>{direction < 0 ? "◀ P10 미만" : "P90 초과 ▶"}</strong>
          <span>{direction < 0 ? "미달 폭" : "초과 폭"} {formatFeatureValue(name, excess)}</span>
          {relativeExcess !== null ? <span>학습 구간 폭의 {formatFeatureValue(undefined, relativeExcess)}배</span>
            : span === 0 ? <span>P10=P90 · 학습 구간 폭 0</span> : null}
        </p>}
      </div>;
  };
  return <section className="activity-profile" aria-label="활동 프로필 · 학습 데이터 비교">
    <div className="activity-profile-heading"><h4>활동 프로필</h4>
      <span className="chip">{editing ? "편집 중인 입력" : "관측값"}</span></div>
    <p className="hint">{ranges ? "항목별 학습 중간 80% 구간과 현재 값을 비교합니다. 정상 범위나 모델 기여도가 아닙니다."
      : "이 판본에는 학습 분포 기록이 없어 중앙값과 수치만 비교합니다."}</p>
    {ranges && <div className="activity-profile-legend"><span>━ 학습 P10–P90 구간</span><span>│ 중앙값</span>
      <span>● 현재 값 · ◀ ▶ 구간 밖</span></div>}
    <div className="activity-profile-rows">{selected.map(name => row(name, true))}</div>
    <p className="hint">{selected.length < model.features.length ? editing ? `기본 ${selected.length}개 · ` : `${model.features.length}개 중 ${selected.length}개 표시 · ` : ""}
      {editing ? "값 변경 후 점수 계산으로 모델 반응을 확인합니다." : "학습 중앙값과의 차이가 큰 항목을 먼저 표시합니다."}</p>
    {extraChanged.length > 0 && <section className="activity-profile-changes" aria-label="기본 항목 외 수정한 활동">
      <h5>추가 수정 항목 · {extraChanged.length}개</h5>
      <div className="activity-profile-rows">{extraChanged.slice(currentPage * 6, (currentPage + 1) * 6).map(name => row(name, false))}</div>
      {pageCount > 1 && <div className="feature-result-pages">
        <span>{currentPage + 1} / {pageCount}</span>
        <button type="button" className="secondary" disabled={currentPage === 0}
          onClick={() => setChangedPage(currentPage - 1)}>이전 수정 항목</button>
        <button type="button" className="secondary" disabled={currentPage === pageCount - 1}
          onClick={() => setChangedPage(currentPage + 1)}>다음 수정 항목</button>
      </div>}
    </section>}
  </section>;
}
