import type { Example } from "./types";

type Props = {
  examples: Example[];
  busy: boolean;
  loading: boolean;
  error?: string;
  onRetry?: () => void;
  onUse: (id: string) => void;
  startLabel?: string;
};
export function ExampleGallery({ examples, busy, loading, error, onRetry, onUse, startLabel = "이 예제로 탐색 시작 →" }: Props) {
  return <section className="example-gallery" aria-label="예제 데이터">
    <div className="panel-head"><div>
      <h3>예제 데이터</h3>
      <p>합성 데이터입니다. 선택할 때만 일반 데이터셋으로 등록됩니다.</p>
    </div></div>
    {loading && <p className="muted" role="status">예제를 불러오는 중입니다.</p>}
    {!loading && error && <div className="notice-blocked" role="alert">
      <p>예제 목록을 불러오지 못했습니다.</p>
      {onRetry && <button type="button" className="secondary" onClick={onRetry}>
        예제 다시 조회
      </button>}
    </div>}
    {!loading && !error && examples.length === 0 && <p>현재 제공되는 예제가 없습니다.</p>}
    <div className="example-grid">
      {examples.map(item => <article className="example-card" key={item.id}>
        <span className="eyebrow">SAMPLE DATASET · {item.row_count} rows</span>
        <h4>{item.title}</h4>
        <p>{item.description}</p>
        <p className="muted">컬럼: {item.columns.join(" · ")}</p>
        <p className="guide">{item.guide}</p>
        <button disabled={busy} onClick={() => onUse(item.id)}>
          {startLabel}
        </button>
      </article>)}
    </div>
  </section>;
}
