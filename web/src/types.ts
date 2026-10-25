/** Pydantic/OpenAPI에서 생성한 API 응답 타입. 직접 필드 중복 정의 금지. */
import type { components } from "./api.generated";

export type Identity = components["schemas"]["AuthMeOut"];
export type AuditEvent = components["schemas"]["AuditEventOut"];
export type JobDiagnostics = components["schemas"]["JobDiagnosticsOut"];
export type Dataset = components["schemas"]["DatasetOut"];
export type DatasetPage = components["schemas"]["DatasetPageOut"];
export type Snapshot = components["schemas"]["SnapshotOut"];
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

export type ExplorationJob = components["schemas"]["ExplorationJobOut"];
export type ExplorationJobSummary = components["schemas"]["ExplorationJobSummaryOut"];
export type ExplorationJobPage = components["schemas"]["ExplorationJobPageOut"];

export type MLTrainingJob = components["schemas"]["MLTrainingJobOut"];
export type MLTrainingJobSummary = components["schemas"]["MLTrainingJobSummaryOut"];
export type MLTrainingJobPage = components["schemas"]["MLTrainingJobPageOut"];
export type ModelRevision = components["schemas"]["ModelRevisionOut"];

export type MLPlaygroundJob = components["schemas"]["MLPlaygroundJobOut"];
export type MLScoringJob = components["schemas"]["MLScoringJobOut"];
export type MLScoringJobSummary = components["schemas"]["MLScoringJobSummaryOut"];
export type MLScoringJobPage = components["schemas"]["MLScoringJobPageOut"];
export type ScoringObservations = components["schemas"]["ScoringObservationsOut"];
export type ScoringResult = components["schemas"]["ScoringResultOut"];

export type MLPolicyJob = components["schemas"]["MLPolicyJobOut"];
export type MLEvaluationJob = components["schemas"]["MLEvaluationJobOut"];
export type MLExplanationJob = components["schemas"]["MLExplanationJobOut"];
export type DecisionPolicyRevision = components["schemas"]["DecisionPolicyRevisionOut"];
export type EvaluationRevision = components["schemas"]["EvaluationRevisionOut"];
export type ExplanationResult = components["schemas"]["ExplanationResultOut"];

export type MLflowExportStatus = components["schemas"]["MLflowExportStatusOut"];
export type MLflowExperiment = components["schemas"]["MLflowExperimentOut"];
export type MLflowExperimentPage = components["schemas"]["MLflowExperimentPageOut"];
export type MLflowRun = components["schemas"]["MLflowRunOut"];
export type MLflowRunPage = components["schemas"]["MLflowRunPageOut"];
export type MLflowModelVersion = components["schemas"]["MLflowModelVersionOut"];
export type MLflowModelVersionPage = components["schemas"]["MLflowModelVersionPageOut"];

export type MLPolicyJobSummary = components["schemas"]["MLPolicyJobSummaryOut"];
export type MLEvaluationJobSummary = components["schemas"]["MLEvaluationJobSummaryOut"];
export type MLExplanationJobSummary = components["schemas"]["MLExplanationJobSummaryOut"];

// Pipeline API 응답은 FastAPI OpenAPI에서 생성한 타입을 사용한다.
export type PipelineDefinition = components["schemas"]["PipelineDefinitionOut"];
export type PipelineCheck = components["schemas"]["PipelineCheckOut"];
export type PipelineRun = components["schemas"]["PipelineRunOut"];
export type PipelineJob = components["schemas"]["PipelineJobOut"];
export type PipelineCandidates = components["schemas"]["PipelineCandidatesOut"];
