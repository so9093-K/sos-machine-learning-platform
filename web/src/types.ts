/** Pydantic/OpenAPI에서 생성한 API 응답 타입. 직접 필드 중복 정의 금지. */
import type { components, operations } from "./api.generated";

export type Identity = components["schemas"]["AuthMeOut"];
export type AuditEvent = components["schemas"]["AuditEventOut"];
export type Dataset = components["schemas"]["DatasetOut"];
export type DatasetPage = components["schemas"]["DatasetPageOut"];
export type Snapshot = components["schemas"]["SnapshotOut"];
export type Preview = components["schemas"]["PreviewOut"];
export type Profile = components["schemas"]["ProfileOut"];
export type Job = components["schemas"]["JobOut"];
export type JobSummary = components["schemas"]["JobSummaryOut"];
export type JobPage = components["schemas"]["JobPageOut"];
/** 목록 필터에 쓸 수 있는 현재 Job 종류. 저장된 Job의 `kind`는 폐기된 기능의 이력도 담는 문자열이다. */
export type JobKind = NonNullable<NonNullable<operations["list_jobs_api_jobs_get"]["parameters"]["query"]>["kind"]>[number];
export type StorageInspection = components["schemas"]["StorageInspectionOut"];
export type IcebergReference = components["schemas"]["IcebergReferenceOut"];
export type UploadPolicy = components["schemas"]["UploadPolicyOut"];
export type DatasetOverview = components["schemas"]["DatasetOverviewOut"];
export type DatasetColumns = components["schemas"]["DatasetColumnsOut"];
export type Exploration = components["schemas"]["ExplorationOut"];
export type ExplorationRequest = components["schemas"]["ExplorationRequest"];

export type ModelRevision = components["schemas"]["ModelRevisionOut"] | components["schemas"]["PublicModelRevisionOut"];
export type ModelOffering = components["schemas"]["ModelOfferingOut"];
export type ModelOfferingPage = components["schemas"]["ModelOfferingPageOut"];
export type ModelOfferingState = components["schemas"]["ModelOfferingStateOut"];

export type ScoringObservations = components["schemas"]["ScoringObservationsOut"];
export type ScoringResult = components["schemas"]["ScoringResultOut"];

export type DecisionPolicyRevision = components["schemas"]["DecisionPolicyRevisionOut"];
export type EvaluationRevision = components["schemas"]["EvaluationRevisionOut"];
export type ModelEvaluationPage = components["schemas"]["ModelEvaluationPageOut"];
export type ExplanationResult = components["schemas"]["ExplanationResultOut"];

// 분석 주제 정의는 FastAPI OpenAPI에서 생성한 타입을 사용한다.
export type ServingEndpoint = components["schemas"]["ServingEndpointOut"];
export type ServingApplication = components["schemas"]["ServingApplicationOut"];
export type AnalysisTopic = components["schemas"]["AnalysisTopicOut"];
export type Prediction = components["schemas"]["PredictionOut"];
