# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Estado del repo

Solo hay diseño: `docs/backbone.md` y `docs/interfaces.md`. **Cero código.** El árbol de
`packages/`, `apps/`, `scenarios/`, `fixtures/`, `infra/` está especificado en
`docs/backbone.md` (sección *Estructura de carpetas*) pero aún no existe. Al implementar, crear
esa estructura tal cual; no inventar otra.

El proyecto es `vela`: un agente de crisis para HackSpain 2026 (reto HappyRobot). Decide con un
LLM + solver determinista, usa Minecraft por RCON como simulador, recibe y emite llamadas de voz,
y lo enseña en un dashboard.

## Stack cerrado

| Capa | Elección | Restricción real |
| --- | --- | --- |
| Backend | Python **3.12 exacto** | `fenic` declara `Requires-Python >=3.10,<3.13`. No 3.13 |
| Contratos | Pydantic v2 | Los mismos modelos son el esquema de `semantic.extract` |
| Servidor | FastAPI + uvicorn, **un solo proceso** | API, webhooks y WS del dashboard juntos |
| Bus | `asyncio` in-process + journal JSONL | Sin infra externa |
| Ingesta | Typedef `fenic` | `semantic.extract` / `classify` / `join` |
| Asignación | `scipy.optimize.linear_sum_assignment` | Determinista y explicable |
| Mundo | Paper 1.21 + RCON (`mcrcon`) | Solo `/tp`, `/fill`, `/setblock`. Sin bots ni pathfinding |
| Voz | HappyRobot en las dos direcciones (tool `report_fact` en llamada, *signals* de vuelta) + Humalike encima (`foresee`, `analyze`, `personas`) | Humalike no toca la telefonía. Todo REST, no hay SDK de Python |
| Dashboard | Vite + React + TS + Tailwind | **Único sitio donde hay TypeScript** |
| Deps | `uv` (Python) + `pnpm` (dashboard) | |

Nada de Node en el servidor: mineflayer se descartó al descartar los agentes LLM dentro de Minecraft.

## Invariantes que no se rompen

1. **El LLM nunca toca Minecraft.** Minecraft renderiza un estado que vive en nuestro modelo.
2. **El LLM nunca asigna recursos.** Produce una `Policy` (pesos + restricciones duras); el solver
   produce el `Plan`. **`Policy` no lleva nunca un campo `assignments`**, por tentador que sea.
3. **Todo evento se escribe al journal antes de repartirse.** `publish` escribe y luego reparte,
   en ese orden. Sin journal no hay replay.
4. **Todos importan de `contracts`; nadie importa del paquete de otro.** La comunicación es el bus
   (`publish` / `subscribe`). `from sim import ...` dentro de `core` rompe la arquitectura.
   Excepción única: `apps/gateway` importa de todos, es su trabajo.
5. **`WorldState` es inmutable.** `apply(state, ev) -> WorldState` devuelve uno nuevo; nadie muta en sitio.
6. **El journal es append-only.** Nada se borra ni se edita: si algo cambia, se emite otro evento.
7. **Una sola llamada al modelo de razonamiento por replan**, y solo si se levanta la bandera:
   divergencia > 0,25, violación de restricción dura, o hecho con `severity: critical`. Si no, no
   se llama al modelo.

## Arquitectura

Cinco paquetes que solo se hablan por eventos tipados, corriendo en un único proceso FastAPI:
`contracts` (de nadie), `sim` (mundo + injects), `core` (belief · planner · solver · verifiers ·
divergence), `voice` (telefonía), `journal` (writer · replay · score).

Ciclo de un tick: `sim` avanza 1 s simulado y publica `world.*` → `core.belief` reconstruye el
`WorldState` → `core.divergence` lo compara contra `PlanContext.assumptions` → si hay bandera de
replan, `planner` emite `Policy`, `solver` emite `Plan`, `verifiers` lo validan (dos vueltas máximo,
luego se cae al plan del solver con pesos neutros) → `core` publica `action.*` → `sim` y `voice`
ejecutan y confirman.

Los cinco niveles y su determinismo: estado (Pydantic, **sí**) · ingesta (`fenic`, no, pero acotada
por esquema) · política (LLM, **no**) · asignación (`linear_sum_assignment`, **sí**) ·
verificación (código puro, **sí**).

## Comandos

Documentados en `docs/backbone.md` y `docs/interfaces.md`; **el Makefile todavía no existe**.

| Comando | Qué hace |
| --- | --- |
| `make dev-core` | core + bus, con sim y voice leídos de `fixtures/run_golden.jsonl` |
| `make dev-sim` | sim + bus + RCON, con un core tonto que manda `goto` en bucle |
| `make dev-voice` | gateway + voice + túnel, con un core que pide una llamada cada 60 s |
| `make dev-dash` | gateway + WS en modo replay |
| `make demo` | todo de verdad · `make world` regenera el mundo por RCON (idempotente) |
| `make replay RUN=<id>` | reproduce ese journal a velocidad real |
| `make check` | mypy sobre `contracts` + replay de `run_golden.jsonl` por `apply` + test de que todo peso y restricción de `core/prompts/` existe en el catálogo |
| `make types` | regenera `apps/dashboard/src/types.ts` desde `contracts` con `scripts/gen_ts_types.py` |

`types.ts` **se genera, nunca se escribe a mano**. Los merges a `main` van directos, sin PR, pero
con `make check` verde.

## Propiedad de ficheros

Cada fichero tiene un dueño. Si necesitas algo de la carpeta de otro, pídelo por evento, no por import.

| Ruta | Dueño |
| --- | --- |
| `packages/contracts/**` | Nadie: los cuatro, solo en la ventana de contrato |
| `packages/core/**`, `packages/journal/**` | P1 |
| `packages/sim/**`, `infra/**`, `scenarios/*.yaml` | P2 |
| `packages/voice/**` | P3 |
| `apps/gateway/**`, `apps/dashboard/**`, `scripts/demo.py` | P4 |
| `fixtures/**` | Quien lo genera; solo se añade, nunca se modifica |

Cambios de contrato: **añadir un campo opcional con valor por defecto es libre** (commit + aviso).
Renombrar, cambiar un tipo o hacer obligatorio un campo necesita a los cuatro. Borrar está prohibido.

## Convenciones

- **Ids con prefijo**: `unit_truck1`, `poi_pueblo_a`, `wp_sur_03`, `cell_14_22`, `task_evac_a`.
- **Coordenadas siempre del mundo Minecraft** (x, z, con y implícita). El core nunca piensa en píxeles.
- **`t_sim`, segundos flotantes, es el tiempo del dominio.** `t_wall` solo depura y mide latencia real.
- **`sim.execute` acepta exactamente cuatro verbos**: `goto`, `set_marker`, `announce`, `rescue`.
  Cualquier otro emite `action.failed` con `error="unknown_verb"`. No se añaden verbos sin avisar.
- **Un payload que no valida nunca tumba el proceso**: lanza en desarrollo, se registra como
  `event.malformed` en la demo.
- **Secretos en un `.env` en la raíz** (`.env.example` commiteado), cargados por
  `contracts/settings.py` con `pydantic-settings`; cada paquete lee solo sus variables.

## Dónde está el detalle

- `docs/backbone.md` — decisiones de stack, guion de la demo minuto a minuto, motor de decisión
  (LLM-Modulo, verificadores, divergencia, aprendizaje entre runs), capa de simulación, telefonía,
  estructura de carpetas, reparto y cronograma, riesgos y planes B.
- `docs/interfaces.md` — catálogo completo de eventos, modelos de `contracts` (`world.py`, `plan.py`,
  `calls.py`), firmas públicas de cada paquete, endpoints HTTP y protocolo del WebSocket, contrato
  de `human.override`, variables de entorno.
