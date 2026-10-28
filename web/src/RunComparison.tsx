import type { Dataset, MLflowRun } from "./types";

const labels: Record<string, string> = {
  "sos.dataset_id": "Dataset", "sos.dataset_version": "데이터 판본",
  "sos.feature_count": "Feature 개수", "sos.feature_set_sha256": "Feature 구성 지문",
  "sos.training_input_mode": "학습 입력 범위", "sos.training_input_rows": "학습 표본 상한",
  "sos.missing_policy": "결측 처리", "sos.train_end": "학습 종료 시각",
  "sos.calibration_end": "판정 기준 산정 종료 시각",
  "sos.n_estimators": "트리 개수", "sos.max_samples": "트리별 표본 수",
  "sos.max_features": "트리별 Feature 비율", "sos.bootstrap": "복원 추출", "sos.random_state": "난수 Seed",
  "sos.training_input_seed": "학습 표본 Seed", "sos.job_kind": "작업 종류",
  "sos.score_mean": "평균 이상 점수", "sos.score_min": "최소 이상 점수", "sos.score_max": "최대 이상 점수",
  "sos.row_count": "관측 수", "sos.threshold": "판정 기준 점수",
  "sos.anomaly_count": "기준 이상 관측 수", "sos.anomaly_rate": "기준 이상 비율",
};
type Field = "params" | "metrics";

/** 기록 누락도 차이로 남긴다. 같은 값의 지표나 성능 우열을 임의로 만들지 않는다. */
export function differingRunKeys(runs: MLflowRun[], field: Field): string[] {
  if (runs.length !== 2) return [];
  return [...new Set(runs.flatMap(run => Object.keys(run[field])))].sort()
    .filter(key => runs[0][field][key] !== runs[1][field][key]);
}

export function RunComparison({ runs, datasets }: { runs: MLflowRun[]; datasets: Dataset[] }) {
  const keys = (field: Field) => [...new Set(runs.flatMap(run => Object.keys(run[field])))].sort();
  const differences = (["params", "metrics"] as const).flatMap(field =>
    differingRunKeys(runs, field).map(key => ({ field, key })));
  const preview = (["params", "metrics"] as const).flatMap(field =>
    differences.filter(row => row.field === field).slice(0, 3));
  for (const row of differences) {
    if (preview.length < 6 && !preview.includes(row)) preview.push(row);
  }
  const value = (run: MLflowRun, field: Field, key: string) => {
    const recorded = run[field][key];
    if (recorded === undefined) return "기록 없음";
    if (key === "sos.dataset_id") return datasets.find(dataset => dataset.id === recorded)?.name ?? `현재 목록 밖의 Dataset · ${String(recorded).slice(0, 8)}`;
    return String(recorded);
  };
  const relation = (key: string) => runs.some(run => run.params[key] === undefined)
    ? "기록 부족" : runs[0].params[key] === runs[1].params[key] ? "동일" : "다름";
  const table = (rows: { field: Field; key: string }[]) => <div className="table-scroll"><table>
    <thead><tr><th>항목</th>{runs.map((run, index) => <th key={run.id}>실행 {index + 1} · {run.id.slice(0, 8)}</th>)}</tr></thead>
    <tbody>{rows.map(({ field, key }) => <tr key={field + key}>
      <th title={key}>{labels[key] ?? key}{field === "metrics" ? " · 지표" : ""}</th>
      {runs.map(run => <td key={run.id} title={String(run[field][key] ?? "기록 없음")}>{value(run, field, key)}</td>)}
    </tr>)}</tbody>
  </table></div>;
  return <section className="run-comparison" aria-label="선택 실행 비교">
    <h3>선택 실행 비교</h3>
    {runs.length === 1 ? <p className="hint">같은 종류의 실행을 하나 더 선택하면 차이를 비교합니다.</p> : <>
      <p className="hint">Dataset {relation("sos.dataset_id")} · 데이터 판본 {relation("sos.dataset_version")} · Feature 구성 {relation("sos.feature_set_sha256")}</p>
      <p>기록된 값이 다른 항목 {differences.length}개</p>
      {differences.length > 0 ? table(preview) : <p className="muted">두 실행의 기록된 설정·지표 값이 같습니다.</p>}
      {differences.length > 6 && <p className="hint">차이 중 6개를 표시합니다. 나머지는 전체 비교에서 확인할 수 있습니다.</p>}
      <p className="hint">기록이 없는 값은 비교할 수 없습니다. 점수 차이가 성능 개선을 의미하지 않으며 학습 점수는 독립 검증 성능이 아닙니다.</p>
    </>}
    <details><summary>전체 설정·지표·비교 근거</summary>
      {(["params", "metrics"] as const).map(field => <section key={field}>
        <h4>{field === "params" ? "실행 조건·설정" : "기록된 지표"}</h4>
        {keys(field).length ? table(keys(field).map(key => ({ field, key }))) : <p className="muted">기록된 항목이 없습니다.</p>}
      </section>)}
    </details>
  </section>;
}
