"""Dataset 목록은 소유권을 유지하며 안정적으로 페이지 조회된다."""

from .support import upload


def test_paginated_dataset_list_preserves_order_and_legacy_response(workspace):
    client, store = workspace
    base = upload(client)
    for number in range(34):
        store.register_dataset(
            name=f"created-{number:02d}.csv",
            kind="file",
            location=base["location"],
            version=base["version"],
            owner_actor="local-developer",
        )
    seen = []
    cursor = None
    while True:
        options = {"limit": 10}
        if cursor:
            options["cursor"] = cursor
        response = client.get("/api/datasets/pages", params=options)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= 10
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 35
    assert len(set(seen)) == 35
    assert set(seen) == {item["id"] for item in client.get("/api/datasets").json()}
    assert client.get("/api/datasets/pages", params={"cursor": "invalid"}).status_code == 422
    assert client.get("/api/datasets/pages", params={"limit": 101}).status_code == 422
