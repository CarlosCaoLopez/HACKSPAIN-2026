# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Reglas personales, si existen en tu copia (gitignored, cada uno las suyas): @claude-nacho.md

## Estado del repo

`vela`: agente de crisis para HackSpain 2026 (reto HappyRobot). Decide con un LLM + solver
determinista, simula con Minecraft por RCON, recibe y emite llamadas y lo enseña en un dashboard.
Hay código en `packages/`, `apps/`, `scenarios/`, `scripts/`, `infra/`, `tests/`;
`fixtures/run_golden.jsonl` aún no existe (mientras, `run_fake_v4.jsonl`).

**Spec vigente: `docs/backbon_corrected.md` (rev. 2).** `docs/backbone.md` es histórico: si se
contradicen, manda la corrected. Contratos, eventos y endpoints: `docs/interfaces.md`.

## Stack cerrado

- **Python 3.12 exacto** (`fenic` declara `<3.13`), Pydantic v2, FastAPI + uvicorn en **un solo proceso**.
- **Bus**: `asyncio` in-process + journal JSONL. Sin infra externa.
- **Percepción en llamada**: TypeSafe `jev-1.13` (`typesafe-sdk`). Elige entre ids del escenario
  (`Choice`/`Score`/`Noul`), cada 5 s en llamada y al colgar. Sin clave o con `VELA_NO_JEV=true` cae a
  `fenic` con `Literal`. `fenic` ya no está en la ruta caliente: solo batch (`core/memory.py`) y plan B.
- **Asignación**: `scipy.optimize.linear_sum_assignment`. **Mundo**: Paper 1.21 + RCON, solo
  `/tp`, `/fill`, `/setblock`.
- **Voz**: HappyRobot en las dos direcciones + Humalike encima (`foresee`, `analyze`, `personas`);
  Humalike no toca la telefonía.
- **Dashboard**: Vite + React + TS + Tailwind, único sitio con TypeScript. **Deps**: `uv` + `pnpm`.

## Invariantes que no se rompen

1. **El LLM nunca toca Minecraft.** Minecraft renderiza un estado que vive en nuestro modelo.
2. **El LLM nunca asigna recursos.** Produce una `Policy` (pesos + restricciones); el solver produce
   el `Plan`. `Policy` no lleva nunca un campo `assignments`.
3. **Todo evento se escribe al journal antes de repartirse** (`publish` escribe y luego reparte).
4. **Todos importan de `contracts`; nadie importa del paquete de otro.** Se habla por el bus.
   Excepción única: `apps/gateway`.
5. **`WorldState` es inmutable**: `apply(state, ev)` devuelve uno nuevo.
6. **El journal es append-only**: si algo cambia, se emite otro evento.
7. **Una sola llamada al modelo de razonamiento por replan**, y solo con bandera: divergencia > 0,25,
   violación de restricción dura o hecho `severity: critical`.
8. **Un hecho asumido nunca se disfraza de observado.** Todo `Fact` lleva `kind: observed | inferred |
   assumed_default`. Solo `observed` funda una restricción dura o reabre una arista cortada; un
   `assumed_default` entra a `PlanContext.assumptions` con peso alto y se pinta gris cursiva.

## Arquitectura

Paquetes que solo se hablan por eventos: `contracts` (de nadie), `sim`, `core` (belief · planner ·
solver · verifiers · divergence), `voice`, `journal`.

- **Tick**: `sim` publica `world.*` → `belief` reconstruye el `WorldState` → `divergence` lo compara
  con `PlanContext.assumptions` → con bandera, `planner` → `Policy`, `solver` → `Plan`, `verifiers`
  (dos vueltas, luego plan del solver con pesos neutros) → `action.*` → `sim` y `voice`.
- **Llamada**: SSE + tool `report_fact` de HappyRobot (el tool es **solo disparador**) →
  `voice.perception`: tick de Jev → `budget` decide asertar, repreguntar (una a la vez, *signal*
  `kind: followup`) o, agotado el presupuesto por gravedad, rellenar con el LLM (`gapfill`) →
  `world.fact.asserted` + `call.completeness`.

## Comandos

- `make check`: mypy sobre `contracts` + `pytest`. Verde en cada merge a `main` (directo, sin PR).
- `make types`: regenera `apps/dashboard/src/types.ts` (**se genera, nunca a mano**).
- `make dev-core | dev-sim | dev-voice | dev-dash`: cada pieza sola con el resto simulado.
- `make demo`: todo de verdad; planes B con `FLAGS="--mock-calls"` / `"--no-minecraft"`.
- `make server | world | cam | replay RUN=<id>`: Paper, mundo por RCON, cámara, reproducir journal.
- `uv run python -m voice.jev --gate`: puerta de Jev, 10 transcripciones en español con la clave real.

## Propiedad de ficheros

Si necesitas algo de la carpeta de otro, pídelo por evento, no por import.

| Ruta | Dueño |
| --- | --- |
| `packages/contracts/**` | Nadie: los cuatro, en la ventana de contrato |
| `packages/core/**`, `packages/journal/**`, `voice/happyrobot.py` | Carlos |
| `packages/voice/**` (percepción, humalike, webhooks), `core/belief.py`, `core/ingest.py` | Hugo |
| `packages/sim/**`, `infra/**`, `scenarios/*.yaml` | Luis |
| `apps/gateway/**`, `apps/dashboard/**`, `scripts/demo.py` | Nacho |
| `fixtures/**` | Quien lo genera; solo se añade, nunca se modifica |

Contrato: **añadir un campo opcional con default es libre** (commit + aviso); renombrar, cambiar un
tipo o hacerlo obligatorio necesita a los cuatro; borrar está prohibido.

## Convenciones

- **Antes de tocar nada: commit de lo que haya (aunque sea `wip:`) y `git pull --rebase origin <rama>`.**
  Nunca `stash` como rutina.
- **Ids con prefijo**: `unit_truck1`, `poi_pueblo_a`, `wp_sur_03`, `cell_14_22`, `task_evac_a`; las
  aristas son `road:wp_a-wp_b` (el id es su dirección).
- **Coordenadas del mundo Minecraft** (x, z). `t_sim` (segundos) es el tiempo del dominio; `t_wall` solo depura.
- **`sim.execute` acepta cuatro verbos**: `goto`, `set_marker`, `announce`, `rescue`; otro emite
  `action.failed` con `error="unknown_verb"`.
- **Un payload que no valida nunca tumba el proceso**: lanza en desarrollo, `event.malformed` en la demo.
- **Secretos en `.env`** (raíz, gitignored; `.env.example` commiteado), cargado por `contracts/settings.py`.
- **Un servicio caído (Jev, Humalike, HappyRobot) se degrada y se anota**; nunca un `except: pass`.
