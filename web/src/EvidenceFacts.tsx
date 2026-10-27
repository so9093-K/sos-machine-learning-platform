/** 사용자에게 확인 가능한 짧은 기술 사실을 레이블·값으로 분리한다. */
export type EvidenceFact = { label: string; value: string; title?: string };

export function EvidenceFacts({ facts, compact = false }: {
  facts: readonly EvidenceFact[];
  compact?: boolean;
}) {
  return <dl className={compact ? "evidence-facts evidence-facts-compact" : "evidence-facts"}>
    {facts.map(fact => <div key={fact.label} title={fact.title}>
      <dt>{fact.label}</dt><dd>{fact.value}</dd>
    </div>)}
  </dl>;
}
