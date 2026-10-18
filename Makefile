# 공개 개발 진입점은 Docker Compose 한 개이며, Host 실행은 격리 테스트 도구에서만 사용한다.
.DEFAULT_GOAL := help
.PHONY: up down restart status logs doctor check check-fast help

COMPOSE := docker compose --profile app

up:
	@$(COMPOSE) up --build -d --wait

down:
	@$(COMPOSE) down

restart:
	@$(COMPOSE) restart api worker web

status:
	@$(COMPOSE) ps --all

logs:
	@$(COMPOSE) logs --tail 80 api worker web

doctor:
	@$(COMPOSE) config --quiet
	@$(COMPOSE) ps --all

check-fast:
	@set -eu; for suite in ruff python-unit frontend; do \
		uv run --locked python scripts/verify.py run "$$suite"; \
	done

check:
	@$(COMPOSE) ps --status running
	@set -eu; for suite in ruff python-unit python-integration frontend openapi-types wheel alembic workspace-e2e token-rbac container-runtime; do \
		uv run --locked python scripts/verify.py run "$$suite"; \
	done

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
