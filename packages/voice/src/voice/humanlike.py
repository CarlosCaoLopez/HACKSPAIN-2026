"""humalike: el ciudadano llama.

Durante la llamada no pasa nada en el mundo: guardamos audio y transcripción
parcial. Resistid la tentación de actuar en streaming — añade latencia y modos de
fallo, y en escenario no se aprecia. Al colgar, el webhook trae la transcripción
completa y ahí empieza todo.
"""

from contracts.calls import CallResult

EXTRACT_TIMEOUT_S = 4.0
"""Por encima de esto se emite `CallResult` con `facts=None` y la transcripción
cruda va al dashboard marcada como *sin extraer*. La demo continúa."""


def parse_webhook(body: dict) -> CallResult:
    """El cuerpo del webhook de fin de llamada → `CallResult`, sin extraer aún."""
    raise NotImplementedError


def is_duplicate(call_id: str) -> bool:
    """Webhook duplicado (pasa): descartad por `call_id` ya visto. Idempotencia
    obligatoria."""
    raise NotImplementedError
