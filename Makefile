.DEFAULT_GOAL := help
.PHONY: up down restart status logs doctor check check-fast help

up down restart status logs doctor check check-fast help:
	@python3 scripts/dev.py $@
