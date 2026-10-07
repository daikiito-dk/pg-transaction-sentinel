.PHONY: setup up down data score app demo test lint format all

setup:
	uv sync
	@test -f .env || cp .env.example .env

up:
	docker compose up -d --wait

down:
	docker compose down

data:
	uv run python generate_data.py

score:
	uv run python ml_engine.py

app:
	uv run streamlit run app.py

demo:
	PUBLIC_DEMO=true uv run streamlit run app.py --server.address 127.0.0.1 --server.port 8502

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

all: setup up data score app
