# 로컬 개발 서비스는 compose.yaml의 app 프로필로 실행한다.
.DEFAULT_GOAL := help
.PHONY: up down restart status logs doctor doctor-dagster validate help

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

# 선택적 진단: 전체 서비스 준비 확인과 구분된 실제 Dagster Run 조회 계약 검사.
doctor-dagster:
	@$(COMPOSE) exec -T api python -c "from sos_platform.dagster_client import DagsterClient; result = DagsterClient().find_by_submission('00000000-0000-4000-8000-000000000000'); print('Dagster Run 검색 계약: 정상, 결과:', result)"

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
		'  make doctor-dagster  Dagster GraphQL Run 검색 계약(읽기 전용) 확인' \
		'  make validate   현재 구성된 제품 검증 실행'
