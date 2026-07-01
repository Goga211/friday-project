.PHONY: help venv install install-voice piper lint fmt typecheck test broker broker-down core desktop cli voice

VENV := .venv
PY := $(VENV)/bin/python

help:
	@echo "Цели: install lint fmt typecheck test broker broker-down core desktop"

venv:
	python3 -m venv $(VENV)

install: venv
	$(PY) -m pip install -U pip
	$(PY) -m pip install -e ".[dev]"

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

broker:
	cd infra && docker compose up -d

broker-down:
	cd infra && docker compose down

core:
	$(PY) -m christopher.core.app

desktop:
	$(PY) -m christopher.agents.desktop.app

cli:
	$(PY) -m christopher.cli.app

voice:
	$(PY) -m christopher.agents.voice.app
