"""처음 보는 Dataset의 Profile·추천·보존·재사용을 검증한다."""

from sos_platform.understanding import role_candidates, suggest_analyses
from sos_platform.worker import run_once


def _upload(client, name, content):
    response = client.post(
        "/api/datasets/files", files={"file": (name, content, "application/octet-stream")}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_role_inference_never_confirms_meaning():
    roles = role_candidates("ts", "String", 10, 10)
    assert any(role["role"] == "temporal" for role in roles)
    assert all(role["confirmed"] is False for role in roles)
    assert any(
        role["role"] == "potentially_sensitive"
        for role in role_candidates("user_name", "String", 10, 10)
    )


def test_suggestions_only_emit_supported_operations():
    columns = [
        {
            "name": "group",
            "roles": [{"role": "categorical"}],
            "is_constant": False,
            "distinct_count": 2,
            "null_count": 0,
        },
        {
            "name": "value",
            "roles": [{"role": "numeric_measure"}],
            "is_constant": False,
            "distinct_count": 4,
            "null_count": 0,
        },
    ]
    proposals = suggest_analyses(columns, 10)
    assert proposals
    assert all(
        p["request"]["metric"] in {"count", "null_count", "mean", "min", "max"} for p in proposals
    )
    assert proposals[0]["request"]["group_by"] == "group"
    assert suggest_analyses([], 0) == []


def test_dataset_understanding_job_version_and_executable_suggestions(workspace):
    client, store = workspace
    dataset = _upload(
        client,
        "unknown.csv",
        b"ts,department,score,user_name,fixed\n"
        b"2026-01-01T00:00:00Z,alpha,2.0,person_a,same\n"
        b"2026-01-01T00:01:00Z,beta,5.0,person_b,same\n"
        b"2026-01-01T00:02:00Z,alpha,,person_c,same\n"
        b"2026-01-01T00:03:00Z,beta,7.0,person_d,same\n",
    )
    path = "/api/datasets/" + dataset["id"] + "/understanding"
    before = client.get(path)
    assert before.status_code == 200, before.text
    assert before.json()["profile"] is None
    submit = client.post(path + "/jobs", json={"scope": "sample"})
    assert submit.status_code == 202, submit.text
    assert submit.json()["kind"] == "profile"
    assert run_once(store)
    result = client.get(path)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["version"] == dataset["version"]
    assert body["job_status"] == "Succeeded"
    assert body["profile"]["scope"] == "sample"
    assert body["profile"]["sampling_method"] == "reservoir"
    assert body["profile"]["row_count"] == 4
    columns = {c["name"]: c for c in body["profile"]["columns"]}
    assert columns["score"]["null_count"] == 1
    assert columns["score"]["distinct_exact"] is True
    assert columns["fixed"]["is_constant"] is True
    assert any(r["role"] == "temporal" for r in columns["ts"]["roles"])
    assert "person_a" not in str(body)
    assert "alpha" not in str(body)

    proposals = body["profile"]["insights"]
    assert proposals
    for item in proposals:
        answer = client.post(
            "/api/datasets/" + dataset["id"] + "/explorations",
            json=item["request"],
        )
        assert answer.status_code == 200, answer.text

    full = client.post(path + "/jobs", json={"scope": "full"})
    assert full.status_code == 202, full.text
    assert run_once(store)
    after = client.get(path).json()
    assert after["profile"]["scope"] == "full"
    assert after["profile"]["row_count"] == 4
    assert all(c["distinct_exact"] is False for c in after["profile"]["columns"])
    assert {c["name"]: c for c in after["profile"]["columns"]}["fixed"]["is_constant"]
    assert store.latest_dataset_profile(dataset["id"], dataset["version"]) is not None

    # 완료된 최신 결과를 우선 조회한다. 전체 결과가 과거라는 이유로 샘플을 가리면 안 된다.
    rerun = client.post(path + "/jobs", json={"scope": "sample"})
    assert rerun.status_code == 202, rerun.text
    assert run_once(store)
    recent = client.get(path).json()
    assert recent["profile"]["scope"] == "sample"
    assert recent["job_id"] == rerun.json()["id"]


def test_cancel_understanding_preserves_dataset_and_previous_profile(workspace):
    client, store = workspace
    dataset = _upload(client, "data.csv", b"group,value\na,1\nb,2\n")
    path = "/api/datasets/" + dataset["id"] + "/understanding"
    job = client.post(path + "/jobs", json={"scope": "sample"}).json()
    client.post("/api/jobs/" + job["id"] + "/cancel")
    assert run_once(store) is False
    assert client.get(path).json()["profile"] is None
    assert client.get("/api/datasets/" + dataset["id"]).status_code == 200
    new_job = client.post(path + "/jobs", json={"scope": "full"})
    assert new_job.status_code == 202
    assert run_once(store)
    assert client.get(path).json()["profile"]["scope"] == "full"
