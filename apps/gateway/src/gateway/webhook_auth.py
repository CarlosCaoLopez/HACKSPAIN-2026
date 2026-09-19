"""El token compartido de los webhooks. P4.

Todo corre en localhost salvo `/webhooks/*`, que van por un túnel: son la única
superficie del sistema que ve internet. Un escaneo aleatorio no puede dispararnos una
llamada de teléfono a mitad del pitch.

Se comprueba **aquí y no dentro de `voice/webhooks.py`**, que es de Hugo: el middleware
es del gateway y no necesita que él toque su fichero. Si además lo comprueba él, no
pasa nada — validar dos veces es inofensivo, al revés que ejecutar una acción dos veces
(ver los puentes de `bridges.py`).
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from contracts.settings import settings

log = logging.getLogger("vela.webhooks")

PREFIX = "/webhooks/"
TOKEN_HEADER = "X-Vela-Token"  # el mismo que declara `voice/webhooks.py`


async def webhook_token_guard(
    request: Request, call_next: RequestResponseEndpoint
) -> JSONResponse:
    """401 si la cabecera no trae el token compartido.

    Con `WEBHOOK_SHARED_TOKEN` vacío **no se bloquea nada**: el viernes por la noche el
    token todavía no existe, y unos webhooks devolviendo 401 sin que nadie sepa por qué
    cuestan una hora de depuración a las tres de la mañana. `GET /api/health` dice en
    cuál de los dos modos está.
    """
    expected = settings.webhook_shared_token
    if (
        expected
        and request.url.path.startswith(PREFIX)
        and request.headers.get(TOKEN_HEADER, "") != expected
    ):
        rt = getattr(request.app.state, "runtime", None)
        if rt is not None:
            rt.webhook_rejected += 1
        log.warning("webhook rechazado en %s desde %s", request.url.path, request.client)
        return JSONResponse(status_code=401, content={"detail": "token inválido"})
    return await call_next(request)
