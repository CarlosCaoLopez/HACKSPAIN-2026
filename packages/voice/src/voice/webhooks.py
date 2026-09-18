"""Los routers FastAPI de telefonía. P3 los registra; no toca `main.py`.

Autenticación: ninguna, todo corre en localhost salvo estos dos, que van por un
túnel. Token compartido en la cabecera para que un escaneo aleatorio no dispare
una llamada a mitad del pitch.
"""

from fastapi import APIRouter, Header, Request

router = APIRouter(prefix="/webhooks", tags=["voice"])

TOKEN_HEADER = "X-Vela-Token"


@router.post("/happyrobot/call")
async def happyrobot_call(
    request: Request, x_vela_token: str = Header(default="")
) -> dict:
    """Eventos de inicio, fin y fallo de llamada saliente.

    La carga incluye `type` (`start` | `end`), `call.id` y `call.metadata.custom`,
    de donde sale nuestro `task_id`. Publica `call.started` o `call.ended`.
    """
    raise NotImplementedError


@router.post("/humalike/call")
async def humalike_call(
    request: Request, x_vela_token: str = Header(default="")
) -> dict:
    """Llamada entrante terminada. Transcripción completa → `semantic.extract` →
    `CallResult` → un `world.fact.asserted` por cada campo no nulo.

    Idempotente por `call_id`: los webhooks duplicados pasan.
    """
    raise NotImplementedError
