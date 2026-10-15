/** Pydantic/OpenAPI에서 생성한 API 응답 타입. 직접 필드 중복 정의 금지. */
import type { components } from "./api.generated";

export type Dataset = components["schemas"]["DatasetOut"];
export type Preview = components["schemas"]["PreviewOut"];
export type Profile = components["schemas"]["ProfileOut"];
export type Job = components["schemas"]["JobOut"];
export type JobSummary = components["schemas"]["JobSummaryOut"];
export type JobPage = components["schemas"]["JobPageOut"];
export type Example = components["schemas"]["ExampleOut"];
export type StorageInspection = components["schemas"]["StorageInspectionOut"];
export type StoragePolicy = components["schemas"]["StoragePolicyOut"];
export type IcebergReference = components["schemas"]["IcebergReferenceOut"];
export type UploadPolicy = components["schemas"]["UploadPolicyOut"];
export type DatasetOverview = components["schemas"]["DatasetOverviewOut"];
export type DatasetColumns = components["schemas"]["DatasetColumnsOut"];
export type Exploration = components["schemas"]["ExplorationOut"];
export type ExplorationRequest = components["schemas"]["ExplorationRequest"];
