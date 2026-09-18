"""Alimentar el WS desde un journal en vez de desde el sim. P4.

Es lo que hace útil `make dev-dash`: con esto el dashboard se desarrolla entero sin
que existan `sim`, `core` ni `voice`, que es la mitad del sistema hasta el sábado.

Dos cosas que no se negocian:

- **Los eventos de replay no pasan por `publish`.** `run_id` y `seq` son los del
  fichero y no se escribe un journal nuevo: reproducir un run no puede generar otro
  run, o `runs/` se llena de fantasmas y `GET /api/runs` miente en el run 1 vs 12.
- **El replay nunca se bloquea esperando al core.** Si `core.belief.apply` aún no
  tiene cuerpo, el estado plegado se queda en `None`, el snapshot va con
  `state: null` y el chorro sigue.

TEMPORAL EN PARTE: `_stream_local` existe solo mientras `journal.replay.stream` no
tenga cuerpo. En cuanto P1 lo entregue, esta función se borra (y el resto del fichero
se queda: el plegado y el reparto son míos).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from contracts.events import Event, EventType
from contracts.plan import Plan

from gateway.runtime import Runtime
from gateway.scenarios import load_scenario

log = logging.getLogger("vela.replay")


async def run(rt: Runtime, path: Path, speed: float = 1.0, loop: bool = False) -> None:
    """Reproduce `path` en el hub hasta que se acabe (o para siempre, con `loop`)."""
    if not path.exists():
        rt.mark("replay", "error", f"no existe {path}")
        return

    rt.mark("replay", "up", f"{path} · speed={speed} · loop={loop}")
    folder = _Folder(rt)
    passes = 0
    while True:
        passes += 1
        async for ev in _stream(path, speed):
            await _wait_while_paused(rt)
            rt.run_id = ev.run_id
            folder.feed(ev)
            rt.hub.dispatch(ev)
        if not loop:
            break
        log.info("replay: vuelta %s terminada, repitiendo %s", passes, path)
        folder.reset()
    log.info("replay: %s agotado tras %s vuelta(s)", path, passes)
    rt.mark("replay", "degraded", "journal agotado")


PAUSE_POLL_S = 0.05
"""Lo que tarda en reanudar tras despausar. Imperceptible en pantalla y no gasta CPU
en un bucle apretado."""


async def _wait_while_paused(rt: Runtime) -> None:
    """`POST /control/pause` congela el chorro aquí.

    Pausar es **dejar de emitir**, no saltarse eventos: el siguiente `seq` que ve el
    cliente al reanudar es el que tocaba. Si se saltara alguno, el dashboard lo leería
    como hueco, pediría `GET /api/state` y se quedaría sin la historia en pantalla —
    justo cuando estoy parando la demo para explicar algo.
    """
    while rt.paused:
        await asyncio.sleep(PAUSE_POLL_S)


# --- las fuentes ----------------------------------------------------------------


async def _stream(path: Path, speed: float) -> AsyncIterator[Event]:
    """`journal.replay.stream` si tiene cuerpo; si no, el lector de reserva."""
    try:
        source = await _journal_stream(path, speed)
    except (ImportError, NotImplementedError, TypeError, AttributeError) as exc:
        log.info("journal.replay.stream no utilizable (%r): lector de reserva", exc)
        source = _stream_local(path, speed)

    # El fallback se decide ANTES de emitir el primer evento: si `stream` se rompe a
    # media reproducción, la excepción sube y no se reproduce el fichero dos veces.
    async for ev in source:
        yield ev


async def _journal_stream(path: Path, speed: float) -> AsyncIterator[Event]:
    """Resolver la fuente de P1, que hoy tiene dos formas posibles.

    `journal.replay.stream` está declarado `-> AsyncIterator[Event]` pero su cuerpo
    es `raise NotImplementedError` sin `yield`, así que **hoy es una corrutina** y
    no un async generator: `async for` sobre ella da `TypeError`, no
    `NotImplementedError`. En cuanto P1 le meta un `yield`, pasará a ser iterable
    directamente. Las dos formas se aceptan y ninguna necesita que yo toque su
    fichero.
    """
    from journal.replay import stream as journal_stream

    obj = journal_stream(path, speed)
    if hasattr(obj, "__aiter__"):
        return obj
    resolved = await obj  # corrutina: aquí salta el NotImplementedError de P1
    if hasattr(resolved, "__aiter__"):
        return resolved
    raise NotImplementedError("journal.replay.stream no devuelve un async iterable")


async def _stream_local(path: Path, speed: float) -> AsyncIterator[Event]:
    """Lector de reserva. Respeta los deltas de `t_sim`; `speed=0` va a tope.

    Se borra en cuanto exista `journal.replay.stream`.
    """
    last_t: float | None = None
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                ev = Event.model_validate_json(line)
            except Exception as exc:  # noqa: BLE001
                # Un journal a medias (todo Ctrl-C deja uno) no puede tumbar el
                # replay: se salta la línea y se sigue.
                log.warning("replay: línea %s ilegible, la salto · %r", n, exc)
                continue
            if last_t is not None and speed > 0:
                delta = (ev.t_sim - last_t) / speed
                if delta > 0:
                    await asyncio.sleep(delta)
            last_t = ev.t_sim
            yield ev


# --- el estado plegado ----------------------------------------------------------


class _Folder:
    """`WorldState` a base de `core.belief.apply`, si existe.

    El primer `NotImplementedError` apaga el plegado **para siempre** (una bandera,
    no un try por evento): si no, se pagaría una excepción por cada uno de los 500
    eventos del journal.
    """

    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.enabled = True

    def reset(self) -> None:
        self.rt.replay_state = None
        self.rt.replay_plan = None

    def feed(self, ev: Event) -> None:
        # `plan.emitted` trae el plan completo: no hay nada que derivar, se guarda.
        # Es exactamente lo que el dashboard hace en TypeScript por el mismo motivo.
        if ev.type == EventType.PLAN_EMITTED:
            try:
                self.rt.replay_plan = Plan.model_validate(ev.payload)
            except Exception as exc:  # noqa: BLE001
                log.warning("plan.emitted no valida en seq=%s: %r", ev.seq, exc)
        if ev.type == EventType.RUN_STARTED:
            self.rt.scenario_id = ev.payload.get("scenario_id")
            self.rt.replay_state = self._initial(ev)
        if not self.enabled or self.rt.replay_state is None:
            return
        try:
            from core.belief import apply

            self.rt.replay_state = apply(self.rt.replay_state, ev)
        except NotImplementedError:
            self._disable("core.belief.apply sin cuerpo")
        except Exception as exc:  # noqa: BLE001
            self._disable(f"apply falló en seq={ev.seq}: {exc!r}")

    def _initial(self, ev: Event):
        """`initial_state(run_id, scenario)`, con el escenario del `run.started`."""
        if not self.enabled:
            return None
        scenario_id = ev.payload.get("scenario_id", "")
        try:
            from core.belief import initial_state

            return initial_state(ev.run_id, load_scenario(scenario_id))
        except NotImplementedError:
            self._disable("core.belief.initial_state sin cuerpo")
        except FileNotFoundError:
            self._disable(f"no encuentro scenarios/{scenario_id}.yaml")
        except Exception as exc:  # noqa: BLE001
            self._disable(f"initial_state falló: {exc!r}")
        return None

    def _disable(self, why: str) -> None:
        self.enabled = False
        self.rt.replay_state = None
        # Info y no warning: hasta el sábado esto es lo normal, no una avería.
        log.info("replay: estado sin plegar (%s). El snapshot irá con state=null", why)
        self.rt.notes["replay_state"] = why
