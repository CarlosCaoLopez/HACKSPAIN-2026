"""El gateway. P4. El único que importa de todos: es su trabajo.

Monta `voice.router`, arranca `Sim` y `Core`, sirve el WS y el dashboard. Un solo
proceso: cada servicio separado es un modo de fallo más en un escenario con wifi
de hackathon.

P4 decide el orden de arranque y apaga limpio.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from gateway.control import router as control_router
from gateway.ws import router as ws_router
from voice import router as voice_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Arranque: bus → journal → sim → core → voice. Apagado, al revés."""
    raise NotImplementedError
    yield


app = FastAPI(title="vela", lifespan=lifespan)
app.include_router(voice_router)
app.include_router(control_router)
app.include_router(ws_router)


@app.get("/api/state")
async def get_state() -> dict:
    """Estado completo al abrir el dashboard."""
    raise NotImplementedError


@app.get("/api/plan")
async def get_plan() -> dict | None:
    """Plan vigente."""
    raise NotImplementedError


@app.get("/api/scenarios")
async def get_scenarios() -> list[str]:
    """Escenarios disponibles."""
    raise NotImplementedError


@app.post("/api/run")
async def post_run(body: dict) -> dict:
    """`{scenario_id}` arranca un run, devuelve `run_id`."""
    raise NotImplementedError


@app.post("/api/run/stop")
async def post_run_stop() -> dict:
    """Para el run actual y cierra el journal."""
    raise NotImplementedError


@app.get("/api/runs")
async def get_runs() -> list[dict]:
    """Runs pasados con su puntuación, para el run 1 vs run 12."""
    raise NotImplementedError
