"""El estado del proceso, en un objeto y no en globals dispersos. P4.

El gateway es el único que importa de todos los paquetes, así que es el único que
puede arrancar medio sistema y quedarse en pie. `Runtime` es donde se anota qué
arrancó, qué no, y de dónde sale el estado que ve el dashboard.

Aquí vive **la única función que construye la forma del snapshot** (`snapshot`). La
usan el primer frame del WS y `GET /api/state`, y por eso el cliente tiene un solo
parser para el arranque, la recuperación por hueco y la reconexión.

Y aquí viven también `publish` y la dependencia `Rt`, que estaban en `main.py` hasta
el H4: `control.py` las necesita las dos, y `main.py` importa `control.py`. Este
fichero no importa de nadie del gateway salvo `hub`, así que es el único sitio donde
pueden estar sin cerrar un ciclo.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, Request
from pydantic import BaseModel

from contracts.events import Event, EventType
from contracts.plan import Plan
from contracts.world import WorldState
from gateway.hub import Hub

log = logging.getLogger("vela.gateway")

Status = Literal["up", "absent", "error", "degraded"]
"""`absent` es un `NotImplementedError`: el paquete existe pero aún no tiene cuerpo.
Es el estado normal de la mitad del sistema hasta el sábado, no un fallo."""

COMPONENTS = ("bus", "journal", "sim", "core", "voice", "replay")

SHUTDOWN_GRACE_S = 3.0
"""Lo que se le da a cada componente antes del `cancel()`. Ctrl-C en mitad de la
demo no puede dejar el RCON colgado ni el journal a medias."""


@dataclass
class Runtime:
    mode: str
    hub: Hub = field(default_factory=Hub)
    components: dict[str, Status] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    run_id: str | None = None
    scenario_id: str | None = None

    # Los dos planes B del H5, decididos por run en `POST /api/run` y no por entorno: la
    # bandera viaja con la petición que la usa. Se enseñan en `/api/health` y de ahí a la
    # cabecera del dashboard, porque una demo degradada que no se anuncia es una demo que
    # miente — y en el minuto cuatro no me voy a acordar de decirlo yo.
    minecraft: bool = True
    calls_mocked: bool = False

    # Los objetos de los demás. `Any` a propósito: el gateway los usa por su
    # superficie pública y no debe acoplarse a sus internos.
    core: Any | None = None
    sim: Any | None = None
    voice: Any | None = None

    # `runs/<run_id>.jsonl` del run en curso. Lo abre y lo escribe el bus
    # (`contracts.bus.configure`); aquí solo se recuerda la ruta para `/api/health`
    # y para devolverla en `POST /api/run/stop`.
    journal_path: Path | None = None

    # Estado plegado del replay, cuando no hay core que lo mantenga.
    replay_state: WorldState | None = None
    replay_plan: Plan | None = None

    tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    duplicate_actions: dict[str, int] = field(default_factory=dict)
    paused: bool = False

    # Peticiones a `/webhooks/*` rechazadas por token (H4). Se cuenta y se enseña en
    # `/api/health`: si alguien escanea el túnel en la sala, quiero ver el número subir
    # y no descubrirlo porque suene un teléfono en mitad del pitch.
    webhook_rejected: int = 0

    def __post_init__(self) -> None:
        for name in COMPONENTS:
            self.components.setdefault(name, "absent")

    # --- arranque degradado ----------------------------------------------------

    def mark(self, name: str, status: Status, note: str = "") -> None:
        self.components[name] = status
        if note:
            self.notes[name] = note
        log.info("componente %s → %s %s", name, status, note)

    @contextlib.contextmanager
    def guard(self, name: str):
        """Arrancar un componente sin que su ausencia tumbe el proceso.

        Un `NotImplementedError` es `absent`; cualquier otra cosa es `error`. En los
        dos casos el resto del sistema sigue arrancando: de eso vive todo el fin de
        semana, porque la mitad de los paquetes no tienen cuerpo hasta el sábado.
        """
        try:
            yield
        except NotImplementedError:
            self.mark(name, "absent", "sin implementar")
        except ImportError as exc:
            self.mark(name, "absent", f"no importable: {exc}")
        except Exception as exc:
            self.mark(name, "error", repr(exc))
            log.exception("fallo arrancando %s", name)

    def spawn(self, name: str, coro: Coroutine[Any, Any, Any]) -> None:
        """Un bucle largo en su propia task, supervisado.

        `Sim.start()` o `Core.run()` lanzando `NotImplementedError` dentro de la task
        no lo vería el `guard`: por eso la supervisión va aquí.
        """

        async def supervised() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except NotImplementedError:
                self.mark(name, "absent", "sin implementar")
            except Exception as exc:
                self.mark(name, "error", repr(exc))
                log.exception("la task de %s murió", name)

        self.tasks[name] = asyncio.create_task(supervised(), name=f"vela.{name}")

    async def stop_tasks(self, *names: str) -> None:
        """Para las tasks nombradas con cortesía y luego a la fuerza."""
        for name in names:
            task = self.tasks.pop(name, None)
            if task is None or task.done():
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(task, timeout=SHUTDOWN_GRACE_S)

    # --- publicar ----------------------------------------------------------------

    def new_run_id(self) -> str:
        """Uno nuevo por run, siempre. El gateway se lo da al bus en
        `bus.configure(run_id=...)`, y NO al revés: `bus.current_run_id()` conserva el
        id del run anterior después de `bus.close()`, y leerlo de ahí haría que el
        segundo run del ensayo escribiera encima del journal del primero."""
        return f"run_{uuid.uuid4().hex[:12]}"

    async def publish(
        self,
        type_: EventType,
        payload: BaseModel,
        source: str,
        causes: list[int] | None = None,
    ) -> Event:
        """Publicar por el bus si tiene cuerpo; si no, al menos que el dashboard lo vea.

        Sin bus, el evento se reparte solo al hub: se pierde el journal (invariante 3),
        así que esto es solo para desarrollo y queda anotado en `/api/health`.

        Devuelve el `Event` sellado porque quien publica necesita su `seq`: es lo que
        `POST /control/override` le contesta al dashboard para que sepa qué fila del
        chorro es la suya.

        `t_sim` sale del último evento visto por el hub, no de cero: lo que se publica
        desde aquí pasa AHORA, y los paneles ordenan por `t_sim`. `causes` es opcional de
        escribir y vale oro (`contracts.events.Event`): sin ella no hay cadena que
        recorrer y el colgar → giro no se puede medir.
        """
        ev = Event(
            run_id=self.run_id or "run_unknown",
            seq=self.hub.last_seq + 1,
            t_wall=datetime.now(UTC),
            t_sim=self.hub.last_t_sim,
            type=type_,
            source=source,
            payload=payload.model_dump(mode="json"),
            causes=causes or [],
        )
        try:
            from contracts.bus import publish

            await publish(ev)
        except (ImportError, NotImplementedError):
            self.notes["bus"] = "sin cuerpo: los eventos del gateway no llegan al journal"
            self.hub.dispatch(ev)
        return ev

    # --- el estado que ve el dashboard -----------------------------------------

    def world_state(self) -> WorldState | None:
        """El core manda si está arriba; si no, el estado plegado del replay."""
        if self.core is not None and self.components.get("core") == "up":
            try:
                return self.core.state()
            except NotImplementedError:
                self.mark("core", "degraded", "state() sin implementar")
            except Exception as exc:  # noqa: BLE001
                log.warning("Core.state() falló: %r", exc)
        return self.replay_state

    def current_plan(self) -> Plan | None:
        if self.core is not None and self.components.get("core") == "up":
            try:
                return self.core.current_plan()
            except NotImplementedError:
                pass
            except Exception as exc:  # noqa: BLE001
                log.warning("Core.current_plan() falló: %r", exc)
        return self.replay_plan

    def snapshot(self) -> dict:
        """El primer frame del WS y el cuerpo de `GET /api/state`, la misma forma.

        `state: null` es un estado válido y esperado: pasa antes del primer run y
        pasa en replay mientras `core.belief.apply` no exista. El dashboard ya lo
        tipa como `WorldState | null` y pinta los paneles vacíos.
        """
        state = self.world_state()
        plan = self.current_plan()
        return {
            "kind": "snapshot",
            "state": state.model_dump(mode="json") if state is not None else None,
            "plan": plan.model_dump(mode="json") if plan is not None else None,
            "seq": state.seq if state is not None else self.hub.last_seq,
        }

    def health(self) -> dict:
        from contracts.settings import settings

        return {
            "mode": self.mode,
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "paused": self.paused,
            # Los dos planes B, con la misma forma que `webhooks`: una palabra que se
            # puede pintar en la cabecera sin interpretarla.
            "minecraft": "encendido" if self.minecraft else "apagado",
            "calls": "simuladas" if self.calls_mocked else "reales",
            "components": dict(self.components),
            "notes": dict(self.notes),
            "duplicate_actions": dict(self.duplicate_actions),
            # Sin token no se bloquea nada, y hay que poder verlo: el viernes por la
            # noche el token no existe todavía y unos webhooks devolviendo 401 sin que
            # nadie sepa por qué cuestan una hora de depuración a las tres de la mañana.
            "webhooks": "activo" if settings.webhook_shared_token else "sin token",
            "webhook_rejected": self.webhook_rejected,
            **self.hub.stats(),
        }


# --- la dependencia de FastAPI ---------------------------------------------------
#
# Vive aquí y no en `main.py` porque `control.py` la necesita y `main.py` importa
# `control.py`. Es el mismo motivo por el que `publish` es un método de `Runtime`.


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


Rt = Annotated[Runtime, Depends(get_runtime)]
