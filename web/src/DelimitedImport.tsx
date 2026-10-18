import { useEffect, useState } from "react";

type Props = {
  file: File;
  disabled: boolean;
  onConfirm: (file: File) => void;
  onCancel: () => void;
};

/** 업로드 전 일부 바이트의 확인용 미리보기. 서버의 전체 행 검증을 대신하지 않는다. */
function firstRows(text: string, maxRows: number): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  for (let i = 0; i < text.length && rows.length < maxRows; i++) {
    const ch = text[i];
    if (ch === "\t") {
      row.push(field); field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
    } else {
      field += ch;
    }
  }
  // 미완성인 마지막 행은 파일 앞부분만 읽을 때 완결되지 않을 수 있다.
  return rows;
}

export function DelimitedImport({ file, disabled, onConfirm, onCancel }: Props) {
  const [hasHeader, setHasHeader] = useState(false);
  const [rows, setRows] = useState<string[][]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void file.slice(0, 65536).text().then(content => {
      if (active) { setRows(firstRows(content, 5)); setError(""); }
    }).catch(() => { if (active) setError("파일 구조를 미리 읽을 수 없습니다."); });
    return () => { active = false; };
  }, [file]);
  const width = rows[0]?.length ?? 0;
  const labels = hasHeader ? rows[0] : Array.from({ length: width }, (_, i) => "unnamed_" + (i + 1));
  const examples = hasHeader ? rows.slice(1, 4) : rows.slice(0, 3);
  return <section className="panel" aria-label="파일 구조 확인">
    <div className="panel-head"><div><h3>파일 구조 확인 · 등록 전</h3>
      <p>{file.name} · 탭 구분 UTF-8 표만 지원하며 따옴표는 일반 문자로 취급합니다.</p>
    </div></div>
    <p className="muted">구분자: Tab · 원본 파일을 변경하지 않고 등록합니다. 아래는 파일 앞부분의 확인용 미리보기이며 서버에서 전체 행을 검증합니다.</p>
    <label>첫 행의 의미
      <select aria-label="첫 행의 의미" value={hasHeader ? "header" : "data"}
        onChange={e => setHasHeader(e.target.value === "header")} disabled={disabled}>
        <option value="data">첫 행도 데이터 (헤더 없음)</option>
        <option value="header">첫 행에 컬럼명 있음</option>
      </select>
    </label>
    {error && <p role="alert">{error}</p>}
    {!error && width >= 2 && <div className="table-scroll">
      <table><thead><tr>{labels.map((name, i) =>
        <th key={i}>{name || "(빈 컬럼명)"}</th>)}</tr></thead>
        <tbody>{examples.map((row, i) => <tr key={i}>{labels.map((_, j) =>
          <td key={j}>{row[j] ?? ""}</td>)}</tr>)}</tbody>
      </table>
    </div>}
    {width < 2 && !error && <p className="muted" role="status">
      탭으로 구분된 열을 확인할 수 없습니다. 표 형식이 아니면 등록이 거부됩니다.
    </p>}
    <p className="hint">헤더가 없으면 unnamed_1부터 중립적인 컬럼 이름을 부여합니다. 업무 의미를 추측하지 않습니다.</p>
    <div className="actions">
      <button disabled={disabled || !!error} onClick={() => onConfirm(new File(
        [file], file.name, { type: "text/tab-separated-values; header=" + (hasHeader ? "present" : "absent") }
      ))}>이 설정으로 Dataset 등록</button>
      <button className="secondary" disabled={disabled} onClick={onCancel}>취소</button>
    </div>
  </section>;
}
