/** UI 전용 상태를 제외한 타입은 OpenAPI 응답 계약과 일치하도록 유지한다. */
export type Dataset = {
  id: string; name: string; kind: "file" | "iceberg";
  version: string; location: string; created_at: string;
};
export type Preview = {
  schema: Record<string, string>; rows: Record<string, unknown>[];
  returned_rows: number;
};
export type Metric = {
  name: string; dtype: string; nulls: number; nan_count: number | null;
  unique: number; min?: number | null; max?: number | null; mean?: number | null;
};
export type Profile = {
  sampled: boolean; sample_limit: number | null; row_count: number; columns: Metric[];
};
export type Job = {
  id: string; kind: string; status: string; cancel_requested: boolean;
  dataset_id: string; result: Record<string, unknown> | null; error: string | null;
  attempts: { number: number; status: string }[];
};
export type Example = {
  id: string; title: string; description: string; columns: string[];
  guide: string; row_count: number; version: string;
};
