# 로컬 개발 서비스는 compose.yaml의 app 프로필로 실행한다.
.DEFAULT_GOAL := help
.PHONY: up down restart status logs doctor validate help

COMPOSE := docker compose --profile app

up:
	@$(COMPOSE) up --build -d --wait

down:
	@$(COMPOSE) down

restart:
	@$(COMPOSE) up --build -d --wait --force-recreate api worker serving serving-controller web feast-ui-access feast-ui dagster-code dagster-webserver dagster-daemon

status:
	@$(COMPOSE) ps --all

logs:
	@$(COMPOSE) logs --tail 80 api worker serving serving-controller web feast-ui feast-ui-access dagster-code dagster-webserver dagster-daemon

doctor:
	@$(COMPOSE) config --quiet
	@$(COMPOSE) ps --all
	@$(COMPOSE) exec -T api python -c "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:9602/api/ready', timeout=5).read().decode())"
	@$(COMPOSE) exec -T feast-ui python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8888/health', timeout=5).close()"
	@$(COMPOSE) exec -T serving python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8265/api/serve/applications/', timeout=5).close()"

validate:
	@uv run --locked pytest -q tests

help:
	@printf '%s\n' \
		'SOS Platform · Docker Compose 개발 명령' \
		'  make up         SOS + Ray Serve·Dagster·MLflow·Feast UI 상시 기동·Ready 확인 (SOS_AUTH_TOKEN_HASHES 필요)' \
		'  make down       서비스 종료·데이터 볼륨 보존' \
		'  make restart    애플리케이션 컨테이너 재시작' \
		'  make status     컨테이너 상태 조회' \
		'  make logs       API·Worker·서빙·Web·Dagster 로그' \
		'  make doctor     Compose 설정 및 상태 확인' \
		'  make validate   현재 구성된 제품 검증 실행'
