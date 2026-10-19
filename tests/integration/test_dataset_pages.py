"""Dataset 목록은 소유권을 유지하며 안정적으로 페이지 조회된다."""

from .support import upload


def test_dataset_list_preserves_order_and_page_contract(workspace):
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
        response = client.get("/api/datasets", params=options)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= 10
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 35
    assert len(set(seen)) == 35
    assert set(seen) == {item["id"] for item in store.dataset_page(limit=101)}
    assert client.get("/api/datasets", params={"limit": 101}).status_code == 422


def test_invalid_dataset_cursor_returns_specific_safe_validation_error(workspace):
    client, _store = workspace
    result = client.get("/api/datasets", params={"cursor": "not-a-valid-cursor"})
    assert result.status_code == 422
    assert result.json()["code"] == "SOS-API-VALIDATION"
    assert result.json()["detail"] == "Invalid Dataset cursor"
