.PHONY: help install server cam director dev-core dev-sim dev-voice dev-dash demo world replay feeds-probe check types clean
.DEFAULT_GOAL := help

RUN ?=
SCENARIO ?= wildfire_ridge
FLAGS ?=

# El journal que sirve `dev-dash`: el golden de P2 en cuanto exista y, mientras no, el
# falso de `scripts/fake_journal.py` — el v3, que lleva los ids de carretera de hoy y la
# voz en vivo de P3; los v2 y v1 quedan como históricos (el escenario ya no los produce).
# El día que Luis grabe el golden, este target cambia solo y nadie tiene que acordarse.
FAKE_REPLAY := $(firstword $(wildcard fixtures/run_fake_v5.jsonl fixtures/run_fake_v4.jsonl fixtures/run_fake_v3.jsonl fixtures/run_fake_v2.jsonl fixtures/run_fake.jsonl))
REPLAY ?= $(if $(wildcard fixtures/run_golden.jsonl),fixtures/run_golden.jsonl,$(FAKE_REPLAY))

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

demo: ## todo de verdad, 6 minutos · make demo FLAGS="--mock-calls --no-minecraft"
	uv run python scripts/demo.py --scenario $(SCENARIO) $(FLAGS)
# Los dos planes B se ensayan con un comando y no editando el script:
#   nivel 2 (falla la llamada):  make demo FLAGS="--mock-calls"
#   nivel 3 (falla Minecraft):   make demo FLAGS="--no-minecraft"
# Se ensayan los dos, de punta a punta. Un plan B que no se ha corrido es una intención.

levanta: ## comprobación previa + levanta lo que falte · make levanta [PLAYER=<u>]
	uv run python scripts/levanta.py --scenario $(SCENARIO) $(if $(PLAYER),--player $(PLAYER),)
# Antes de cada ensayo. Caza lo que no da error y arruina el run: un gateway huérfano
# en el 8000 (se mide contra código viejo), dos directores peleándose por la cámara,
# la URL de ngrok desincronizada de HappyRobot, el mundo vacío y nadie conectado.
# `--check` solo diagnostica; `--demo` lanza además la demo con llamadas reales.

server: ## levanta Paper 1.21 en local (jar pelado, sin Docker). Déjalo en su terminal
	./infra/server/start.sh

cam: ## cámara del pitch · 1-6 EN ESTA TERMINAL, Minecraft en la 2ª pantalla
	@test -n "$(PLAYER)" || { echo "falta PLAYER=<tu usuario de Minecraft>"; exit 1; }
	uv run python -m sim.camera --live --who $(PLAYER) --scenario scenarios/$(SCENARIO).yaml

director: ## cámara automática + narración en vivo · make director PLAYER=<usuario>
	@test -n "$(PLAYER)" || { echo "falta PLAYER=<tu usuario de Minecraft>"; exit 1; }
	uv run python -m sim.director --player $(PLAYER) --scenario scenarios/$(SCENARIO).yaml

narra: ## SOLO narración, sin tocar la cámara · para correr A LA VEZ que `make cam`
	@test -n "$(PLAYER)" || { echo "falta PLAYER=<tu usuario de Minecraft>"; exit 1; }
	uv run python -m sim.director --narrar-solo --player $(PLAYER) --scenario scenarios/$(SCENARIO).yaml
# `cam` y `director` mandan los dos /tp al MISMO jugador y no se coordinan: si corren
# a la vez, un evento urgente del director te roba el plano en medio segundo. Elige
# uno de los dos — o `cam` + `narra`, que es la pareja que sí convive.

world: ## regenera el mundo por RCON (idempotente: /kill @e[tag=vela] y otra vez)
	uv run python -m sim.worldgen --scenario scenarios/$(SCENARIO).yaml

replay: ## reproduce un journal a velocidad real · make replay RUN=<id>
	@test -n "$(RUN)" || { echo "falta RUN=<id>"; exit 1; }
	uv run python -m journal.replay runs/$(RUN).jsonl

feeds-probe: ## prueba las fuentes reales del ancla sin publicar · make feeds-probe SCENARIO=wildfire_ridge
	uv run python -m gateway.feeds --probe $(SCENARIO)

# --- Contratos verificados, no acordados. Verde en cada merge a main. ---

check: ## mypy sobre contracts + replay del golden + catálogo contra prompts
	uv run mypy packages/contracts/src
	uv run pytest -q

types: ## regenera apps/dashboard/src/types.ts desde contracts
	uv run python scripts/gen_ts_types.py

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
