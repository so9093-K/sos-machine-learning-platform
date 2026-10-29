import { useCallback, useEffect, useRef, useState } from "react";
import { describeError, formatError, request, type ErrorNotice } from "./api";
import { mergeById } from "./collections";
import type { Dataset, DatasetPage } from "./types";

/** 여러 화면이 공유하는 Dataset 목록. 페이지 병합과 오래된 응답의 덮어쓰기를 막는다. */
export function useDatasetCatalog() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(true);
  const [readError, setReadError] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const requestVersion = useRef(0);

  async function refresh(): Promise<"updated" | "superseded"> {
    const version = ++requestVersion.current;
    setLoading(true);
    let page: DatasetPage;
    try {
      page = await request<DatasetPage>("/api/datasets?limit=20");
    } catch (error) {
      // 새 등록·더 최신 조회에서 대체된 실패는 현재 화면의 오류가 아니다.
      if (version !== requestVersion.current) return "superseded";
      throw error;
    } finally { if (version === requestVersion.current) setLoading(false); }
    // 오래된 GET 응답이 최근 등록·조회 후 확정된 Dataset 선택을 덮지 못하게 한다.
    if (version !== requestVersion.current) return "superseded";
    // 이미 조회한 옛 Dataset과 결과 화면의 현재 선택은 최신 20개 밖이어도 보존한다.
    setDatasets(previous => mergeById(page.items, previous));
    setNextCursor(page.next_cursor ?? null);
    return "updated";
  }

  /** 목록 조회 실패는 화면 작업 실패가 아니므로 별도 읽기 오류로 보관한다. */
  function retry() {
    return refresh().then(() => setReadError("")).catch(e => setReadError(formatError(e)));
  }

  async function loadMore() {
    if (!nextCursor) return;
    const version = requestVersion.current;
    const page = await request<DatasetPage>(`/api/datasets?limit=20&cursor=${encodeURIComponent(nextCursor)}`);
    if (version !== requestVersion.current) return;
    setDatasets(previous => mergeById(previous, page.items));
    setNextCursor(page.next_cursor ?? null);
  }

  /** 등록·개별 조회로 확인한 Dataset을 목록 맨 앞에 반영한다. */
  const upsert = useCallback((item: Dataset) => {
    setDatasets(previous => [item, ...previous.filter(entry => entry.id !== item.id)]);
  }, []);

  useEffect(() => { void retry(); }, []);

  return { datasets, loading, readError, nextCursor, refresh, retry, loadMore, upsert };
}

/** URL로 지정했지만 첫 목록 페이지에 없는 Dataset을 개별 조회한다.
 *
 *  refreshKey가 바뀌면(탭 이동·다시 불러오기) 이전 조회 실패를 지우고 다시 확인한다.
 */
export function useRequestedDataset(
  requestedId: string, loaded: boolean, enabled: boolean, refreshKey: string,
  upsert: (item: Dataset) => void,
) {
  const [error, setError] = useState<ErrorNotice | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    setError(null);
    if (!requestedId || loaded) { setLoading(false); return; }
    let active = true;
    setLoading(true);
    void request<Dataset>(`/api/datasets/${encodeURIComponent(requestedId)}`)
      .then(item => { if (active) upsert(item); })
      .catch(e => { if (active) setError(describeError(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [enabled, requestedId, loaded, refreshKey, upsert]);
  return { error, loading };
}
