"""Snapshot 연결은 사용자 소유 Dataset 범위를 넘을 수 없다."""

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from sos_platform.api import create_app
from sos_platform.worker import run_once

from .support import upload


@pytest.mark.pr
def test_scoped_snapshot_listing_and_connection(workspace, monkeypatch):
    client, store = workspace
    source = upload(client)
    queued = client.post(
        "/api/iceberg/import",
        json={"dataset_id": source["id"], "table_name": "access_scope"},
    )
    assert queued.status_code == 202
    assert run_once(store)
    job = store.get_job(queued.json()["id"])
    assert job["status"] == "Succeeded"
    saved = store.get_dataset(job["result"]["dataset_id"])
    # 같은 테이블에 대한 기존 Dataset 소유권을 명시적으로 부여한다.
    store.register_dataset(
        name=saved["name"],
        kind="iceberg",
        location=saved["location"],
        version=saved["version"],
        owner_actor="alice",
    )
    actors = {
        "alice": {"actor_id": "alice", "kind": "human", "roles": ["researcher"]},
        "mallory": {"actor_id": "mallory", "kind": "human", "roles": ["researcher"]},
    }
    tokens = {name: "secure-" + name + "-" * 40 for name in actors}
    monkeypatch.setenv("SOS_AUTH_MODE", "token")
    monkeypatch.setenv(
        "SOS_AUTH_TOKEN_HASHES",
        json.dumps(
            {
                hashlib.sha256(tokens[name].encode()).hexdigest(): actor
                for name, actor in actors.items()
            }
        ),
    )
    with TestClient(create_app(store.home)) as secure:
        alice = {"Authorization": "Bearer " + tokens["alice"]}
        mallory = {"Authorization": "Bearer " + tokens["mallory"]}
        listing = secure.get("/api/iceberg/access_scope/snapshots", headers=alice)
        assert listing.status_code == 200, listing.text
        assert saved["version"] in {item["snapshot_id"] for item in listing.json()}
        assert secure.get("/api/iceberg/access_scope/snapshots", headers=mallory).status_code == 404
        assert (
            secure.post(
                "/api/datasets/iceberg",
                json={
                    "table_name": "access_scope",
                    "snapshot_id": saved["version"],
                },
                headers=mallory,
            ).status_code
            == 404
        )
        linked = secure.post(
            "/api/datasets/iceberg",
            json={
                "table_name": "access_scope",
                "snapshot_id": saved["version"],
            },
            headers=alice,
        )
        assert linked.status_code == 201, linked.text
        assert linked.json()["version"] == saved["version"]
