.PHONY: install test lint typecheck serve verify docker-up docker-down

install:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .

typecheck:
	mypy src/eck

serve:
	eck serve

verify:
	python scripts/verify_release.py

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

