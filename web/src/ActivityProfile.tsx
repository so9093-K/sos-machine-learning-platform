import type { ReactNode } from "react";
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

export function ActivityProfile({ model, values, names, editing = false, control }: {
  model: ModelRevision; values: Record<string, number | null>; names?: string[]; editing?: boolean;
  control?: (name: string) => ReactNode;
}) {
  const ranges = trainingFeatureRanges(model);
  const selected = names ?? profileFeatureNames(model, values);
  return <section className="activity-profile" aria-label="활동 프로필 · 학습 데이터 비교">
    <div className="activity-profile-heading"><h4>활동 프로필</h4>
      <span className="chip">{editing ? "편집 중인 입력" : "관측값"}</span></div>
    <p className="hint">{ranges ? "항목별 학습 중간 80% 구간과 현재 값을 비교합니다. 정상 범위나 모델 기여도가 아닙니다."
      : "이 판본에는 학습 분포 기록이 없어 중앙값과 수치만 비교합니다."}</p>
    {ranges && <div className="activity-profile-legend"><span>━ 학습 중간 80% 범위</span><span>│ 중앙값</span><span>● 현재 값</span></div>}
    <div className="activity-profile-rows">{selected.map(name => {
      const value = values[name];
      const range = ranges?.[name];
      const median = range?.p50 ?? referenceMedian(model, name);
      // 항목마다 자기 단위의 범위를 쓴다. 서로 다른 단위의 원시 수치를 같은 축에 올리지 않는다.
      const span = range ? range.p90 - range.p10 : 0;
      const padding = span > 0 ? span / 4 : Math.max(Math.abs(median ?? 0) / 10, 1);
      const low = range ? range.p10 - padding : 0;
      const width = span + padding * 2;
      const position = (number: number) => Math.max(1, Math.min(99, (number - low) / width * 100));
      const outside = range && finite(value) ? value < range.p10 ? "중간 80% 구간 미만" : value > range.p90 ? "중간 80% 구간 초과" : "" : "";
      const difference = finite(value) && median !== null && Number.isFinite(value - median) ? value - median : null;
      return <div className="activity-profile-row" key={name}>
        <div className="activity-profile-feature"><strong title={name}>{activityFeatureLabel(name)}</strong>
          {control ? control(name) : <strong className="activity-profile-value">{finite(value) ? formatFeatureValue(name, value) : "결측값"}</strong>}</div>
        {range && <div className="activity-profile-scale" role="img"
          title={`학습 하위 10% 지점 ${formatFeatureValue(name, range.p10)} · 하위 90% 지점 ${formatFeatureValue(name, range.p90)}`}
          aria-label={`${activityFeatureLabel(name)} · 학습 하위 10% 지점 ${range.p10}, 중앙값 ${range.p50}, 하위 90% 지점 ${range.p90}, 현재 ${finite(value) ? value : "유효한 값 없음"}`}>
          <span className="activity-profile-band" style={{ left: `${position(range.p10)}%`, width: `${span / width * 100}%` }} />
          <span className="activity-profile-median" style={{ left: `${position(range.p50)}%` }} />
          {finite(value) && <span className="activity-profile-dot" style={{ left: `${position(value)}%` }} />}
        </div>}
        <div className="activity-profile-caption">
          <span>학습 중앙값 {median === null ? "기록 없음" : formatFeatureValue(name, median)}</span>
          <span>{outside ? outside + " · " : ""}{difference === null ? "비교 불가" : difference === 0 ? "중앙값과 동일" :
            `차이 ${difference > 0 ? "+" : ""}${formatFeatureValue(name, difference)}`}</span>
        </div>
      </div>;
    })}</div>
    <p className="hint">{selected.length < model.features.length ? `${model.features.length}개 중 ${selected.length}개 표시 · ` : ""}
      {editing ? "값 변경 후 점수 계산으로 모델 반응을 확인합니다." : "학습 중앙값과의 차이가 큰 항목을 먼저 표시합니다."}</p>
  </section>;
}
