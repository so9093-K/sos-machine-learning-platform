import { useEffect, useState } from "react";
import { formatError } from "./api";
import type { ExplorationJobSummary, JobSummary } from "./types";
import { usePagedJobs } from "./usePagedJobs";

const FINAL = ["Succeeded", "Failed", "Cancelled"];

/** 데이터 작업(일반·탐색) 이력. 조회 실패는 실행 실패와 구분하고 진행 중인 작업만 다시 조회한다. */
export function useDataJobs(historyOpen: boolean) {
  const jobs = usePagedJobs<JobSummary>("/api/jobs");
  const explorations = usePagedJobs<ExplorationJobSummary>("/api/explorations/jobs");
  const [loading, setLoading] = useState(true);
  const [jobsError, setJobsError] = useState("");
  const [explorationsError, setExplorationsError] = useState("");

  async function refreshAll() {
    setLoading(true);
    const results = await Promise.allSettled([jobs.refresh(), explorations.refresh()]);
    setJobsError(results[0].status === "rejected" ? formatError(results[0].reason) : "");
    setExplorationsError(results[1].status === "rejected" ? formatError(results[1].reason) : "");
    setLoading(false);
  }
  const retryJobs = () => jobs.refresh().then(() => setJobsError("")).catch(e => setJobsError(formatError(e)));
  const retryExplorations = () => explorations.refresh().then(() => setExplorationsError(""))
    .catch(e => setExplorationsError(formatError(e)));

  useEffect(() => { if (historyOpen) void refreshAll(); }, [historyOpen]);
  useEffect(() => {
    const handle = window.setInterval(() => {
      if (jobs.items.some(j => !FINAL.includes(j.status))) void retryJobs();
      if (explorations.items.some(j => !FINAL.includes(j.status))) void retryExplorations();
    }, 2000);
    return () => window.clearInterval(handle);
  }, [jobs.items, explorations.items]);

  return {
    jobs: jobs.items, jobsCursor: jobs.nextCursor, refreshJobs: jobs.refresh, loadOlderJobs: jobs.loadMore,
    explorations: explorations.items, explorationsCursor: explorations.nextCursor,
    refreshExplorations: explorations.refresh, loadOlderExplorations: explorations.loadMore,
    loading, jobsError, explorationsError, refreshAll, retryJobs, retryExplorations,
  };
}
