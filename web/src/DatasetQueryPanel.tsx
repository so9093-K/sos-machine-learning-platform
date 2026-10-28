/** 전체 원본 버전을 유지한 채 DuckDB Query 계약에 따라 작은 결과만 조회한다. */
import { useEffect, useRef, useState } from "react";
import { jsonPost, formatError } from "./api";
import type { Preview } from "./types";
import type { components } from "./api.generated";

type DatasetQuery = components["schemas"]["DatasetQuery"];
type FilterOp = components["schemas"]["DatasetFilter"]["op"];

export type QueryDraft = {
  chosen: string[];
  filterColumn: string;
  filterValue: string;
  filterOp: FilterOp;
  sortColumn: string;
  descending: boolean;
  limit: number;
};

export const emptyQueryDraft = (): QueryDraft => ({
  chosen: [], filterColumn: "", filterValue: "", filterOp: "eq",
  sortColumn: "", descending: false, limit: 30,
});

type Props = {
  datasetId: string;
  columns: string[];
  busy: boolean;
  draft: QueryDraft;
  onDraftChange: (draft: QueryDraft) => void;
  onResult: (value: Preview, explicitProjection: boolean) => void;
  onInvalidate: () => void;
};

export function DatasetQueryPanel({
  datasetId, columns, busy, draft, onDraftChange, onResult, onInvalidate,
}: Props) {
  const requestVersion = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; ++requestVersion.current; };
  }, []);
  function invalidate() {
    // 설정을 바꾼 뒤 늦게 도착한 이전 조회 결과를 현재 결과로 표시하지 않는다.
    ++requestVersion.current;
    onInvalidate();
  }
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { chosen, filterColumn, filterValue, filterOp, sortColumn, descending, limit } = draft;
  function edit(change: Partial<QueryDraft>) {
    invalidate();
    onDraftChange({ ...draft, ...change });
  }
  const found = columns.filter(column => column.toLowerCase().includes(search.toLowerCase()));

  async function apply() {
    if (loading) return;
    const version = requestVersion.current;
    setError(""); setLoading(true);
    try {
      const body: DatasetQuery = {
        ...(chosen.length ? { columns: columns.filter(c => chosen.includes(c)) } : {}),
        ...(filterColumn ? { filters: [{
          column: filterColumn, op: filterOp,
          ...(!["is_null", "is_not_null"].includes(filterOp) ? { value: filterValue } : {}),
        }] } : {}),
        ...(sortColumn ? { order_by: sortColumn } : {}),
        descending,
        limit,
      };
      const result = await jsonPost<Preview>(
        `/api/datasets/${encodeURIComponent(datasetId)}/query`, body,
      );
      if (mounted.current && version === requestVersion.current) onResult(result, chosen.length > 0);
    } catch (cause) {
      if (mounted.current && version === requestVersion.current) setError(formatError(cause));
    } finally { if (mounted.current) setLoading(false); }
  }

  return <div className="query-tools">
    <details><summary>컬럼·필터·정렬 선택</summary>
      <div className="form-row">
        <label>컬럼 검색 <input aria-label="컬럼 검색" value={search}
          onChange={event => { setSearch(event.target.value); }} placeholder="컬럼명 검색" /></label>
        <label>반환 행 수 <select aria-label="반환 행 수" value={limit}
          onChange={event => edit({ limit: Number(event.target.value) })}>
          {[10, 30, 50, 100].map(n => <option key={n} value={n}>{n}</option>)}
        </select></label>
      </div>
      <p className="hint">컬럼을 선택하지 않으면 모든 컬럼을 반환합니다. 필터는 전체 Dataset 판본에 적용됩니다.</p>
      <div className="column-picks" role="group" aria-label="표시할 컬럼 선택">
        {found.map(name => <label key={name}>
          <input type="checkbox" checked={chosen.includes(name)}
            onChange={event => {
              edit({ chosen: event.target.checked
                ? [...chosen, name] : chosen.filter(c => c !== name) });
            }} />{name}
        </label>)}
      </div>
      <div className="form-row">
        <label>필터 컬럼 <select aria-label="필터 컬럼" value={filterColumn}
          onChange={event => edit({ filterColumn: event.target.value })}>
          <option value="">필터 없음</option>
          {columns.map(c => <option key={c} value={c}>{c}</option>)}
        </select></label>
        <label>비교 연산 <select aria-label="비교 연산" value={filterOp}
          onChange={event => edit({ filterOp: event.target.value as FilterOp })}>
          {["eq", "ne", "lt", "lte", "gt", "gte", "is_null", "is_not_null"].map(op =>
            <option key={op} value={op}>{{ eq: "같음 (=)", ne: "다름 (≠)", lt: "미만 (<)", lte: "이하 (≤)",
              gt: "초과 (>)", gte: "이상 (≥)", is_null: "결측값", is_not_null: "결측값 제외" }[op]}</option>)}
        </select></label>
        {filterColumn && !["is_null", "is_not_null"].includes(filterOp) &&
          <label>비교 값 <input aria-label="비교 값" value={filterValue}
            onChange={event => edit({ filterValue: event.target.value })} /></label>}
        <label>정렬 컬럼 <select aria-label="정렬 컬럼" value={sortColumn}
          onChange={event => edit({ sortColumn: event.target.value })}>
          <option value="">정렬 지정 없음</option>
          {columns.map(c => <option key={c} value={c}>{c}</option>)}
        </select></label>
        <label>내림차순 <input aria-label="내림차순" type="checkbox" checked={descending}
          onChange={event => edit({ descending: event.target.checked })} /></label>
      </div>
      <button className="secondary" disabled={busy || loading} onClick={() => void apply()}>
        {loading ? "조회 중" : "조회 적용"}</button>
      {error && <p role="alert">{error}</p>}
    </details>
  </div>;
}
