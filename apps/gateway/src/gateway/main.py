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
4. **El bus se configura ANTES de construir nada.** `Sim` y `Core` leen
   `bus.current_run_id()` en su `__init__`, y `sim._publish` guarda los eventos en
   una reserva si no hay run: sin `bus.configure(run_id)` primero no fluye nada y
   el dashboard se queda en blanco. El journal `runs/<run_id>.jsonl` lo abre el
   propio bus (`contracts.bus.configure`), no un writer inyectado.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from contracts.events import EventType, RunEnded, RunStarted
from contracts.settings import E164, PHONE_KEYS, normalize_phone, settings
from gateway import replay_source
from gateway.bridges import mount_bridges
from gateway.control import router as control_router
from gateway.feeds.anchor import load_anchor
from gateway.feeds.poller import feeds_from_settings, off_status
from gateway.rcon_null import NullRcon
from gateway.runtime import SHUTDOWN_GRACE_S, Rt, Runtime
from gateway.scenarios import list_ids, load_scenario, scenario_path
from gateway.voice_canned import CannedVoice
from gateway.webhook_auth import webhook_token_guard
from gateway.ws import router as ws_router

try:  # P3 puede tener el paquete a medias el viernes por la noche
    from voice import router as voice_router
    from voice.telegram import router as telegram_router
except Exception:  # noqa: BLE001 — sin telefonía se arranca igual
    voice_router = None  # type: ignore[assignment]
    telegram_router = None  # type: ignore[assignment]

log = logging.getLogger("vela.gateway")

SCENARIOS_DIR = Path("scenarios")
RUNS_DIR = Path("runs")
DEFAULT_SCENARIO = "wildfire_ridge"
DASHBOARD_DIST = Path("apps/dashboard/dist")
"""El dashboard construido (`pnpm build`). Si existe, el gateway lo sirve en `/`: en el
VPS lo sirve Caddy igualmente, pero así `uvicorn` solo ya es una demo entera y el
`Dockerfile` no necesita un segundo servidor para probarlo."""

CAM_PLAYER_WAIT_S = 120.0
CAM_PLAYER_POLL_S = 5.0
"""Cuánto se espera a que el jugador-cámara entre en el servidor, y cada cuánto se mira.
El cliente headless tarda en arrancar más que el sim en reconstruir el mundo."""

PHONE_ROLES: tuple[dict[str, str], ...] = (
    {
        "key": "fire_crew",
        "label": "Retén de bomberos",
        "explica": "Recibe la primera llamada: qué camiones salen, a qué frente y por qué ruta.",
    },
    {
        "key": "ambulance",
        "label": "Dotación de la ambulancia",
        "explica": "Recibe la petición de las ambulancias para evacuar y, si hace falta, un rescate.",
    },
    {
        "key": "pueblo_a",
        "label": "Responsable de Pueblo A",
        "explica": "Recibe la orden de evacuación, con los medios que ya han confirmado.",
    },
    {
        "key": "pueblo_b",
        "label": "Pueblo B",
        "explica": "Recibe el aviso de que puede llegarle gente huyendo y, si el viento gira, su propia orden.",
    },
    {
        "key": "neighbor",
        "label": "Vecino que llama",
        "explica": "No recibe llamadas: es el móvil desde el que tú llamarás al 112 de la demo para contar lo que ves.",
    },
)
"""Lo que la landing pinta al lado de cada campo. Vive aquí y no en la landing para que
el orden y el texto salgan del mismo sitio que la validación."""


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
    """El journal del run lo escribe el bus (`contracts.bus.configure`); `journal` se
    usa aquí para puntuar (`journal.score` en `GET /api/runs`). Solo se comprueba que
    el paquete está."""
    with rt.guard("journal"):
        from journal import score  # noqa: F401

        rt.mark("journal", "up", "el journal del run lo abre el bus al arrancar")


def _check_sim(rt: Runtime) -> None:
    with rt.guard("sim"):
        from sim import RconClient, Sim  # noqa: F401

        rt.mark("sim", "degraded", "importado; se construye al arrancar el run")


def _check_core(rt: Runtime) -> None:
    with rt.guard("core"):
        from core import Core  # noqa: F401

        rt.mark("core", "degraded", "importado; se construye al arrancar el run")


def _check_voice(rt: Runtime) -> None:
    """`VoiceGateway` y, una vez por proceso, el despachador de señales de P3:
    `call.signal.requested` → el monitor de esa llamada (`voice.humanlike`). No es un
    puente del gateway: lo suscribe `voice`, aquí solo se le da la task."""
    with rt.guard("voice"):
        from voice import VoiceGateway

        rt.voice = VoiceGateway()
        rt.mark("voice", "up")
    with rt.guard("voice"):
        from voice import humanlike

        if "voice-signals" not in rt.tasks:
            rt.spawn("voice-signals", humanlike.signal_dispatcher())
    with rt.guard("voice"):
        # El segundo canal del reporte ciudadano: las señales de un chat `tg_*` van por
        # `sendMessage`, no por HappyRobot. Sin token el canal se anota y no manda.
        from voice import telegram

        if "voice-telegram" not in rt.tasks:
            rt.spawn("voice-telegram", telegram.signal_dispatcher())
        notes = []
        if not settings.telegram_bot_token or settings.vela_no_telegram:
            notes.append("sin token: el bot no contesta (canal ausente)")
        if not settings.telegram_secret_token:
            # La ruta no se deja abierta por olvidar una variable: responde 401 y se ve.
            notes.append("sin TELEGRAM_SECRET_TOKEN: /webhooks/telegram responde 401")
        if notes:
            rt.notes["telegram"] = " · ".join(notes)


async def _shutdown(rt: Runtime) -> None:
    """Orden inverso: voice → core → sim → journal → bus.

    `run.ended` se publica ANTES de cerrar el writer, y el fallo al cerrar uno no
    impide cerrar los demás: un Ctrl-C en mitad de la demo no puede dejar el RCON
    colgado ni el journal a medias.
    """
    if rt.run_id is not None:
        with contextlib.suppress(Exception):
            await stop_run(rt)

    await rt.stop_tasks(
        "feeds", "voice-telegram", "voice-signals", "voice", "bridges", "core"
    )
    if rt.sim is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(rt.sim.stop(), timeout=SHUTDOWN_GRACE_S)
    await rt.stop_tasks("sim", "replay")

    # Idempotente: `stop_run` ya lo cerró si había run. Un Ctrl-C antes del primer
    # run no deja nada abierto, pero cerrar dos veces no cuesta nada.
    _close_journal(rt)

    await rt.stop_tasks("bus")
    for c in list(rt.hub.clients):
        c.mark_close(1001, "going away")
    log.info("apagado limpio")


# --- Arrancar y parar un run -----------------------------------------------------


async def start_run(
    rt: Runtime,
    scenario_id: str,
    *,
    minecraft: bool = True,
    mock_calls: bool = False,
    speed: float = 1.0,
    phones: dict[str, str] | None = None,
) -> str:
    """Construye sim + core para ese escenario, abre el journal y publica
    `run.started`. Lo usan `POST /api/run` y el arranque del modo demo.

    `minecraft=False` y `mock_calls=True` son los planes B nivel 3 y 2: se deciden por
    run, quedan anotados en `Runtime` y salen en `/api/health`.

    `phones` son los teléfonos de la /demo autoservicio (ya validados por `RunBody`):
    se aplican sobre `settings` **antes** de construir `Core`, que los lee de ahí, y
    `stop_run` devuelve los del `.env`. Sin `phones`, el run llama a los del `.env`.
    """
    rt.run_id = rt.new_run_id()
    rt.scenario_id = scenario_id
    rt.minecraft, rt.calls_mocked = minecraft, mock_calls
    rt.started_at = datetime.now(UTC)
    rt.phones_given = bool(phones)
    rt.phones_prev = settings.apply_phones(phones) if phones else {}

    # PRIMERO el bus, y no es estilo: `Sim.__init__` y `Core.__init__` leen
    # `bus.current_run_id()`, y `sim._publish` guarda en una reserva todo lo que emite
    # sin run. Configurado aquí, el bus abre `runs/<run_id>.jsonl` él mismo y cada
    # `publish` escribe antes de repartir (invariante 3).
    with rt.guard("journal"):
        from contracts import bus

        bus.configure(run_id=rt.run_id, journal_dir=RUNS_DIR)
        rt.journal_path = RUNS_DIR / f"{rt.run_id}.jsonl"
        rt.mark("journal", "up", str(rt.journal_path))

    _load_voice_scenario(rt, scenario_id)

    with rt.guard("sim"):
        from sim import RconClient, Sim

        # Sin Minecraft NO se construye el cliente real: `connect` reintenta con backoff
        # y un puerto muerto son varios segundos de bloqueo al arrancar el run.
        rcon: Any = (
            RconClient(settings.rcon_host, settings.rcon_port, settings.rcon_password)
            if minecraft
            else NullRcon()
        )
        rt.sim = Sim(scenario_path(scenario_id), rcon)
        await _ask_for_speed(rt, speed)
        rt.spawn("sim", rt.sim.start())
        rt.mark("sim", "up", "" if minecraft else "sin Minecraft (plan B nivel 3)")

    if mock_calls:
        # El plan B nivel 2 se elige por run y no al arrancar el proceso: los ensayos del
        # domingo alternan llamada real y simulada sin reiniciar uvicorn.
        #
        # `voice.fake.FakeVoice` es de P3 y manda si tiene cuerpo; `CannedVoice` es mía y
        # solo entra si la suya no está. Se le pasa el `VoiceGateway` real para que la
        # extracción siga siendo la de P3 aunque la conversación sea enlatada.
        real_voice = rt.voice
        with rt.guard("voice"):
            try:
                from voice.fake import FakeVoice

                rt.voice = FakeVoice()
                rt.mark("voice", "up", "llamadas simuladas · voice.fake (P3)")
            except NotImplementedError:
                rt.voice = CannedVoice(rt, real=real_voice)
                rt.mark("voice", "up", "llamadas simuladas · guiones enlatados (P4)")

    with rt.guard("core"):
        from contracts import bus
        from core import Core

        # `Core.__init__(bus, scenario)` pide un objeto `bus`, pero `contracts.bus`
        # expone funciones de módulo: se le pasa el módulo (duck typing sobre las
        # tres firmas) hasta que P1 decida si publica una clase `Bus`.
        rt.core = Core(bus, load_scenario(scenario_id))
        rt.spawn("core", rt.core.run())
        rt.mark("core", "up", f"run {rt.run_id} · suscrito al bus")

    mount_bridges(rt)
    await rt.publish(EventType.RUN_STARTED, RunStarted(scenario_id=scenario_id), "core")
    # Después de `run.started`, no antes: así es lo primero que ve el journal del run y
    # ningún hecho de una fuente puede colarse por delante de él.
    _start_feeds(rt, scenario_id)
    _schedule_auto_stop(rt)
    _place_camera(rt, scenario_id)
    log.info("run %s arrancado · escenario %s", rt.run_id, scenario_id)
    return rt.run_id


def _schedule_auto_stop(rt: Runtime) -> None:
    """El run se para solo a los `VELA_RUN_MAX_S` segundos (0 = nunca).

    Es el candado de la /demo autoservicio: Paper solo aguanta un mundo a la vez, así
    que un run que nadie para bloquea la demo para el siguiente visitante. Se comprueba
    el `run_id` antes de parar: si alguien lo paró y arrancó otro, este temporizador no
    es el suyo."""
    max_s = settings.vela_run_max_s
    if max_s <= 0:
        return
    run_id = rt.run_id

    async def auto_stop() -> None:
        await asyncio.sleep(max_s)
        if rt.run_id != run_id:
            return
        # Se da de baja antes de parar: `stop_run` cancela la task `auto_stop`, y
        # cancelarse a uno mismo dejaría el `stop_run` a medias, con el journal abierto.
        rt.tasks.pop("auto_stop", None)
        log.info("run %s · %ss cumplidos, se para solo", run_id, max_s)
        await stop_run(rt)

    rt.spawn("auto_stop", auto_stop())


def _place_camera(rt: Runtime, scenario_id: str) -> None:
    """Pone al jugador-cámara (`VELA_CAM_PLAYER`) en espectador y en el plano `aguila`.

    Es la cámara fija de la /demo: nadie pulsa teclas. Espera a que el jugador esté en
    el servidor (el cliente headless tarda en entrar) y manda el `tp` por el RCON del
    sim, que ya está conectado. El gateway es el único autorizado a importar de `sim`.
    Sin jugador configurado, o sin Minecraft, no hace nada y no anota nada: no es una
    degradación, es la demo de siempre."""
    player = settings.vela_cam_player
    if not player or not rt.minecraft or rt.sim is None:
        return
    run_id = rt.run_id

    async def place() -> None:
        from sim.camera import HIGH, LOW, shots
        from sim.scenario import load

        rcon = rt.sim.rcon
        shot = shots(load(scenario_path(scenario_id)))["aguila"]
        deadline = asyncio.get_running_loop().time() + CAM_PLAYER_WAIT_S
        while rt.run_id == run_id:
            answer = await rcon.send(f"execute if entity {player}", LOW)
            if answer.startswith("Test passed"):
                await rcon.send(f"gamemode spectator {player}", HIGH)
                await rcon.send(shot.tp(player), HIGH)
                rt.notes["camera"] = f"{player} en espectador · plano {shot.name}"
                log.info("cámara · %s", rt.notes["camera"])
                return
            if asyncio.get_running_loop().time() > deadline:
                rt.notes["camera"] = (
                    f"{player} no ha entrado en {CAM_PLAYER_WAIT_S:.0f}s: sin cámara"
                )
                log.warning("cámara · %s", rt.notes["camera"])
                return
            await asyncio.sleep(CAM_PLAYER_POLL_S)

    rt.spawn("camera", place())


def _start_feeds(rt: Runtime, scenario_id: str) -> None:
    """Las fuentes reales (SPEC-007), solo con `VELA_FEEDS≠off`. Con `off` esto no hace
    nada: ni tarea, ni socket, ni nota (REQ-233).

    Un escenario sin ancla no es un error: es un escenario al que no se le pueden
    contrastar datos reales, y se anota (degradación explícita).
    """
    if settings.vela_feeds == "off":
        return
    with rt.guard("feeds"):
        anchor = load_anchor(scenario_id)
        if anchor is None:
            rt.mark("feeds", "absent", f"sin ancla para {scenario_id}")
            return
        rt.feeds = feeds_from_settings(rt, anchor, load_scenario(scenario_id))
        rt.spawn("feeds", rt.feeds.run())
        rt.mark("feeds", "up", settings.vela_feeds)


_voice_warmed = False
"""`voice.warmup()` levanta la sesión de fenic en un hilo: una vez por proceso."""


def _load_voice_scenario(rt: Runtime, scenario_id: str) -> None:
    """La tabla de POIs y carreteras que `voice` resuelve en caliente, y el prewarm.

    `voice` no importa de `sim` (invariante 4): la tabla se la da el gateway. Primero
    el YAML tal cual (trae los alias, si los hay); si no se puede leer o no tiene
    `pois`, el `Scenario` ya validado, sin alias. Sin esto, cada hecho de una llamada
    sale «sin ubicar» y no entra al estado.
    """
    global _voice_warmed
    with rt.guard("voice"):
        import voice
        from voice import pois

        path = scenario_path(scenario_id)
        if pois.load_scenario_yaml(path) and pois.pois():
            rt.notes["voice-pois"] = f"{len(pois.pois())} POIs de {path}"
        else:
            scenario = load_scenario(scenario_id)
            pois.set_scenario(scenario.pois, scenario.roads, geo=scenario.geo)
            rt.notes["voice-pois"] = f"{len(scenario.pois)} POIs del Scenario, sin alias"
        # `geo` es lo que proyecta un pin de Telegram al mundo; sin él se anota.
        rt.notes["voice-geo"] = (
            "anclaje geo cargado"
            if pois.geo()
            else "sin `geo`: los pines no se proyectan"
        )
        if not _voice_warmed:
            _voice_warmed = True
            voice.warmup()


async def _ask_for_speed(rt: Runtime, speed: float) -> None:
    """`--speed`: se le pide al sim, que puede no saber.

    `Sim` no expone `set_speed()`, pero su tick loop lee `self.speed`
    (`sim/runner.py`, D3): si hay método se usa; si no, se fija el atributo; y si no
    hay ni eso, se anota y el mundo va a 1×. Es la misma degradación explícita que
    `Sim.pause()` en el H4: ni se inventa el método ni se entra en el fichero de P2.
    """
    if speed == 1.0 or rt.sim is None:
        return
    setter = getattr(rt.sim, "set_speed", None)
    if setter is not None:
        result = setter(speed)
        if inspect.isawaitable(result):  # valen las dos formas
            await result
        rt.notes["speed"] = f"{speed}×"
        return
    if isinstance(getattr(rt.sim, "speed", None), int | float):
        rt.sim.speed = float(speed)
        rt.notes["speed"] = f"{speed}× (atributo `Sim.speed`, sin set_speed())"
        return
    rt.notes["speed"] = f"el sim no acepta velocidad: {speed}× ignorado, el mundo va a 1×"
    log.info("%s", rt.notes["speed"])


async def stop_run(rt: Runtime) -> dict:
    """Publica `run.ended`, cierra el journal y deja el proceso listo para otro run
    sin reiniciar uvicorn: las seis pasadas cronometradas del H5 dependen de eso."""
    if rt.run_id is None:
        return {"run_id": None, "stopped": False, "journal": None}

    run_id, scenario_id = rt.run_id, rt.scenario_id or DEFAULT_SCENARIO
    # El temporizador y la cámara no publican nada, pero un `stop_run` que dispare
    # dos veces (el temporizador y un `POST /api/run/stop` a la vez) sí sería un lío.
    await rt.stop_tasks("auto_stop", "camera")
    # Las fuentes primero: si siguieran vivas, un hecho podría entrar después de `run.ended`.
    await rt.stop_tasks("feeds")
    rt.feeds = None
    await rt.publish(
        EventType.RUN_ENDED, RunEnded(scenario_id=scenario_id), "core"
    )  # antes de cerrar el writer, siempre

    await rt.stop_tasks("core")
    if rt.sim is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(rt.sim.stop(), timeout=SHUTDOWN_GRACE_S)
    await rt.stop_tasks("sim")

    journal_path = str(rt.journal_path) if rt.journal_path is not None else None
    _close_journal(rt)

    rt.core, rt.sim = None, None
    rt.run_id, rt.scenario_id = None, None
    rt.started_at = None
    # Los teléfonos del `.env` vuelven: el siguiente run sin `phones` no debe llamar a
    # los del visitante anterior.
    if rt.phones_prev:
        settings.apply_phones(rt.phones_prev)
    rt.phones_prev, rt.phones_given = {}, False
    rt.mark("sim", "degraded", "run parado")
    rt.mark("core", "degraded", "run parado")
    log.info("run %s parado", run_id)
    return {"run_id": run_id, "stopped": True, "journal": journal_path}


def _close_journal(rt: Runtime) -> None:
    """Cierra el fichero del bus. Después de esto ningún evento llega al journal, así
    que va SIEMPRE después de `run.ended` y de parar sim y core."""
    with rt.guard("journal"):
        from contracts import bus

        bus.close()
    rt.journal_path = None


# --- La app ----------------------------------------------------------------------

app = FastAPI(title="vela", lifespan=lifespan)

# La landing (otro dominio) llama a `POST /api/run` y `GET /api/demo/status` desde el
# navegador: sin CORS el preflight muere. Solo los orígenes de `VELA_CORS_ORIGINS`,
# nunca `*`: es lo que separa «la landing arranca la demo» de «cualquier web arranca
# la demo». Vacío en local, como siempre.
_cors_origins = [o.strip() for o in settings.vela_cors_origins.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

TELEGRAM_WEBHOOK_PATH = "/webhooks/telegram"
"""Telegram no puede mandar `X-Vela-Token`: esa ruta trae su propio secreto
(`X-Telegram-Bot-Api-Secret-Token`) y lo comprueba `voice.telegram`. Se la exime del
token compartido **solo si ese secreto está configurado**; sin él, 401 y se anota."""
TELEGRAM_NO_SECRET_DETAIL = "sin TELEGRAM_SECRET_TOKEN"


async def _webhook_guard(request: Request, call_next):
    if request.url.path == TELEGRAM_WEBHOOK_PATH:
        if settings.telegram_secret_token:
            return await call_next(request)  # la ruta comprueba su propio secreto
        rt = getattr(request.app.state, "runtime", None)
        if rt is not None:
            rt.webhook_rejected += 1
        log.warning("webhook de Telegram rechazado: %s", TELEGRAM_NO_SECRET_DETAIL)
        return JSONResponse(
            status_code=401, content={"detail": TELEGRAM_NO_SECRET_DETAIL}
        )
    return await webhook_token_guard(request, call_next)


# Antes de montar nada: `/webhooks/*` es lo único que ve internet (va por un túnel) y
# el token se comprueba desde aquí, sin entrar en el router de P3.
app.middleware("http")(_webhook_guard)
if voice_router is not None:
    app.include_router(voice_router)
if telegram_router is not None:
    app.include_router(telegram_router, prefix="/webhooks")  # POST /webhooks/telegram
app.include_router(control_router)
app.include_router(ws_router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Nada de 500 con traza en mitad del pitch."""
    log.exception("error no manejado en %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": repr(exc)})


class RunBody(BaseModel):
    """El cuerpo de `POST /api/run`.

    Los dos planes B del H5 son campos **de aquí** y no variables de entorno: este
    modelo es del gateway, así que la bandera viaja con la petición que la usa y
    `packages/contracts/**` no se entera. `scripts/demo.py` los manda desde
    `--no-minecraft` y `--mock-calls`.
    """

    scenario_id: str
    minecraft: bool = True  # False = plan B nivel 3: ni se abre el socket RCON
    mock_calls: bool = False  # True = plan B nivel 2: `voice.fake` en vez de telefonía
    speed: float = 1.0  # multiplicador del mundo, si el sim sabe hacerlo
    # Los teléfonos de la /demo autoservicio, por interlocutor (`PHONE_KEYS`). Sin
    # ellos mandan los del `.env`. Un mismo número puede repetirse en varios papeles:
    # la landing lo avisa, no se prohíbe (un visitante solo tiene un móvil).
    phones: dict[str, str] | None = None

    @field_validator("phones")
    @classmethod
    def _phones_e164(cls, phones: dict[str, str] | None) -> dict[str, str] | None:
        if phones is None:
            return None
        out: dict[str, str] = {}
        for key, raw in phones.items():
            if key not in PHONE_KEYS:
                raise ValueError(f"teléfono desconocido: {key} (vale {', '.join(PHONE_KEYS)})")
            value = normalize_phone(str(raw))
            if not E164.match(value):
                raise ValueError(f"{key}: {raw!r} no es un número internacional (+34…)")
            out[key] = value
        return out


@app.get("/api/health")
async def get_health(rt: Rt) -> dict:
    """Qué arrancó y qué no. No está en `docs/interfaces.md` porque no lo consume
    nadie más: es mío, y es cómo se diagnostica el arranque a las tres de la mañana
    sin leer logs."""
    return rt.health()


@app.get("/api/feeds")
async def get_feeds(rt: Rt) -> dict:
    """Las fuentes reales: modo, ancla y el estado de cada una (SPEC-007 · REQ-260).

    Sin run —o con `VELA_FEEDS=off`— devuelve todo `off`. **En replay sirve el ancla aunque
    las fuentes no corran** (REQ-234, REQ-265): los hechos `api:*` llegan por el journal y el
    dashboard tiene que poder decir de qué sitio real son.
    """
    if rt.feeds is not None:
        return rt.feeds.status()
    mode = "replay" if rt.mode == "replay" else settings.vela_feeds
    anchor = load_anchor(rt.scenario_id or DEFAULT_SCENARIO) if mode != "off" else None
    return off_status(mode, anchor)


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


@app.get("/api/scenario")
async def get_current_scenario(rt: Rt) -> dict:
    """La capa estática del run en curso: geometría para el mapa del dashboard.

    El mapa necesita `origin` y `cell_size` para proyectar celdas, y las coordenadas de
    los waypoints para dibujar rutas y cortes de carretera — `world.road.changed` solo
    trae el `edge_id`. Nada de eso viaja por eventos, así que va por aquí y el
    dashboard lo pide una vez por run.
    """
    return _scenario_layer(rt, rt.scenario_id or DEFAULT_SCENARIO)


@app.get("/api/scenario/{scenario_id}")
async def get_scenario(scenario_id: str, rt: Rt) -> dict:
    """La de un escenario concreto, para comparar antes de arrancar un run."""
    return _scenario_layer(rt, scenario_id)


def _scenario_layer(rt: Runtime, scenario_id: str) -> dict:
    if not scenario_path(scenario_id).exists():
        raise HTTPException(404, f"escenario desconocido: {scenario_id}")
    return {
        **load_scenario(scenario_id).model_dump(mode="json"),
        "of_run": rt.run_id,  # null = es el escenario por defecto, no hay run
    }


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
        # La landing lee `retry_after_s` para decir «hay una demo en marcha, vuelve en
        # N minutos»; sin tope de tiempo, no puede prometer nada.
        raise HTTPException(
            409,
            {
                "reason": "busy",
                "message": f"ya hay un run en curso: {rt.run_id}",
                "run_id": rt.run_id,
                "retry_after_s": rt.ends_in_s(),
            },
        )
    if not scenario_path(body.scenario_id).exists():
        raise HTTPException(404, f"escenario desconocido: {body.scenario_id}")
    run_id = await start_run(
        rt,
        body.scenario_id,
        minecraft=body.minecraft,
        mock_calls=body.mock_calls,
        speed=body.speed,
        phones=body.phones,
    )
    return {
        "run_id": run_id,
        "scenario_id": body.scenario_id,
        # Se devuelve lo que se ha aplicado, no lo que se ha pedido: `demo.py` lo imprime
        # al arrancar y así el plan B se ve en la consola antes de que empiece el pitch.
        "minecraft": rt.minecraft,
        "mock_calls": rt.calls_mocked,
        "speed": rt.notes.get("speed", "1×"),
        # Lo que la landing necesita para llevar al visitante a mirar.
        "phones": "por petición" if rt.phones_given else "del .env",
        "watch_url": settings.vela_public_url or "/",
        "inbound_number": settings.happyrobot_inbound_number,
        "ends_in_s": rt.ends_in_s(),
    }


@app.get("/api/demo/status")
async def get_demo_status(rt: Rt) -> dict:
    """Lo que la landing y la pantalla de espera del dashboard preguntan cada pocos
    segundos: ¿hay demo en marcha, cuánto le queda, a qué número llama el vecino y
    qué papel tiene cada teléfono? Nunca devuelve teléfonos: los del run son de quien
    los tecleó."""
    return {
        "busy": rt.run_id is not None,
        "run_id": rt.run_id,
        "scenario_id": rt.scenario_id,
        "started_at": rt.started_at.isoformat() if rt.started_at else None,
        "ends_in_s": rt.ends_in_s(),
        "run_max_s": settings.vela_run_max_s,
        "inbound_number": settings.happyrobot_inbound_number,
        "watch_url": settings.vela_public_url or "/",
        "landing_url": settings.vela_landing_url,
        "cam_hls_url": settings.vela_cam_hls_url if settings.vela_cam_player else "",
        "minecraft": rt.minecraft if rt.run_id else None,
        "calls": ("simuladas" if rt.calls_mocked else "reales") if rt.run_id else None,
        "phone_roles": list(PHONE_ROLES),
        "default_scenario": DEFAULT_SCENARIO,
    }


@app.post("/api/run/stop")
async def post_run_stop(rt: Rt) -> dict:
    """Para el run actual y cierra el journal. Idempotente."""
    return await stop_run(rt)


@app.get("/api/runs")
async def get_runs(rt: Rt) -> list[dict]:
    """Runs pasados con su puntuación, para el run 1 vs run 12.

    Tres estados posibles por run, y el dashboard los pinta distintos porque son cosas
    distintas: **puntuado** por `journal.score` (P1, el normal), **provisional** cuando
    lo ha contado el gateway (`score_fallback`: solo si el puntuador de P1 no puede leer
    el journal), e **incompleto** cuando el journal no llega a `run.ended` —cada Ctrl-C
    deja uno, y `journal.replay.read` revienta en la línea cortada—. Un run sintético
    (`run_fake*`) se marca aparte: lo genero yo para desarrollar y no puede colarse en
    una comparación del pitch.
    """
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
            "provisional": False,
            "incomplete": False,
            "synthetic": path.stem.startswith("run_fake"),
            "notes": [],
        }
        try:
            from journal import score

            item["score"] = score(path).model_dump(mode="json")
            item["partial"] = False
        except (ImportError, NotImplementedError):
            rt.notes["score"] = (
                "journal.score no disponible: cuenta el gateway (provisional)"
            )
            _count_here(item, path)
        except Exception as exc:  # noqa: BLE001
            # Un journal a medias es la norma: cada Ctrl-C deja uno. `journal.score` no
            # lo lee (línea cortada); el contador del gateway sí, y lo dice.
            item["error"] = repr(exc)
            _count_here(item, path)
        out.append(item)
    return out


def _count_here(item: dict, path: Path) -> None:
    """La puntuación provisional del gateway, marcada como tal.

    `partial` sigue siendo cierto: hay campos que no están (`total`, la fórmula es de
    P1). `provisional` dice quién ha contado, que es lo que el panel enseña en pantalla.
    """
    from gateway import score_fallback

    try:
        counted = score_fallback.count(path)
    except Exception as exc:  # noqa: BLE001 — sin `journal` ni contar se puede
        item["error"] = repr(exc)
        return
    item["score"] = counted.as_json()
    item["provisional"] = True
    item["incomplete"] = counted.incomplete
    item["notes"] = counted.notes


# El último montaje, después de todas las rutas: `/` sirve el dashboard construido si
# existe. En local sin `pnpm build` no hay carpeta y no pasa nada (Vite sigue en :5173).
if DASHBOARD_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(DASHBOARD_DIST), html=True), name="dashboard")
