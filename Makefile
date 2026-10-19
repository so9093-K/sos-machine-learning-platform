# 공개 서비스와 격리 브라우저 검증은 compose.yaml을 공유한다.
.DEFAULT_GOAL := help
.PHONY: up down restart status logs doctor check check-fast help

COMPOSE := docker compose --profile app

up:
	@$(COMPOSE) up --build -d --wait

down:
	@$(COMPOSE) down

restart:
	@$(COMPOSE) up --build -d --wait --force-recreate api worker web

status:
	@$(COMPOSE) ps --all

logs:
	@$(COMPOSE) logs --tail 80 api worker web

doctor:
	@$(COMPOSE) config --quiet
	@$(COMPOSE) ps --all
	@$(COMPOSE) exec -T api python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:9602/api/ready', timeout=5).close()"

check-fast:
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	uv run --locked pytest -q tests/test_*.py
	cd web && npm run build && npm test

check: check-fast
	@docker compose -f compose.yaml -p sos-platform ps --status running
	uv run --locked pytest -q tests/integration
	cd web && npm run generate:types
	git diff --exit-code -- web/src/api.generated.ts
	uv run --locked python scripts/verify_wheel.py
	uv run --locked alembic check
	uv run --locked python scripts/isolated_compose.py --spec all

help:
	@printf '%s\n' \
		'SOS Platform · Docker Compose 개발 명령' \
		'  make up         전체 서비스 기동·Ready 확인 (SOS_AUTH_TOKEN_HASHES 필요)' \
		'  make down       서비스 종료·데이터 볼륨 보존' \
		'  make restart    애플리케이션 컨테이너 재시작' \
		'  make status     컨테이너 상태 조회' \
		'  make logs       API·Worker·Web 로그' \
		'  make doctor     Compose 설정 및 상태 확인' \
		'  make check      전체 통합 검증' \
		'  make check-fast 인프라 없이 품질·단위·UI 검증'
