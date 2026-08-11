.PHONY: install test lint typecheck architecture serve verify docker-up docker-down

install:
	python -m pip install -e ".[dev]"

test:
	pytest

lint:
	ruff check .

typecheck:
	mypy src/eck

architecture:
	python scripts/check_architecture.py

serve:
	eck serve

verify:
	python scripts/verify_release.py

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down
