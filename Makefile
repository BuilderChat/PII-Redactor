PYTHON ?= python3
VENV ?= .venv

.PHONY: venv install run test lint

venv:
	$(PYTHON) -m venv $(VENV)
	. $(VENV)/bin/activate && pip install --upgrade pip

install:
	. $(VENV)/bin/activate && pip install -r requirements-dev.txt

run:
	. $(VENV)/bin/activate && uvicorn src.server:app --host 0.0.0.0 --port 8000 --reload

test:
	. $(VENV)/bin/activate && pytest -q

lint:
	. $(VENV)/bin/activate && python -m compileall src tests
