"""El gateway. P4. El único que importa de todos: es su trabajo.

Monta `voice.router`, arranca `Sim` y `Core`, sirve el WS y el dashboard. Un solo
proceso: cada servicio separado es un modo de fallo más en un escenario con wifi
de hackathon.

P4 decide el orden de arranque y apaga limpio.

Tres decisiones que explican la forma del fichero:

1. **Arranque degradado.** Un paquete que no importa o que lanza
   `NotImplementedError` se anota en `Runtime.components` y no tumba el proceso.
   Hasta el sábado, media arquitectura está sin cuerpo: si el gateway esperase a
   todos, no habría dashboard que enseñar.
2. **Arrancar uvicorn no arranca un run.** El run empieza con `POST /api/run` (o
   solo, en `VELA_MODE=demo`). `runs/` es el dataset del criterio de aprendizaje y
   no se llena de journals vacíos por levantar el servidor.
3. **`VELA_MODE=replay` no necesita a nadie**: ni sim, ni core, ni voz, ni RCON.
   Lee un journal y lo empuja por el WS. Es `make dev-dash`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from contracts.events import Event, EventType, RunEnded, RunStarted
from contracts.settings import settings

from gateway import replay_source
from gateway.bridges import mount_bridges
from gateway.control import router as control_router
from gateway.runtime import SHUTDOWN_GRACE_S, Runtime
from gateway.scenarios import list_ids, load_scenario, scenario_path
from gateway.ws import router as ws_router

try:  # P3 puede tener el paquete a medias el viernes por la noche
    from voice import router as voice_router
except Exception:  # noqa: BLE001 — sin telefonía se arranca igual
    voice_router = None  # type: ignore[assignment]

log = logging.getLogger("vela.gateway")

SCENARIOS_DIR = Path("scenarios")
RUNS_DIR = Path("runs")
DEFAULT_SCENARIO = "wildfire_ridge"


# --- Ciclo de vida ---------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Arranque: bus → journal → sim → core → voice. Apagado, al revés."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s · %(message)s")
    rt = Runtime(mode=settings.vela_mode)
    app.state.runtime = rt

    if rt.mode == "replay":
        # Nada de sim, core ni voz: solo el hub y el journal que se reproduce.
        path = Path(settings.vela_replay_file)
        rt.spawn(
            "replay",
            replay_source.run(
                rt, path, settings.vela_replay_speed, settings.vela_replay_loop
            ),
        )
        log.info("modo replay · %s", path)
    else:
        _start_bus(rt)
        _check_journal(rt)
        _check_sim(rt)
        _check_core(rt)
        _check_voice(rt)
        if rt.mode == "demo":
            # El modo demo sí arranca solo: en el escenario no se hace un curl.
            await start_run(rt, DEFAULT_SCENARIO)

    try:
        yield
    finally:
        await _shutdown(rt)


def _start_bus(rt: Runtime) -> None:
    """La única suscripción al bus de todo el proceso, la del hub."""
    with rt.guard("bus"):
        from contracts.bus import subscribe

        rt.spawn("bus", rt.hub.run_from_bus(subscribe))
        rt.mark("bus", "up")


def _check_journal(rt: Runtime) -> None:
    """El writer se crea por run (necesita `run_id`); aquí solo se comprueba que
    el paquete está y se avisa de si hay dónde inyectarlo."""
    with rt.guard("journal"):
        from journal import JournalWriter  # noqa: F401

        rt.mark("journal", "up")


def _check_sim(rt: Runtime) -> None:
    with rt.guard("sim"):
        from sim import RconClient, Sim  # noqa: F401

        rt.mark("sim", "degraded", "importado; se construye al arrancar el run")


def _check_core(rt: Runtime) -> None:
    with rt.guard("core"):
        from core import Core  # noqa: F401

        rt.mark("core", "degraded", "importado; se construye al arrancar el run")


def _check_voice(rt: Runtime) -> None:
    with rt.guard("voice"):
        from voice import VoiceGateway

        rt.voice = VoiceGateway()
        rt.mark("voice", "up")


async def _shutdown(rt: Runtime) -> None:
    """Orden inverso: voice → core → sim → journal → bus.

    `run.ended` se publica ANTES de cerrar el writer, y el fallo al cerrar uno no
    impide cerrar los demás: un Ctrl-C en mitad de la demo no puede dejar el RCON
    colgado ni el journal a medias.
    """
    if rt.run_id is not None:
        with contextlib.suppress(Exception):
            await stop_run(rt)

    await rt.stop_tasks("voice", "core")
    if rt.sim is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(rt.sim.stop(), timeout=SHUTDOWN_GRACE_S)
    await rt.stop_tasks("sim", "replay")

    if rt.writer is not None:
        with contextlib.suppress(Exception):
            rt.writer.close()
        rt.writer = None

    await rt.stop_tasks("bus")
    for c in list(rt.hub.clients):
        c.mark_close(1001, "going away")
    log.info("apagado limpio")


# --- Arrancar y parar un run -----------------------------------------------------


def _new_run_id() -> str:
    """El uuid del run lo pone el bus; si aún no tiene cuerpo, uno propio."""
    with contextlib.suppress(Exception):
        from contracts.bus import current_run_id

        rid = current_run_id()
        if rid:
            return rid
    return f"run_{uuid.uuid4().hex[:12]}"


async def _publish(rt: Runtime, type_: EventType, payload: BaseModel, source: str) -> None:
    """Publicar por el bus si tiene cuerpo; si no, al menos que el dashboard lo vea.

    Sin bus, el evento se reparte solo al hub: se pierde el journal (invariante 3),
    así que esto es solo para desarrollo y queda anotado en `/api/health`.
    """
    ev = Event(
        run_id=rt.run_id or "run_unknown",
        seq=rt.hub.last_seq + 1,
        t_wall=datetime.now(UTC),
        t_sim=0.0,
        type=type_,
        source=source,
        payload=payload.model_dump(mode="json"),
    )
    try:
        from contracts.bus import publish

        await publish(ev)
    except (ImportError, NotImplementedError):
        rt.notes["bus"] = "sin cuerpo: los eventos del gateway no llegan al journal"
        rt.hub.dispatch(ev)


async def start_run(rt: Runtime, scenario_id: str) -> str:
    """Construye sim + core para ese escenario, abre el journal y publica
    `run.started`. Lo usan `POST /api/run` y el arranque del modo demo."""
    rt.run_id = _new_run_id()
    rt.scenario_id = scenario_id

    with rt.guard("journal"):
        from journal import JournalWriter

        rt.writer = JournalWriter(rt.run_id)
        # El writer se inyecta en el bus (contracts no importa de journal). El punto
        # de inyección lo define P1: si aparece, se usa; si no, queda anotado.
        import contracts.bus as bus

        setter = getattr(bus, "set_writer", None)
        if setter is not None:
            setter(rt.writer)
        else:
            rt.notes["journal"] = "falta el punto de inyección del writer en el bus (P1)"
        rt.mark("journal", "up", str(getattr(rt.writer, "path", "")))

    with rt.guard("sim"):
        from sim import RconClient, Sim

        rcon = RconClient(settings.rcon_host, settings.rcon_port, settings.rcon_password)
        rt.sim = Sim(scenario_path(scenario_id), rcon)
        rt.spawn("sim", rt.sim.start())
        rt.mark("sim", "up")

    with rt.guard("core"):
        import contracts.bus as bus
        from core import Core

        # `Core.__init__(bus, scenario)` pide un objeto `bus`, pero `contracts.bus`
        # expone funciones de módulo: se le pasa el módulo (duck typing sobre las
        # tres firmas) hasta que P1 decida si publica una clase `Bus`.
        rt.core = Core(bus, load_scenario(scenario_id))
        rt.spawn("core", rt.core.run())
        rt.mark("core", "up")

    mount_bridges(rt)
    await _publish(rt, EventType.RUN_STARTED, RunStarted(scenario_id=scenario_id), "core")
    log.info("run %s arrancado · escenario %s", rt.run_id, scenario_id)
    return rt.run_id


async def stop_run(rt: Runtime) -> dict:
    """Publica `run.ended`, cierra el journal y deja el proceso listo para otro run
    sin reiniciar uvicorn: las seis pasadas cronometradas del H5 dependen de eso."""
    if rt.run_id is None:
        return {"run_id": None, "stopped": False, "journal": None}

    run_id, scenario_id = rt.run_id, rt.scenario_id or DEFAULT_SCENARIO
    await _publish(
        rt, EventType.RUN_ENDED, RunEnded(scenario_id=scenario_id), "core"
    )  # antes de cerrar el writer, siempre

    await rt.stop_tasks("core")
    if rt.sim is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(rt.sim.stop(), timeout=SHUTDOWN_GRACE_S)
    await rt.stop_tasks("sim")

    journal_path = None
    if rt.writer is not None:
        journal_path = str(getattr(rt.writer, "path", "") or "") or None
        with contextlib.suppress(Exception):
            rt.writer.close()

    rt.writer, rt.core, rt.sim = None, None, None
    rt.run_id, rt.scenario_id = None, None
    rt.mark("sim", "degraded", "run parado")
    rt.mark("core", "degraded", "run parado")
    log.info("run %s parado", run_id)
    return {"run_id": run_id, "stopped": True, "journal": journal_path}


# --- La app ----------------------------------------------------------------------

app = FastAPI(title="vela", lifespan=lifespan)
if voice_router is not None:
    app.include_router(voice_router)
app.include_router(control_router)
app.include_router(ws_router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Nada de 500 con traza en mitad del pitch."""
    log.exception("error no manejado en %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": repr(exc)})


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


Rt = Annotated[Runtime, Depends(get_runtime)]


class RunBody(BaseModel):
    scenario_id: str


@app.get("/api/health")
async def get_health(rt: Rt) -> dict:
    """Qué arrancó y qué no. No está en `docs/interfaces.md` porque no lo consume
    nadie más: es mío, y es cómo se diagnostica el arranque a las tres de la mañana
    sin leer logs."""
    return rt.health()


@app.get("/api/state")
async def get_state(rt: Rt) -> dict:
    """Estado completo al abrir el dashboard.

    Devuelve **exactamente la forma del frame de snapshot** del WS, para que el
    cliente tenga un solo parser entre arranque, hueco y reconexión.
    """
    return rt.snapshot()


@app.get("/api/plan")
async def get_plan(rt: Rt) -> dict | None:
    """Plan vigente."""
    plan = rt.current_plan()
    return plan.model_dump(mode="json") if plan is not None else None


@app.get("/api/scenarios")
async def get_scenarios(rt: Rt) -> list[str]:
    """Escenarios disponibles. Con el loader de P2 si existe, y si no por nombre
    de fichero (que hoy coincide con el `id` de dentro)."""
    ids = list_ids()
    rt.notes.setdefault("scenarios", f"{len(ids)} escenarios en {SCENARIOS_DIR}")
    return ids


@app.post("/api/run")
async def post_run(body: RunBody, rt: Rt) -> dict:
    """`{scenario_id}` arranca un run, devuelve `run_id`."""
    if rt.mode == "replay":
        raise HTTPException(409, "en modo replay no se arrancan runs")
    if rt.run_id is not None:
        raise HTTPException(409, f"ya hay un run en curso: {rt.run_id}")
    if not scenario_path(body.scenario_id).exists():
        raise HTTPException(404, f"escenario desconocido: {body.scenario_id}")
    run_id = await start_run(rt, body.scenario_id)
    return {"run_id": run_id, "scenario_id": body.scenario_id}


@app.post("/api/run/stop")
async def post_run_stop(rt: Rt) -> dict:
    """Para el run actual y cierra el journal. Idempotente."""
    return await stop_run(rt)


@app.get("/api/runs")
async def get_runs(rt: Rt) -> list[dict]:
    """Runs pasados con su puntuación, para el run 1 vs run 12."""
    out: list[dict] = []
    for path in sorted(
        RUNS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        item: dict[str, Any] = {
            "run_id": path.stem,
            "path": str(path),
            "bytes": path.stat().st_size,
            "score": None,
            "partial": True,
        }
        try:
            from journal import score

            item["score"] = score(path).model_dump(mode="json")
            item["partial"] = False
        except (ImportError, NotImplementedError):
            rt.notes["score"] = "journal.score sin cuerpo"
        except Exception as exc:  # noqa: BLE001
            # Un journal a medias es la norma: cada Ctrl-C deja uno. No rompe.
            item["error"] = repr(exc)
        out.append(item)
    return out
