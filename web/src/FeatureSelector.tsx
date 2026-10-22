import { useMemo, useState } from "react";
import type { DatasetColumns } from "./types";

type Column = DatasetColumns["columns"][number];

/** 컬럼 수에 관계없이 Feature 이름과 dtype을 함께 읽고 선택한다. */
export function FeatureSelector({ columns, selected, disabled, onChange }: {
  columns: Column[];
  selected: string[];
  disabled: boolean;
  onChange: (next: string[]) => void;
}) {
  const [search, setSearch] = useState("");
  const shown = useMemo(() => columns.filter(column =>
    column.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())), [columns, search]);
  const names = new Set(shown.map(column => column.name));
  const allShownSelected = shown.length > 0 && shown.every(column => selected.includes(column.name));
  return <div className="feature-selector">
    <div className="feature-selector-toolbar">
      <label>Feature 검색
        <input type="search" value={search} onChange={event => setSearch(event.target.value)}
          placeholder="컬럼 이름" aria-label="학습 Feature 검색" />
      </label>
      <span role="status">{selected.length.toLocaleString("ko-KR")}개 선택 · {shown.length.toLocaleString("ko-KR")}개 표시</span>
      <button type="button" className="secondary" disabled={disabled || shown.length === 0}
        onClick={() => onChange(allShownSelected
          ? selected.filter(name => !names.has(name))
          : [...new Set([...selected, ...shown.map(column => column.name)])])}>
        {allShownSelected ? "표시된 선택 해제" : "표시된 항목 선택"}
      </button>
    </div>
    <fieldset className="feature-selector-options" disabled={disabled}>
      <legend className="sr-only">학습 Feature 선택</legend>
      {shown.map(column => <label key={column.name} className="feature-selector-item">
        <input type="checkbox" checked={selected.includes(column.name)}
          onChange={event => onChange(event.target.checked
            ? [...selected, column.name] : selected.filter(name => name !== column.name))}/>
        <span className="feature-selector-name">{column.name}</span>
        <small>{column.dtype}</small>
      </label>)}
      {shown.length === 0 && <p className="muted">검색 조건에 맞는 Feature가 없습니다.</p>}
    </fieldset>
  </div>;
}
