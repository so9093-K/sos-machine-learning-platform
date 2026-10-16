import { useEffect, useState } from "react";
import { formatError, jsonPost } from "./api";
import { createStep, needsTimeOrder, type FeatureStep } from "./feature-utils";
import type { Preview } from "./types";

/** Feature 편집 초안과 변환 미리보기의 유일한 소유자. 탭 이동에도 보존한다. */
export function useFeatureEditor({
  datasetId, preview, onError, withFeedback,
}: {
  datasetId: string;
  preview: Preview | null;
  onError: (message: string) => void;
  withFeedback: (action: () => Promise<void>) => Promise<void>;
}) {
  const [steps, setSteps] = useState<FeatureStep[]>([]);
  const [editingStep, setEditingStep] = useState<number | null>(null);
  const [lagPeriods, setLagPeriods] = useState(1);
  const [rollingWindow, setRollingWindow] = useState(3);
  const [op, setOp] = useState("filter_not_null");
  const [column, setColumn] = useState("");
  const [value, setValue] = useState("");
  const [orderBy, setOrderBy] = useState("");
  const [groupBy, setGroupBy] = useState<string[]>([]);
  const [chosenColumns, setChosenColumns] = useState<string[]>([]);
  const [previewResult, setPreviewResult] = useState<{ signature: string; data: Preview } | null>(null);
  useEffect(() => {
    setPreviewResult(null); setSteps([]); setEditingStep(null);
    setColumn(""); setOrderBy(""); setGroupBy([]); setChosenColumns([]);
  }, [datasetId]);
  const transform = {
    steps,
    ...(orderBy ? { order_by: orderBy } : {}),
    ...(groupBy.length ? { group_by: groupBy } : {}),
  };
  // 늦게 돌아온 변환 응답도 원본과 실제 Recipe가 일치할 때만 보인다.
  const signature = JSON.stringify({ dataset_id: datasetId, transform });
  const featurePreview = previewResult?.signature === signature ? previewResult.data : null;
  function addStep() {
    try {
      const next = createStep(op, column, value, preview?.schema[column],
        op === "lag" ? lagPeriods : op === "rolling_mean" ? rollingWindow : undefined,
        chosenColumns);
      setSteps(existing => editingStep === null ? [...existing, next]
        : existing.map((step, index) => index === editingStep ? next : step));
      setEditingStep(null); setPreviewResult(null); onError("");
    } catch (e) { onError(formatError(e)); }
  }
  function editStep(index: number) {
    const step = steps[index];
    if (!step) return;
    setEditingStep(index);
    setOp(step.op); setColumn(step.column ?? "");
    setValue(step.value == null ? "" : String(step.value));
    setLagPeriods(step.periods ?? 1); setRollingWindow(step.window ?? 3);
    setChosenColumns(step.columns ?? []);
  }
  function moveStep(index: number, direction: -1 | 1) {
    const destination = index + direction;
    if (destination < 0 || destination >= steps.length) return;
    setSteps(current => {
      const copy = [...current];
      [copy[index], copy[destination]] = [copy[destination], copy[index]];
      return copy;
    });
    setEditingStep(null); setPreviewResult(null);
  }
  function deleteStep(index: number) {
    setSteps(current => current.filter((_, i) => i !== index));
    setEditingStep(null); setPreviewResult(null);
  }
  async function previewFeatures() {
    if (!datasetId || steps.length === 0) return;
    if (needsTimeOrder(steps) && !orderBy) {
      onError("Lag와 Rolling 변환에는 정렬 컬럼을 지정해야 합니다."); return;
    }
    await withFeedback(async () => {
      const data = await jsonPost<Preview>("/api/features/preview",
        { dataset_id: datasetId, transform });
      setPreviewResult({ signature, data });
    });
  }
  return {
    steps, editingStep, lagPeriods, setLagPeriods, rollingWindow, setRollingWindow,
    op, setOp, column, setColumn, value, setValue, orderBy, setOrderBy,
    groupBy, setGroupBy, chosenColumns, setChosenColumns, transform,
    featurePreview, addStep, editStep, moveStep, deleteStep,
    cancelEdit: () => setEditingStep(null), previewFeatures,
  };
}
