"""Pausa, override e inject manual. P4 es el único que publica `human.override`.

La intervención humana es requisito obligatorio del reto y la prueba visible del
criterio *Control*. Usadla en la demo al menos una vez, en directo.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from contracts.events import OverrideKind

router = APIRouter(prefix="/control", tags=["control"])


class InjectBody(BaseModel):
    inject_type: str
    payload: dict = {}


class OverrideBody(BaseModel):
    kind: OverrideKind
    target: str  # "unit_truck1" | "task_evac_a" | "road:wp_sur_03-wp_sur_04"
    value: str | float | bool | None = None
    note: str = ""


@router.post("/inject")
async def post_inject(body: InjectBody) -> dict:
    """Dispara un inject a mano. En el ensayo se usa constantemente; en el pitch
    es el botón de emergencia si algo se retrasa."""
    raise NotImplementedError


@router.post("/override")
async def post_override(body: OverrideBody) -> dict:
    """Se publica como `human.override` y el core lo trata con prioridad máxima."""
    raise NotImplementedError


@router.post("/pause")
async def post_pause(paused: bool = True) -> dict:
    """Congela el tick, para explicar algo en el pitch."""
    raise NotImplementedError
