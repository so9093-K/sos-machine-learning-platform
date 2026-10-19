import { useEffect, useRef, useState } from "react";
import { formatError, request } from "./api";
import type { Preview, Profile } from "./types";
import type { DatasetTab } from "./WorkspaceTabs";

type InspectionResult<T> = { datasetId: string; revision: number; data: T };
type LoadStatus = "loading" | "ready" | "error";

/** 필요 탭에서만 Preview·EDA Profile을 조회하며 데이터 귀속과 요청 경합을 관리한다. */
export function useDatasetInspection(
  datasetId: string, revision: number, tab: DatasetTab | "preparation" | "ml" | "jobs" | "storage" | "audit",
) {
  const [previewState, setPreviewState] = useState<InspectionResult<Preview> | null>(null);
  const [profileState, setProfileState] = useState<InspectionResult<Profile> | null>(null);
  const [previewStatus, setPreviewStatus] = useState<LoadStatus>("loading");
  const [profileStatus, setProfileStatus] = useState<LoadStatus>("loading");
  const [previewError, setPreviewError] = useState("");
  const [profileError, setProfileError] = useState("");
  const previewKey = useRef("");
  const profileKey = useRef("");
  useEffect(() => {
    setPreviewState(null); setProfileState(null);
    setPreviewStatus("loading"); setProfileStatus("loading");
    setPreviewError(""); setProfileError("");
    previewKey.current = ""; profileKey.current = "";
  }, [datasetId, revision]);
  useEffect(() => {
    if (!datasetId) return;
    let active = true;
    const key = datasetId + ":" + revision;
    let previewStarted = false, previewSettled = false;
    let profileStarted = false, profileSettled = false;
    if (["preview", "profile", "preparation"].includes(tab) && previewKey.current !== key) {
      previewKey.current = key;
      previewStarted = true;
      setPreviewStatus("loading"); setPreviewError("");
      request<Preview>(`/api/datasets/${encodeURIComponent(datasetId)}/preview?limit=100`)
        .then(data => {
          previewSettled = true;
          if (active) {
            setPreviewState({ datasetId, revision, data }); setPreviewStatus("ready");
          }
        }).catch(error => {
          previewSettled = true;
          if (active) { setPreviewError(formatError(error)); setPreviewStatus("error"); }
        });
    }
    if (tab === "profile" && profileKey.current !== key) {
      profileKey.current = key;
      profileStarted = true;
      setProfileStatus("loading"); setProfileError("");
      request<Profile>(`/api/datasets/${encodeURIComponent(datasetId)}/profile`)
        .then(data => {
          profileSettled = true;
          if (active) {
            setProfileState({ datasetId, revision, data }); setProfileStatus("ready");
          }
        }).catch(error => {
          profileSettled = true;
          if (active) { setProfileError(formatError(error)); setProfileStatus("error"); }
        });
    }
    return () => {
      active = false;
      if (previewStarted && !previewSettled && previewKey.current === key) previewKey.current = "";
      if (profileStarted && !profileSettled && profileKey.current === key) profileKey.current = "";
    };
  }, [datasetId, revision, tab]);
  return {
    preview: previewState?.datasetId === datasetId && previewState.revision === revision
      ? previewState.data : null,
    profile: profileState?.datasetId === datasetId && profileState.revision === revision
      ? profileState.data : null,
    previewStatus, profileStatus, previewError, profileError,
  };
}
