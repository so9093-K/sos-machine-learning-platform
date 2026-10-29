import { useEffect, useMemo, useState } from "react";
import type * as echarts from "echarts";
import { AnalysisEvidence, coversAllRows, dataSelectionLabel } from "./AnalysisEvidence";
import { CHART_COLORS, DataChart } from "./DataChart";
import { formatDtype } from "./display-format";
import { histogram } from "./feature-utils";
import type { Preview, Profile } from "./types";

const PAGE = 10;

/** 제한 조회 프로파일의 컬럼 통계와 미리보기 분포·행 비교 차트.
 *
 *  검색·페이지·축 선택은 이 화면 안에서만 쓰며 Dataset이 바뀌면 처음 상태로 돌아간다.
 */
export function ProfileStatistics({ profile, profileStatus, profileError, preview, columnNames,
  canRun, busy, onRunFullProfile, onRetry }: {
  profile: Profile | null; profileStatus: string; profileError: string;
  preview: Preview | null; columnNames: string[];
  canRun: boolean; busy: boolean; onRunFullProfile: () => void; onRetry: () => void;
}) {
  const [visualColumn, setVisualColumn] = useState("");
  const [timeColumn, setTimeColumn] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  useEffect(() => {
    if (!preview) return;
    const columns = Object.keys(preview.schema);
    // 원본의 첫 컬럼이 의미 있는 지표나 시간축이라는 근거는 없다.
    setVisualColumn(current =>
      columns.includes(current) && /Int|Float|Decimal/.test(preview.schema[current]) ? current : "");
    setTimeColumn(current => columns.includes(current) ? current : "");
  }, [preview]);
  const complete = Boolean(profile?.sampled && coversAllRows(profile.row_count, profile.sample_limit));
  const numeric = profile?.columns.filter(c => /Int|Float|Decimal/.test(c.dtype)) ?? [];
  const matched = profile?.columns.filter(column =>
    column.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())) ?? [];
  const currentPage = Math.min(page, Math.max(0, Math.ceil(matched.length / PAGE) - 1));
  const distribution = useMemo(() => histogram(preview?.rows ?? [], visualColumn), [preview, visualColumn]);
  const distributionOption = useMemo<echarts.EChartsCoreOption | null>(() => {
    if (!distribution) return null;
    return {
      backgroundColor: "transparent", textStyle: { color: CHART_COLORS.text },
      tooltip: { trigger: "axis" },
      grid: { left: 50, right: 15, top: 20, bottom: 48 },
      xAxis: { type: "category", data: distribution.labels,
        axisLabel: { color: CHART_COLORS.label, rotate: 20, hideOverlap: true } },
      yAxis: { type: "value", axisLabel: { color: CHART_COLORS.label },
        splitLine: { lineStyle: { color: CHART_COLORS.grid } } },
      series: [{ type: "bar", data: distribution.counts, itemStyle: { color: CHART_COLORS.bar } }],
    };
  }, [distribution]);
  const chartOption = useMemo<echarts.EChartsCoreOption | null>(() => {
    if (!preview?.rows.length || !visualColumn || !timeColumn) return null;
    const values = preview.rows.map(r => r[visualColumn])
      .filter((n): n is number => typeof n === "number" && Number.isFinite(n));
    // 일정한 값이나 가로축 하나만으로 변화가 존재하는 것처럼 표시하지 않는다.
    if (new Set(values).size < 2 ||
        new Set(preview.rows.map(row => String(row[timeColumn] ?? ""))).size < 2) return null;
    return {
      backgroundColor: "transparent",
      textStyle: { color: CHART_COLORS.text },
      tooltip: { trigger: "axis" },
      grid: { left: 48, right: 20, top: 25, bottom: 42 },
      xAxis: { type: "category", data: preview.rows.map(r => String(r[timeColumn] ?? "")),
        axisLabel: { color: CHART_COLORS.label, hideOverlap: true } },
      yAxis: { type: "value", axisLabel: { color: CHART_COLORS.label }, splitLine: {
        lineStyle: { color: CHART_COLORS.grid } } },
      series: [{ type: "line", showSymbol: false, name: visualColumn, data: preview.rows.map(
        r => typeof r[visualColumn] === "number" ? r[visualColumn] : null
      ), lineStyle: { color: CHART_COLORS.line }, itemStyle: { color: CHART_COLORS.line } }],
    };
  }, [preview, visualColumn, timeColumn]);

  return <details className="data-profile-details">
    <summary>컬럼 통계·분포</summary>
    <div className="panel-head"><div><h3>부분 데이터 프로파일</h3>
      <p>정렬 기준 없이 제한 조회한 최대 1,000행에서 계산합니다. 전체 데이터 분석은 별도 Job으로 실행할 수 있습니다.</p>
    </div>{canRun && <button disabled={busy} onClick={onRunFullProfile}>전체 데이터 프로파일 실행 ↗</button>}</div>
    {profileStatus === "loading" && <p role="status" className="muted">EDA 통계를 계산하고 있습니다.</p>}
    {profileStatus === "error" && <p role="alert">{profileError}</p>}
    {profileStatus === "error" && <button className="secondary" onClick={onRetry}>EDA 다시 시도</button>}
    {profile && <>
      <div className="cards">
        <div className="stat"><span>분석한 행</span><strong>{profile.row_count.toLocaleString()}</strong></div>
        <div className="stat"><span>컬럼</span><strong>{profile.columns.length}</strong></div>
        <div className="stat"><span>결측 셀</span><strong>{profile.columns.reduce(
          (s, c) => s + c.nulls, 0).toLocaleString()}</strong></div>
      </div>
      <AnalysisEvidence
        summary={(profile.sampled && !complete ? "부분 데이터 분석 · " : "전체 데이터 분석 · ")
          + profile.row_count.toLocaleString() + "행 분석"}
        note={complete
          ? `조회 상한(${(profile.sample_limit ?? 0).toLocaleString()}행)보다 데이터가 작아 전체 행을 분석했습니다.`
          : profile.sampled
          ? "정렬 기준 없는 제한 조회 결과입니다. 전체 데이터의 분포를 대표하지 않습니다."
          : "전체 데이터 대상 분석입니다. 개별 수치의 계산 정확도는 별도 확인이 필요합니다."}
        details={[
          { label: "데이터 선택 방식", value: dataSelectionLabel(profile.sampled ? "limited_rows" : "full_scan") },
          { label: "조회 행 수 상한", value: !profile.sampled ? "해당 없음 (전체 대상)"
            : profile.sample_limit === null ? "미확인" : profile.sample_limit.toLocaleString() + "행" },
          { label: "계산 대상 행 수", value: profile.row_count.toLocaleString() + "행" },
          { label: "고유값 수", value: "조회 범위 내 정확 (NULL 포함)" },
        ]}
      />
      <div className="profile-column-tools">
        <h4>컬럼 통계</h4>
        <label>컬럼 검색<input type="search" aria-label="프로파일 컬럼 검색" value={search}
          onChange={event => { setSearch(event.target.value); setPage(0); }} /></label>
        <span role="status">{matched.length}개 중 {matched.length ? currentPage * PAGE + 1 : 0}–{Math.min(matched.length, (currentPage + 1) * PAGE)}개</span>
      </div>
      <div className="table-scroll" tabIndex={0} role="region" aria-label="컬럼 통계 표"><table><thead><tr>
        <th>컬럼</th><th>dtype</th><th>NULL</th><th>NaN</th><th>고유 값</th><th>평균</th>
      </tr></thead><tbody>{matched.slice(currentPage * PAGE, (currentPage + 1) * PAGE).map(c => <tr key={c.name}>
        <td>{c.name}</td><td title={c.dtype}>{formatDtype(c.dtype)}</td><td>{c.nulls}</td>
        <td>{c.nan_count ?? "—"}</td><td>{c.unique}</td>
        <td>{typeof c.mean === "number" ? c.mean.toFixed(3) : "—"}</td>
      </tr>)}</tbody></table></div>
      {matched.length > PAGE && <div className="feature-result-pages">
        <button type="button" className="secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>이전 컬럼</button>
        <button type="button" className="secondary" disabled={(currentPage + 1) * PAGE >= matched.length} onClick={() => setPage(currentPage + 1)}>다음 컬럼</button>
      </div>}
      <div className="chart-controls"><h4>분포·행 비교</h4>
        <label>값 <select aria-label="값 컬럼" value={visualColumn} onChange={e => setVisualColumn(e.target.value)}>
          <option value="">값 컬럼 선택</option>
          {numeric.map(c => <option key={c.name} value={c.name}>{c.name}</option>)}
        </select></label>
        <label>가로축 <select aria-label="가로축 컬럼" value={timeColumn} onChange={e => setTimeColumn(e.target.value)}>
          <option value="">가로축 선택</option>
          {columnNames.map(c => <option key={c} value={c}>{c}</option>)}
        </select></label></div>
      <div className="charts">
        <div><h4>행 순서에 따른 값</h4>{chartOption ? <DataChart option={chartOption} ariaLabel="조회 행 순서의 값 차트" /> :
          <p className="muted">값·가로축 컬럼을 선택하면 변화 여부를 확인합니다. 값이나 축이 일정하면 변화 차트를 만들지 않습니다.</p>}</div>
        <div><h4>분포 (Histogram)</h4>{distributionOption ? <DataChart option={distributionOption} ariaLabel="데이터 분포 히스토그램" /> :
          <p className="muted">숫자형 값 컬럼을 선택하면 반환된 행의 분포를 확인합니다.</p>}</div>
      </div>
      <p className="hint">차트와 히스토그램은 최대 100행의 기본 미리보기만 사용합니다. 가로축 값으로 정렬하지 않고 조회된 행 순서로 연결하므로 시간적 추세를 확정하지 않습니다.</p>
    </>}
  </details>;
}
