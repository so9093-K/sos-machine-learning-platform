"""Dagster OSS GraphQL 경계. SOS Core는 Dagster Python Runtime에 의존하지 않는다."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class DagsterUnavailable(RuntimeError):
    """Dagster 요청 실패의 진단 분류만 보존한다. 원문 응답은 노출하지 않는다."""

    def __init__(self, message: str, *, operation: str = "unknown",
                 failure_type: str = "dagster_response",
                 http_status: int | None = None) -> None:
        super().__init__(message)
        self.operation = operation
        self.failure_type = failure_type
        self.http_status = http_status


class DagsterClient:
    def __init__(self, url: str | None = None):
        self.url = url or os.environ.get(
            "SOS_DAGSTER_GRAPHQL_URL", "http://dagster-webserver:3000/graphql"
        )

    def _execute(self, query: str, variables: dict | None = None,
                 *, operation: str = "unknown") -> dict:
        payload = json.dumps({"query": query, "variables": variables or {}}).encode()
        request = Request(
            self.url, data=payload, headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                document = json.loads(response.read())
        except HTTPError as exc:
            raise DagsterUnavailable(
                "Dagster GraphQL 요청을 처리하지 못했습니다.",
                operation=operation, failure_type="http_error",
                http_status=exc.code,
            ) from exc
        except TimeoutError as exc:
            raise DagsterUnavailable(
                "Dagster 응답이 시간 내에 도착하지 않았습니다.",
                operation=operation, failure_type="timeout",
            ) from exc
        except URLError as exc:
            # urllib은 소켓 타임아웃을 URLError로 감싸기도 한다.
            timeout = isinstance(exc.reason, TimeoutError)
            raise DagsterUnavailable(
                "Dagster 응답이 시간 내에 도착하지 않았습니다."
                if timeout else "Dagster에 연결하지 못했습니다.",
                operation=operation,
                failure_type="timeout" if timeout else "connection",
            ) from exc
        except OSError as exc:
            # 연결 재설정·조기 종료처럼 urllib이 URLError로 감싸지 않은 경우.
            raise DagsterUnavailable(
                "Dagster 연결 중 오류가 발생했습니다.",
                operation=operation, failure_type="connection",
            ) from exc
        except (ValueError, UnicodeError, TypeError) as exc:
            raise DagsterUnavailable(
                "Dagster 응답 형식을 확인할 수 없습니다.",
                operation=operation, failure_type="invalid_response",
            ) from exc
        if not isinstance(document, dict):
            raise DagsterUnavailable(
                "Dagster 응답 형식을 확인할 수 없습니다.",
                operation=operation, failure_type="invalid_response",
            )
        if document.get("errors"):
            raise DagsterUnavailable(
                "Dagster GraphQL 계약 또는 실행 오류가 발생했습니다.",
                operation=operation, failure_type="graphql_error",
            )
        data = document.get("data")
        if not isinstance(data, dict):
            raise DagsterUnavailable(
                "Dagster 응답의 data를 확인할 수 없습니다.",
                operation=operation, failure_type="invalid_response",
            )
        return data

    def find_by_submission(self, submission_id: str) -> str | None:
        # RunsFilter에는 jobName이 없다. 제출 태그로 조회한 뒤 Run의 Job을 확인한다.
        query = """query($id: String!) {
          runsOrError(
            filter: {tags: [{key: "sos/submission_id", value: $id}]}, limit: 2
          ) {
            __typename
            ... on Runs {results {runId jobName}}
          }
        }"""
        result = self._execute(
            query, {"id": submission_id}, operation="run_lookup"
        ).get("runsOrError")
        if not isinstance(result, dict):
            raise DagsterUnavailable(
                "Dagster Run 조회 응답의 구조가 올바르지 않습니다.",
                operation="run_lookup", failure_type="invalid_response",
            )
        if result.get("__typename") != "Runs":
            raise DagsterUnavailable(
                "Dagster Run 목록을 확인하지 못했습니다.",
                operation="run_lookup", failure_type="unexpected_result",
            )
        runs = result.get("results")
        if not isinstance(runs, list):
            raise DagsterUnavailable(
                "Dagster Run 검색 결과가 올바르지 않습니다.",
                operation="run_lookup", failure_type="invalid_response",
            )
        if len(runs) > 1:
            raise DagsterUnavailable(
                "동일한 SOS 제출로 여러 Dagster Run이 확인됐습니다.",
                operation="run_lookup", failure_type="duplicate_submission",
            )
        if not runs:
            return None
        run = runs[0]
        if not isinstance(run, dict):
            raise DagsterUnavailable(
                "Dagster Run 조회 응답의 구조가 올바르지 않습니다.",
                operation="run_lookup", failure_type="invalid_response",
            )
        if run.get("jobName") != "activity_anomaly_workflow":
            raise DagsterUnavailable(
                "SOS 제출에 연결된 Dagster Job이 다릅니다.",
                operation="run_lookup", failure_type="job_mismatch",
            )
        if not isinstance(run.get("runId"), str) or not run["runId"]:
            raise DagsterUnavailable(
                "Dagster Run 식별자가 올바르지 않습니다.",
                operation="run_lookup", failure_type="invalid_response",
            )
        return run["runId"]

    def launch(self, submission_id: str) -> str:
        mutation = """mutation($id: String!) {
          launchRun(executionParams: {
            selector: {
              repositoryLocationName: "sos"
              repositoryName: "__repository__"
              jobName: "activity_anomaly_workflow"
            }
            runConfigData: {}
            executionMetadata: {tags: [
              {key: "sos/submission_id", value: $id}
            ]}
          }) {
            __typename
            ... on LaunchRunSuccess {run {runId}}
            ... on RunConfigValidationInvalid {errors {message}}
            ... on PythonError {message}
          }
        }"""
        result = self._execute(
            mutation, {"id": submission_id}, operation="run_launch"
        ).get("launchRun")
        if not isinstance(result, dict):
            raise DagsterUnavailable(
                "Dagster 실행 접수 응답이 올바르지 않습니다.",
                operation="run_launch", failure_type="invalid_response",
            )
        if result.get("__typename") != "LaunchRunSuccess":
            raise DagsterUnavailable(
                "Dagster 실행을 접수하지 못했습니다.",
                operation="run_launch", failure_type="unexpected_result",
            )
        run = result.get("run")
        if not isinstance(run, dict) or not isinstance(run.get("runId"), str) or not run["runId"]:
            raise DagsterUnavailable(
                "Dagster 실행 접수 응답에 Run 식별자가 없습니다.",
                operation="run_launch", failure_type="invalid_response",
            )
        return run["runId"]

    def status(self, run_id: str) -> str:
        query = """query($id: ID!) {
          runOrError(runId: $id) {
            __typename
            ... on Run {runId status}
          }
        }"""
        result = self._execute(
            query, {"id": run_id}, operation="run_status"
        ).get("runOrError")
        if not isinstance(result, dict):
            raise DagsterUnavailable(
                "Dagster Run 상태 응답이 올바르지 않습니다.",
                operation="run_status", failure_type="invalid_response",
            )
        if result.get("__typename") != "Run":
            raise DagsterUnavailable(
                "Dagster Run을 조회하지 못했습니다.",
                operation="run_status", failure_type="unexpected_result",
            )
        if not isinstance(result.get("status"), str) or not result["status"]:
            raise DagsterUnavailable(
                "Dagster Run 상태를 확인할 수 없습니다.",
                operation="run_status", failure_type="invalid_response",
            )
        return result["status"]

    def terminate(self, run_id: str) -> None:
        mutation = """mutation($id: String!) {
          terminateRun(runId: $id) {
            __typename
            ... on TerminateRunFailure {message}
            ... on PythonError {message}
          }
        }"""
        result = self._execute(
            mutation, {"id": run_id}, operation="run_terminate"
        ).get("terminateRun")
        if not isinstance(result, dict):
            raise DagsterUnavailable(
                "Dagster 취소 응답이 올바르지 않습니다.",
                operation="run_terminate", failure_type="invalid_response",
            )
        if result.get("__typename") != "TerminateRunSuccess":
            raise DagsterUnavailable(
                "Dagster 종료 요청을 확인하지 못했습니다.",
                operation="run_terminate", failure_type="unexpected_result",
            )
