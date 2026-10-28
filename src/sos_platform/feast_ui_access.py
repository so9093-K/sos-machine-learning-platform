"""로컬 Feast UI가 기존 Registry를 읽기 전용으로 탐색할 수 있게 준비한다.

실제 Feature 정의의 등록·변경·폐기는 Feast Worker와 SOS Job만 수행한다.
UI에는 전체 권한의 Registry 연결 문자열을 전달하지 않는다.
"""

import os

import psycopg2
from sqlalchemy.engine import make_url


def main() -> None:
    """Registry 스키마 준비 및 기존·향후 테이블의 UI 읽기 권한을 적용한다."""
    from sos_platform.feast_features import _feature_store

    # SQL Registry는 최초 생성 시 메타데이터 행을 프로젝트 목록보다 먼저 만들 수 있다.
    # UI는 그 간극을 읽기 전용 계정으로 복구할 수 없으므로 관리 Job에서 완결한다.
    from feast.project import Project

    store = _feature_store()
    registry = store.registry
    if not any(project.name == store.project for project in registry.list_projects(allow_cache=False)):
        registry.apply_project(Project(name=store.project))

    registry_url = os.environ["SOS_FEAST_REGISTRY_URL"]
    password = os.environ["SOS_FEAST_UI_DB_PASSWORD"]
    if not password:
        raise ValueError("Feast UI database password must not be empty")
    url = make_url(registry_url)
    if url.drivername != "postgresql+psycopg2":
        raise ValueError("Feast UI supports the existing PostgreSQL/psycopg2 registry")
    params = dict(
        dbname=url.database,
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port or 5432,
        connect_timeout=10,
    )
    with psycopg2.connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'feast_ui'")
            if cursor.fetchone() is None:
                cursor.execute("CREATE ROLE feast_ui LOGIN PASSWORD %s", (password,))
            else:
                cursor.execute("ALTER ROLE feast_ui LOGIN PASSWORD %s", (password,))
            cursor.execute(
                "ALTER ROLE feast_ui NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
            )
            cursor.execute("ALTER ROLE feast_ui SET default_transaction_read_only = on")
            cursor.execute("GRANT CONNECT ON DATABASE sos_feast TO feast_ui")
            cursor.execute("GRANT USAGE ON SCHEMA public TO feast_ui")
            cursor.execute("REVOKE CREATE ON SCHEMA public FROM feast_ui")
            cursor.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO feast_ui")
            cursor.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE feast IN SCHEMA public "
                "GRANT SELECT ON TABLES TO feast_ui"
            )
    print("Feast UI read-only Registry access configured")


if __name__ == "__main__":
    main()
