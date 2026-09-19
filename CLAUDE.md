# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Reglas personales, si existen en tu copia (gitignored, cada uno las suyas): @claude-nacho.md

## Estado del repo

`vela`: agente de crisis para HackSpain 2026 (reto HappyRobot). Decide con un LLM + solver
determinista, usa Minecraft por RCON como simulador, recibe y emite llamadas de voz y lo enseña en
un dashboard. Hay código en `packages/`, `apps/`, `scenarios/`, `scripts/`, `infra/` y `tests/`;
`fixtures/run_golden.jsonl` aún no existe (mientras, `fixtures/run_fake_v4.jsonl`).

**La spec vigente es `docs/backbon_corrected.md` (rev. 2).** `docs/backbone.md` es la versión
anterior y solo sirve de histórico: donde se contradigan, manda la corrected.

## Stack cerrado

| Capa | Elección | Nota |
| --- | --- | --- |
| Backend | Python **3.12 exacto** | `fenic` declara `<3.13` |
| Contratos | Pydantic v2 | Sin dependencias de terceros en `contracts` |
| Servidor | FastAPI + uvicorn, **un solo proceso** | API, webhooks y WS juntos |
| Bus | `asyncio` in-process + journal JSONL | Sin infra externa |
| Percepción en llamada | **TypeSafe `jev-1.13`** (`typesafe-sdk`) | Elige entre ids del escenario (`Choice`/`Score`/`Noul`), cada 5 s en llamada y al colgar. Sin clave o con `VELA_NO_JEV=true`, cae a `fenic` con `Literal` |
| Batch entre runs | Typedef `fenic` | Solo aprendizaje (`core/memory.py`) y plan B; ya no está en la ruta caliente |
| Asignación | `scipy.optimize.linear_sum_assignment` | Determinista |
| Mundo | Paper 1.21 + RCON (`mcrcon`) | Solo `/tp`, `/fill`, `/setblock`. Sin bots |
| Voz | HappyRobot en las dos direcciones + Humalike encima (`foresee`, `analyze`, `personas`) | Humalike no toca la telefonía. Todo REST |
| Dashboard | Vite + React + TS + Tailwind | **Único sitio con TypeScript** |
| Deps | `uv` + `pnpm` | |

## Invariantes que no se rompen

1. **El LLM nunca toca Minecraft.** Minecraft renderiza un estado que vive en nuestro modelo.
2. **El LLM nunca asigna recursos.** Produce una `Policy` (pesos + restricciones duras); el solver
   produce el `Plan`. `Policy` no lleva nunca un campo `assignments`.
3. **Todo evento se escribe al journal antes de repartirse** (`publish` escribe y luego reparte).
4. **Todos importan de `contracts`; nadie importa del paquete de otro.** Se habla por el bus.
   Excepción única: `apps/gateway`.
5. **`WorldState` es inmutable.** `apply(state, ev)` devuelve uno nuevo.
6. **El journal es append-only.** Si algo cambia, se emite otro evento.
7. **Una sola llamada al modelo de razonamiento por replan**, y solo con bandera: divergencia > 0,25,
   violación de restricción dura o hecho `severity: critical`.
8. **Un hecho asumido nunca se disfraza de observado.** Todo `Fact` lleva `kind: observed | inferred |
   assumed_default`. Solo `observed` puede fundar una restricción dura o reabrir una arista cortada;
   un `assumed_default` entra a `PlanContext.assumptions` con peso alto y se pinta gris cursiva.

## Arquitectura

Cinco paquetes que solo se hablan por eventos tipados: `contracts` (de nadie), `sim` (mundo +
injects), `core` (belief · planner · solver · verifiers · divergence), `voice` (telefonía + percepción),
`journal` (writer · replay · score).

Tick: `sim` publica `world.*` → `core.belief` reconstruye el `WorldState` → `divergence` lo compara
con `PlanContext.assumptions` → con bandera, `planner` emite `Policy`, `solver` emite `Plan`,
`verifiers` lo validan (dos vueltas, luego plan del solver con pesos neutros) → `core` publica
`action.*` → `sim` y `voice` ejecutan.

Llamada: HappyRobot (SSE + tool `report_fact`, que es **solo disparador**) → `voice.perception` hace
un tick de Jev → `budget` decide asertar, repreguntar (una a la vez, por *signal* `kind: followup`) o
rellenar con el LLM (`gapfill`) al agotarse el presupuesto por gravedad → `world.fact.asserted` +
`call.completeness`.

## Comandos

| Comando | Qué hace |
| --- | --- |
| `make check` | mypy sobre `contracts` + `pytest` (replay, catálogo, voz, belief…). Verde en cada merge |
| `make types` | regenera `apps/dashboard/src/types.ts` (**se genera, nunca a mano**) |
| `make dev-core` / `dev-sim` / `dev-voice` / `dev-dash` | cada pieza sola, con el resto simulado |
| `make demo` | todo de verdad · `FLAGS="--mock-calls"` / `"--no-minecraft"` son los planes B |
| `make server` · `make world` · `make cam` | Paper local · regenera el mundo por RCON · cámara del pitch |
| `make replay RUN=<id>` | reproduce un journal |
| `uv run python -m voice.jev --gate` | puerta de Jev: 10 transcripciones en español contra la clave real |

Los merges a `main` van directos, sin PR, con `make check` verde.

## Propiedad de ficheros

Si necesitas algo de la carpeta de otro, pídelo por evento, no por import.

| Ruta | Dueño |
| --- | --- |
| `packages/contracts/**` | Nadie: los cuatro, solo en la ventana de contrato |
| `packages/core/**`, `packages/journal/**`, `voice/happyrobot.py` | Carlos (P1) |
| `packages/voice/**` (percepción, humalike, webhooks), `core/belief.py`, `core/ingest.py` | Hugo (P3) |
| `packages/sim/**`, `infra/**`, `scenarios/*.yaml` | Luis (P2) |
| `apps/gateway/**`, `apps/dashboard/**`, `scripts/demo.py` | Nacho (P4) |
| `fixtures/**` | Quien lo genera; solo se añade, nunca se modifica |

Contrato: **añadir un campo opcional con default es libre** (commit + aviso). Renombrar, cambiar un
tipo o hacer obligatorio un campo necesita a los cuatro. Borrar está prohibido.

## Convenciones

- **Antes de tocar nada: commit de lo que haya (aunque sea `wip:`) y `git pull --rebase origin <rama>`.**
  Nunca `stash` como rutina.
- **Ids con prefijo**: `unit_truck1`, `poi_pueblo_a`, `wp_sur_03`, `cell_14_22`, `task_evac_a`; las
  aristas son `road:wp_a-wp_b` (el id es su dirección).
- **Coordenadas del mundo Minecraft** (x, z). `t_sim` (segundos) es el tiempo del dominio; `t_wall` solo depura.
- **`sim.execute` acepta cuatro verbos**: `goto`, `set_marker`, `announce`, `rescue`. Otro emite
  `action.failed` con `error="unknown_verb"`.
- **Un payload que no valida nunca tumba el proceso**: lanza en desarrollo, `event.malformed` en la demo.
- **Secretos en `.env`** (raíz, gitignored; `.env.example` commiteado), cargado por `contracts/settings.py`.
- **Degradar a lo explícito**: un servicio caído (Jev, Humalike, HappyRobot) se anota y la demo sigue;
  nunca un `except: pass`.

## Dónde está el detalle

- `docs/backbon_corrected.md` — stack, guion de la demo, motor de decisión, percepción con Jev,
  simulación, telefonía, reparto, riesgos y planes B.
- `docs/interfaces.md` — catálogo de eventos, modelos de `contracts`, firmas por paquete, endpoints y
  WebSocket, `human.override`, variables de entorno.
