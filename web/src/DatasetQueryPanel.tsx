/** 전체 원본 버전을 유지한 채 DuckDB Query 계약에 따라 작은 결과만 조회한다. */
import { useEffect, useRef, useState } from "react";
import { jsonPost, formatError } from "./api";
import type { Preview } from "./types";

type Props = {
  datasetId: string;
  columns: string[];
  busy: boolean;
  onResult: (value: Preview) => void;
  onInvalidate: () => void;
};

export function DatasetQueryPanel({ datasetId, columns, busy, onResult, onInvalidate }: Props) {
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
  const [chosen, setChosen] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [filterColumn, setFilterColumn] = useState("");
  const [filterValue, setFilterValue] = useState("");
  const [filterOp, setFilterOp] = useState("eq");
  const [sortColumn, setSortColumn] = useState("");
  const [descending, setDescending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [limit, setLimit] = useState(30);
  const found = columns.filter(column => column.toLowerCase().includes(search.toLowerCase()));

  async function apply() {
    if (loading) return;
    const version = requestVersion.current;
    setError(""); setLoading(true);
    try {
      const body = {
        ...(chosen.length ? { columns: columns.filter(c => chosen.includes(c)) } : {}),
        ...(filterColumn ? { filters: [{
          column: filterColumn, op: filterOp,
          ...(!["is_null", "is_not_null"].includes(filterOp) ? { value: filterValue } : {}),
        }] } : {}),
        ...(sortColumn ? { order_by: sortColumn, descending } : {}),
        limit,
      };
      const result = await jsonPost<Preview>(
        `/api/datasets/${encodeURIComponent(datasetId)}/query`, body,
      );
      if (mounted.current && version === requestVersion.current) onResult(result);
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
          onChange={event => { invalidate(); setLimit(Number(event.target.value)); }}>
          {[10, 30, 50, 100].map(n => <option key={n} value={n}>{n}</option>)}
        </select></label>
      </div>
      <p className="hint">컬럼을 선택하지 않으면 모든 컬럼을 반환합니다. 필터는 전체 Dataset 판본에 적용됩니다.</p>
      <div className="column-picks" role="group" aria-label="표시할 컬럼 선택">
        {found.map(name => <label key={name}>
          <input type="checkbox" checked={chosen.includes(name)}
            onChange={event => {
              invalidate();
              setChosen(current => event.target.checked
                ? [...current, name] : current.filter(c => c !== name));
            }} />{name}
        </label>)}
      </div>
      <div className="form-row">
        <label>필터 컬럼 <select aria-label="필터 컬럼" value={filterColumn}
          onChange={event => { invalidate(); setFilterColumn(event.target.value); }}>
          <option value="">필터 없음</option>
          {columns.map(c => <option key={c} value={c}>{c}</option>)}
        </select></label>
        <label>비교 연산 <select aria-label="비교 연산" value={filterOp}
          onChange={event => { invalidate(); setFilterOp(event.target.value); }}>
          {["eq", "ne", "lt", "lte", "gt", "gte", "is_null", "is_not_null"].map(op =>
            <option key={op} value={op}>{op}</option>)}
        </select></label>
        {filterColumn && !["is_null", "is_not_null"].includes(filterOp) &&
          <label>비교 값 <input aria-label="비교 값" value={filterValue}
            onChange={event => { invalidate(); setFilterValue(event.target.value); }} /></label>}
        <label>정렬 컬럼 <select aria-label="정렬 컬럼" value={sortColumn}
          onChange={event => { invalidate(); setSortColumn(event.target.value); }}>
          <option value="">원본 순서</option>
          {columns.map(c => <option key={c} value={c}>{c}</option>)}
        </select></label>
        <label>내림차순 <input aria-label="내림차순" type="checkbox" checked={descending}
          onChange={event => { invalidate(); setDescending(event.target.checked); }} /></label>
      </div>
      <button className="secondary" disabled={busy || loading} onClick={() => void apply()}>
        {loading ? "조회 중" : "조회 적용"}</button>
      {error && <p role="alert">{error}</p>}
    </details>
  </div>;
}
