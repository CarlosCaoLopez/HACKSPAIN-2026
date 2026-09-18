.PHONY: help install dev-core dev-sim dev-voice dev-dash demo world replay check types clean
.DEFAULT_GOAL := help

RUN ?=
SCENARIO ?= wildfire_ridge

# El journal que sirve `dev-dash`: el golden de P2 en cuanto exista y, mientras no,
# el falso de `scripts/fake_journal.py`. El día que Luis grabe el golden, este
# target cambia solo y nadie tiene que acordarse.
REPLAY ?= $(if $(wildcard fixtures/run_golden.jsonl),fixtures/run_golden.jsonl,fixtures/run_fake.jsonl)

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
	@echo "replay: $(REPLAY)"
	VELA_MODE=replay VELA_REPLAY_FILE=$(REPLAY) \
		uv run uvicorn gateway.main:app --port 8000 & \
	GW=$$!; trap 'kill $$GW 2>/dev/null' EXIT INT TERM; \
	cd apps/dashboard && pnpm dev
# El `trap` es lo que hace que Ctrl-C se lleve los dos procesos: sin él, `pnpm dev`
# muere y uvicorn se queda con el puerto 8000 cogido, y el siguiente `make dev-dash`
# arranca contra un gateway viejo sin que se note.

# --- La demo ---

demo: ## todo de verdad, 6 minutos
	uv run python scripts/demo.py --scenario $(SCENARIO)

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
