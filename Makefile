.DEFAULT_GOAL := help
.PHONY: up down restart status logs doctor check help

up down restart status logs doctor check help:
	@python3 scripts/dev.py $@
