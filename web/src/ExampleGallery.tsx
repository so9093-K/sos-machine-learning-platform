import type { Example } from "./types";

type Props = {
  examples: Example[];
  busy: boolean;
  loading: boolean;
  onUse: (id: string) => void;
  canUse: boolean;
  startLabel?: string;
};
export function ExampleGallery({ examples, busy, loading, canUse, onUse, startLabel = "이 예제로 탐색 시작 →" }: Props) {
  return <section className="example-gallery" aria-label="예제 데이터">
    <div className="panel-head"><div>
      <h3>예제 데이터로 먼저 살펴보기</h3>
      <p>합성 데이터입니다. 선택할 때만 일반 데이터셋으로 등록됩니다.</p>
      {!canUse && <p className="muted">예제를 등록하려면 Dataset 생성 권한이 필요합니다. 기존 Dataset은 계속 조회할 수 있습니다.</p>}
    </div></div>
    {loading && <p className="muted" role="status">예제를 불러오는 중입니다.</p>}
    <div className="example-grid">
      {examples.map(item => <article className="example-card" key={item.id}>
        <span className="eyebrow">SAMPLE DATASET · {item.row_count} rows</span>
        <h4>{item.title}</h4>
        <p>{item.description}</p>
        <p className="muted">컬럼: {item.columns.join(" · ")}</p>
        <p className="guide">{item.guide}</p>
        <button disabled={busy || !canUse} onClick={() => onUse(item.id)}>
          {startLabel}
        </button>
      </article>)}
    </div>
  </section>;
}
