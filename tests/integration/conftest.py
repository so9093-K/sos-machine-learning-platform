"""테스트 별 독립 DB·S3 버킷을 제공하는 Fixture."""

from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from sos_platform.api import create_app
from sos_platform.config import Settings
from sos_platform.object_store import ObjectStore
from sos_platform.store import Store


@pytest.fixture(scope="session")
def migrated_template_database():
    """세션당 한 번 마이그레이션하고 각 테스트의 실제 DB 격리는 유지한다."""
    settings = Settings.from_env()
    base = make_url(settings.database_url)
    endpoint = urlsplit(settings.s3_endpoint)
    if base.host not in ("localhost", "127.0.0.1", "::1") or endpoint.hostname not in (
        "localhost", "127.0.0.1", "::1"
    ):
        pytest.fail("Integration tests require loopback PostgreSQL and S3 endpoints")
    name = "sos_test_template_" + uuid4().hex[:14]
    with ExitStack() as resources:
        admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
        resources.callback(admin.dispose)
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))

        def drop_template():
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))

        resources.callback(drop_template)
        template_url = base.set(database=name).render_as_string(hide_password=False)
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", template_url)
        command.upgrade(config, "head")
        yield name


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch, migrated_template_database):
    """독립 DB·버킷을 생성하고 준비 단계의 예외에도 본인이 만든 리소스를 회수한다."""
    # 각 테스트 워크스페이스의 로컬 신원을 명시하며, 테스트 내부의 Token 전환은 허용한다.
    monkeypatch.setenv("SOS_AUTH_MODE", "development")
    settings = Settings.from_env()
    base = make_url(settings.database_url)
    s3_endpoint = urlsplit(settings.s3_endpoint)
    # 로컬 호스트 여부는 S3/DB 초기화보다 먼저 검증해야 한다.
    if base.host not in ("localhost", "127.0.0.1", "::1") or s3_endpoint.hostname not in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        pytest.fail("Integration tests require loopback PostgreSQL and S3 endpoints")
    name = "sos_test_" + uuid4().hex[:14]
    bucket = "sos-pytest-" + uuid4().hex[:20]
    monkeypatch.setenv("SOS_S3_BUCKET", bucket)

    # fixture가 yield에 도달하지 못하더라도 이미 생성한 자원은 역순으로 정리한다.
    with ExitStack() as resources:
        admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
        resources.callback(admin.dispose)
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{migrated_template_database}"'))

        def drop_database():
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))

        resources.callback(drop_database)
        test_url = base.set(database=name).render_as_string(hide_password=False)
        monkeypatch.setenv("SOS_DATABASE_URL", test_url)
        storage = ObjectStore()
        try:
            storage.client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status != 404:
                raise
        else:
            pytest.fail("Refusing to reuse an existing S3 test bucket")
        storage.client.create_bucket(Bucket=bucket)

        def remove_test_bucket():
            # 정상 생성한 테스트 버킷에서만 객체를 제거하고 다른 버킷은 접근하지 않는다.
            paginator = storage.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if objects:
                    storage.client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
            storage.client.delete_bucket(Bucket=bucket)

        resources.callback(remove_test_bucket)
        store = Store(tmp_path, database_url=test_url)
        resources.callback(store.engine.dispose)
        with TestClient(create_app(tmp_path)) as client:
            yield client, store
