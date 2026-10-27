.PHONY: up down logs ps dev test lint samples verify clean

up:
	cd deploy && docker compose up -d --build

down:
	cd deploy && docker compose down

logs:
	cd deploy && docker compose logs -f app

ps:
	cd deploy && docker compose ps

dev:
	cd backend && PYTHONPATH=src python -m uvicorn citeseek.main:app --reload --port 8000

web:
	cd frontend && npm run build

web-dev:
	cd frontend && npm run dev

test:
	cd backend && python -m pytest -q

lint:
	cd backend && python -m ruff check . && python -m ruff format --check .

samples:
	python scripts/make_samples.py

verify:
	python scripts/verify_library.py

verify-search:
	python scripts/verify_search.py

verify-ask:
	python scripts/verify_ask.py

verify-all:
	python scripts/verify_library.py
	python scripts/verify_search.py
	python scripts/verify_ask.py

clean:
	cd deploy && docker compose down -v
