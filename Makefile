.PHONY: help install server dev-core dev-sim dev-voice dev-dash demo world replay check types clean
.DEFAULT_GOAL := help

RUN ?=
SCENARIO ?= wildfire_ridge

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

install: ## uv sync + pnpm install del dashboard
	uv sync
	cd apps/dashboard && pnpm install

# --- Trabajar sin los demás. Nadie espera a nadie en ningún momento. ---

dev-core: ## core + bus, con sim y voice leídos de fixtures/run_golden.jsonl
	uv run python -m core.loop --replay fixtures/run_golden.jsonl

dev-sim: ## sim + bus + RCON, con un core tonto que manda goto en bucle
	uv run python -m sim.runner --scenario scenarios/$(SCENARIO).yaml --dummy-core

dev-voice: ## gateway + voice + túnel, con un core que pide una llamada cada 60 s
	uv run uvicorn gateway.main:app --reload --port 8000

dev-dash: ## gateway + WS en modo replay
	VELA_MODE=replay uv run uvicorn gateway.main:app --port 8000 & \
	cd apps/dashboard && pnpm dev

# --- La demo ---

demo: ## todo de verdad, 6 minutos
	uv run python scripts/demo.py --scenario $(SCENARIO)

server: ## levanta Paper 1.21 en local (jar pelado, sin Docker). Déjalo en su terminal
	./infra/server/start.sh

world: ## regenera el mundo por RCON (idempotente: /kill @e[tag=vela] y otra vez)
	uv run python -m sim.worldgen --scenario scenarios/$(SCENARIO).yaml

replay: ## reproduce un journal a velocidad real · make replay RUN=<id>
	@test -n "$(RUN)" || { echo "falta RUN=<id>"; exit 1; }
	uv run python -m journal.replay runs/$(RUN).jsonl

# --- Contratos verificados, no acordados. Verde en cada merge a main. ---

check: ## mypy sobre contracts + replay del golden + catálogo contra prompts
	uv run mypy packages/contracts/src
	uv run pytest -q

types: ## regenera apps/dashboard/src/types.ts desde contracts
	uv run python scripts/gen_ts_types.py

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
