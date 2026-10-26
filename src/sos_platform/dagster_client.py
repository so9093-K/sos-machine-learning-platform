"""Dagster OSS GraphQL 경계. SOS Core는 Dagster Python Runtime에 의존하지 않는다."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class DagsterUnavailable(RuntimeError):
    """Dagster에서 작업 상태를 확인하지 못했다."""


class DagsterClient:
    def __init__(self, url: str | None = None):
        self.url = url or os.environ.get("SOS_DAGSTER_GRAPHQL_URL", "http://dagster-webserver:3000/graphql")

    def _execute(self, query: str, variables: dict | None = None) -> dict:
        payload = json.dumps({"query": query, "variables": variables or {}}).encode()
        request = Request(
            self.url, data=payload, headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                document = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            raise DagsterUnavailable("Dagster 실행 서버를 사용할 수 없습니다.") from exc
        if document.get("errors"):
            raise DagsterUnavailable("Dagster GraphQL 계약 오류: " + str(document["errors"]))
        return document.get("data") or {}

    def find_by_submission(self, submission_id: str) -> str | None:
        query = """query($id: String!) {
          runsOrError(
            filter: {jobName: "activity_anomaly_workflow", tags: [
              {key: "sos/submission_id", value: $id}
            ]}, limit: 2
          ) {
            __typename
            ... on Runs {results {runId}}
          }
        }"""
        result = self._execute(query, {"id": submission_id}).get("runsOrError") or {}
        if result.get("__typename") != "Runs":
            raise DagsterUnavailable("Dagster Run 목록을 확인하지 못했습니다.")
        runs = result.get("results") or []
        if len(runs) > 1:
            raise DagsterUnavailable("동일한 SOS 제출로 여러 Dagster Run이 확인됐습니다.")
        return runs[0]["runId"] if runs else None

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
        result = self._execute(mutation, {"id": submission_id}).get("launchRun") or {}
        if result.get("__typename") != "LaunchRunSuccess":
            raise DagsterUnavailable("Dagster 실행 접수에 실패했습니다: " + str(result))
        return result["run"]["runId"]

    def status(self, run_id: str) -> str:
        query = """query($id: ID!) {
          runOrError(runId: $id) {
            __typename
            ... on Run {runId status}
          }
        }"""
        result = self._execute(query, {"id": run_id}).get("runOrError") or {}
        if result.get("__typename") != "Run":
            raise DagsterUnavailable("Dagster Run을 조회하지 못했습니다.")
        return result["status"]

    def terminate(self, run_id: str) -> None:
        mutation = """mutation($id: String!) {
          terminateRun(runId: $id) {
            __typename
            ... on TerminateRunFailure {message}
            ... on PythonError {message}
          }
        }"""
        result = self._execute(mutation, {"id": run_id}).get("terminateRun") or {}
        if result.get("__typename") not in ("TerminateRunSuccess", "TerminateRunFailure"):
            raise DagsterUnavailable("Dagster 종료 요청에 실패했습니다: " + str(result))
        if result["__typename"] == "TerminateRunFailure":
            raise DagsterUnavailable(result.get("message") or "Dagster 종료를 요청할 수 없습니다.")
