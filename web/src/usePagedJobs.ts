import { useRef, useState } from "react";
import { request } from "./api";
import { mergeById } from "./collections";

type Page<T> = { items: T[]; next_cursor: string | null };

/** 서로 다른 Job 계약을 유지하면서 페이지 병합·경합·기존 페이지 보존을 공통화한다. */
export function usePagedJobs<T extends { id: string }>(endpoint: string) {
  const [items, setItems] = useState<T[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const generation = useRef(0);
  const retainedOldPages = useRef(false);

  async function refresh(): Promise<void> {
    const current = ++generation.current;
    const page = await request<Page<T>>(`${endpoint}?limit=20`);
    // 수동 갱신과 폴링이 겹치면 이미 시작된 더 최근 요청이 우선이다.
    if (generation.current !== current) return;
    setItems(previous => retainedOldPages.current ? mergeById(page.items, previous) : page.items);
    if (!retainedOldPages.current) setNextCursor(page.next_cursor ?? null);
  }

  async function loadMore(): Promise<void> {
    if (!nextCursor) return;
    const current = generation.current;
    const page = await request<Page<T>>(
      `${endpoint}?limit=20&cursor=${encodeURIComponent(nextCursor)}`);
    if (generation.current !== current) return;
    setItems(previous => mergeById(previous, page.items));
    setNextCursor(page.next_cursor ?? null);
    retainedOldPages.current = true;
  }

  return { items, nextCursor, refresh, loadMore };
}
