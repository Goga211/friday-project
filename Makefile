.PHONY: help venv install install-voice install-hud piper lint fmt typecheck test certs broker broker-down core desktop cli voice hud home

VENV := .venv
PY := $(VENV)/bin/python

help:
	@echo "Цели: install lint fmt typecheck test broker broker-down core desktop"

venv:
	python3 -m venv $(VENV)

install: venv
	$(PY) -m pip install -U pip
	$(PY) -m pip install -e ".[dev]"

install-hud: venv
	$(PY) -m pip install -e ".[dev,hud]"

install-voice: venv
	$(PY) -m pip install -e ".[dev,voice]"
	# openWakeWord отдельно с --no-deps: его tflite-runtime не собирается под py312, а мы на ONNX
	$(PY) -m pip install "openwakeword>=0.6" --no-deps

piper:
	bash scripts/install-piper.sh

lint:
	$(VENV)/bin/ruff check src tests

fmt:
	$(VENV)/bin/black src tests
	$(VENV)/bin/ruff check --fix src tests

typecheck:
	$(VENV)/bin/mypy src

test:
	$(VENV)/bin/pytest

certs:
	@test -f infra/certs/ca.crt || bash infra/scripts/gen-certs.sh

broker: certs
	cd infra && docker compose up -d

broker-down:
	cd infra && docker compose down

core:
	$(PY) -m friday.core.app

desktop:
	$(PY) -m friday.agents.desktop.app

cli:
	$(PY) -m friday.cli.app

voice:
	$(PY) -m friday.agents.voice.app

hud:
	$(PY) -m friday.hud.app

home:
	$(PY) -m friday.agents.home.app
