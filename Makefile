.PHONY: install test test-all integration lint fmt check cov build docs

install:
	.venv/bin/pip install -e ".[dev,docs]"

test:
	.venv/bin/python -m pytest -m "not integration"

test-all:
	.venv/bin/python -m pytest

integration:
	.venv/bin/python -m pytest -m integration

lint:
	.venv/bin/ruff check src tests tools

fmt:
	.venv/bin/ruff format src tests tools

check: lint test

cov:
	.venv/bin/python -m pytest --cov=scriptsuite --cov-report=term-missing

build:
	.venv/bin/python -m build
	.venv/bin/twine check dist/*

docs:
	.venv/bin/python tools/screenshot.py
	.venv/bin/python tools/build_docs.py
