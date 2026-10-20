# 공개 서비스와 격리 브라우저 검증은 compose.yaml을 공유한다.
.DEFAULT_GOAL := help
.PHONY: up down restart status logs doctor help

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

help:
	@printf '%s\n' \
		'SOS Platform · Docker Compose 개발 명령' \
		'  make up         전체 서비스 기동·Ready 확인 (SOS_AUTH_TOKEN_HASHES 필요)' \
		'  make down       서비스 종료·데이터 볼륨 보존' \
		'  make restart    애플리케이션 컨테이너 재시작' \
		'  make status     컨테이너 상태 조회' \
		'  make logs       API·Worker·Web 로그' \
		'  make doctor     Compose 설정 및 상태 확인'
