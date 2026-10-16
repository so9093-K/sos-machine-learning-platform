/** 최신 페이지의 동일 ID 레코드가 우선하며, 과거에 로드한 레코드는 보존한다. */
export function mergeById<T extends { id: string }>(
  preferred: readonly T[], fallback: readonly T[],
): T[] {
  const existing = new Set(preferred.map(item => item.id));
  return [...preferred, ...fallback.filter(item => !existing.has(item.id))];
}
