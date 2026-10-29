import { useId, useState, type ReactNode } from "react";
import { activityFeatureLabel } from "./ActivityFeatureLabels";

/** 전체 근거는 검색과 짧은 페이지로 확인하며 결과 화면을 길게 늘리지 않는다. */
export function FeatureResultDetails({ summary, columns, rows, open = false }: {
  summary: string;
  open?: boolean;
  columns: string[];
  rows: Array<{ feature: string; values: ReactNode[] }>;
}) {
  const searchId = useId();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const term = search.trim().toLocaleLowerCase();
  const filtered = rows.filter(row =>
    [row.feature, activityFeatureLabel(row.feature)].some(value => value.toLocaleLowerCase().includes(term)));
  const pageCount = Math.max(1, Math.ceil(filtered.length / 6));
  const currentPage = Math.min(page, pageCount - 1);
  const shown = filtered.slice(currentPage * 6, (currentPage + 1) * 6);
  return <details className="feature-result-details" open={open}>
    <summary>{summary}</summary>
    <div className="feature-result-toolbar">
      <label htmlFor={searchId}>항목 찾기</label>
      <input id={searchId} type="search" value={search} placeholder="이름으로 검색"
        onChange={event => { setSearch(event.target.value); setPage(0); }} />
      <span role="status">{filtered.length}개 중 {shown.length ? currentPage * 6 + 1 : 0}–{currentPage * 6 + shown.length}개</span>
    </div>
    {shown.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label="Feature 값 표"><table>
      <thead><tr><th>항목</th>{columns.map(column => <th key={column}>{column}</th>)}</tr></thead>
      <tbody>{shown.map(row => <tr key={row.feature}>
        <th title={row.feature}>{activityFeatureLabel(row.feature)}
          {activityFeatureLabel(row.feature) !== row.feature && <small>{row.feature}</small>}</th>
        {row.values.map((value, index) => <td key={index}>{value}</td>)}
      </tr>)}</tbody>
    </table></div> : <p className="hint">검색 조건에 맞는 항목이 없습니다.</p>}
    {pageCount > 1 && <div className="feature-result-pages">
      <button type="button" className="secondary" disabled={currentPage === 0}
        onClick={() => setPage(currentPage - 1)}>이전 항목</button>
      <span>{currentPage + 1} / {pageCount}</span>
      <button type="button" className="secondary" disabled={currentPage === pageCount - 1}
        onClick={() => setPage(currentPage + 1)}>다음 항목</button>
    </div>}
  </details>;
}
