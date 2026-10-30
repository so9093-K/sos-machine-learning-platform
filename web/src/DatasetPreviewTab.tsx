import { useEffect, useState } from "react";
import { DatasetQueryPanel, emptyQueryDraft, type QueryDraft } from "./DatasetQueryPanel";
import type { DatasetOverview, Preview } from "./types";

/** 데이터 조회 조건·표의 컬럼 표시 범위와 마지막 적용 결과. 탭을 오가도 유지하고 Dataset이 바뀌면 버린다.
 *
 *  다시 불러오기(revision 증가)는 이전 적용 결과를 폐기하지만 편집 중인 조건은 보존하므로,
 *  새 기본 미리보기가 나타나도 그 조건이 적용됐다고 표시하지 않는다.
 */
export function useDatasetQuery(datasetId: string, revision: number) {
  const [result, setResult] = useState<{
    datasetId: string; revision: number; data: Preview; explicitProjection: boolean
  } | null>(null);
  const [draftState, setDraftState] = useState<{ datasetId: string; draft: QueryDraft } | null>(null);
  const [pending, setPending] = useState<{ datasetId: string; revision: number } | null>(null);
  const [allColumnsFor, setAllColumnsFor] = useState<string | null>(null);
  useEffect(() => {
    setResult(null); setDraftState(null); setPending(null); setAllColumnsFor(null);
  }, [datasetId]);
  const current = result?.datasetId === datasetId && result.revision === revision ? result : null;
  const draft = draftState?.datasetId === datasetId ? draftState.draft : emptyQueryDraft();
  const edited = draft.chosen.length > 0 || !!draft.filterColumn || !!draft.filterValue ||
    !!draft.sortColumn || draft.descending || draft.limit !== 30;
  const unapplied = (pending?.datasetId === datasetId && pending.revision === revision) || (!current && edited);
  return {
    current, draft, unapplied, showAllColumns: allColumnsFor === datasetId,
    toggleAllColumns: () => setAllColumnsFor(value => value === datasetId ? null : datasetId),
    changeDraft: (next: QueryDraft) => setDraftState({ datasetId, draft: next }),
    applyResult: (data: Preview, explicitProjection: boolean) => {
      setResult({ datasetId, revision, data, explicitProjection });
      setPending(null);
    },
    invalidate: () => setPending({ datasetId, revision }),
  };
}

export type DatasetQueryState = ReturnType<typeof useDatasetQuery>;

/** 기본 미리보기와 적용한 조건 조회 결과를 구분해 보여주는 데이터 조회 탭. */
export function DatasetPreviewPanel({ datasetId, revision, preview, previewStatus, previewError, overview,
  columnNames, busy, query, onRetry }: {
  datasetId: string; revision: number;
  preview: Preview | null; previewStatus: string; previewError: string;
  overview: DatasetOverview | null; columnNames: string[]; busy: boolean;
  query: DatasetQueryState; onRetry: () => void;
}) {
  const { current, showAllColumns } = query;
  const displayed = current?.data ?? preview;
  const available = displayed ? Object.keys(displayed.schema) : [];
  // 사용자가 명시적으로 선택한 투영 컬럼은 임의로 감추지 않는다.
  const columns = showAllColumns || current?.explicitProjection ? available : available.slice(0, 12);
  return <section className="panel" role="tabpanel"
    id="sos-panel-preview" aria-labelledby="sos-tab-preview" tabIndex={0}>
    <div className="panel-head"><div><h3>데이터 조회</h3><p>최대 100행 · 전체 데이터가 아닌 일부입니다.</p></div>
      <span className="chip">반환 {displayed?.returned_rows ?? "…"}행 · {current
        ? "조건 일치 전체 건수 미계산"
        : overview?.dataset_id === datasetId && overview.row_count != null
          ? `전체 ${overview.row_count.toLocaleString("ko-KR")}행`
          : "전체 행 수 미계산"}</span></div>
    {preview && <DatasetQueryPanel key={datasetId + ":" + revision} datasetId={datasetId}
      columns={columnNames} busy={busy} draft={query.draft}
      onDraftChange={query.changeDraft} onResult={query.applyResult} onInvalidate={query.invalidate} />}
    {displayed && <p className="query-result-context" role="status">
      {query.unapplied
        ? current
          ? "변경 사항 미적용 · 아래 표는 이전에 적용한 조회 결과입니다. 조회 적용을 누르면 새 조건으로 갱신됩니다."
          : "변경 사항 미적용 · 아래 표는 기본 미리보기입니다. 편집 중인 조건은 조회 적용 후 반영됩니다."
        : current ? "적용된 조회 결과 · 현재 Dataset 판본 기준"
          : "기본 미리보기 · 필터·정렬 조건을 적용하지 않은 결과입니다."}
    </p>}
    {displayed && <>
      {available.length > 12 && <div className="query-column-summary">
        <span className="hint">전체 {available.length}개 컬럼 중 {columns.length}개 표시 · {
          current?.explicitProjection ? "선택한 컬럼 전부 표시" : "조회한 값의 화면 표시 범위만 변경합니다."
        }</span>
        {!current?.explicitProjection && <button className="secondary"
          onClick={query.toggleAllColumns}>
          {showAllColumns ? "첫 12개 컬럼만 보기" : "전체 컬럼 보기"}
        </button>}
      </div>}
      <div className="table-scroll" tabIndex={0} role="region" aria-label="데이터 조회 결과 표"><table><thead><tr>
        {columns.map(c => <th key={c}>{c}<small>{displayed.schema[c]}</small></th>)}
      </tr></thead><tbody>{displayed.rows.map((r, i) => <tr key={i}>
        {columns.map(c => <td key={c}>{r[c] == null ? <span className="null">NULL</span> : String(r[c])}</td>)}
      </tr>)}</tbody></table></div>
      {displayed.rows.length === 0 && <p className="muted">조건에 맞는 행이 없습니다.</p>}
    </>}
    {previewStatus === "loading" && <p className="muted" role="status">미리보기를 불러오는 중입니다.</p>}
    {previewStatus === "error" && <p role="alert">{previewError}</p>}
    {previewStatus === "error" && <button className="secondary" onClick={onRetry}>미리보기 다시 시도</button>}
    <div className="hint">스키마와 파일·스냅샷 버전은 실행 입력으로 고정됩니다.</div>
  </section>;
}
