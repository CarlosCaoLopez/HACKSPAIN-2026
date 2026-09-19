"""Cuando se agota el presupuesto de la llamada, el LLM rellena los huecos.

Es lo único que en la percepción *genera* algo, así que va acotado: solo puede elegir
entre las opciones cerradas del escenario (o `not_stated`, que significa "no hay base
para asumir nada") y todo lo que devuelva sale marcado `assumed_default`, en gris
cursiva en pantalla. Es una hipótesis que el sistema intenta falsar, no un hecho.

Nunca lanza ni bloquea: 2 s de tope; sin clave o con error, el monitor cae al valor
seguro de `budget.safe_default`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from contracts.questions import NOT_STATED, QuestionSpec
from contracts.settings import settings

log = logging.getLogger("voice.gapfill")

GAPFILL_TIMEOUT_S = 2.0
GAPFILL_MODEL = "claude-haiku-4-5"

SYSTEM = (
    "You fill gaps in an emergency call report. Given a phone transcript and, for each "
    "missing field, its closed list of allowed options, pick the most plausible option "
    "using ONLY what the caller said or strongly implies. If nothing supports a guess, "
    'answer "not_stated". When unsure about a hazard, choose the safe side (assume '
    "people may be trapped, assume a road is cut). Reply with ONLY a JSON object "
    "mapping each field to one allowed option."
)


def _parse(text: str, allowed: dict[str, list[str]]) -> dict[str, str]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        raw = json.loads(m.group(0))
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        k: str(v)
        for k, v in raw.items()
        if k in allowed and str(v) in allowed[k] and str(v) != NOT_STATED
    }


async def fill(
    fields: list[str], transcript: str, questions: dict[str, QuestionSpec]
) -> dict[str, str]:
    """campo → opción elegida, solo los que el modelo pudo justificar. Vacío si no hay
    clave, si falla o si tarda más de `GAPFILL_TIMEOUT_S`."""
    if not fields or not settings.anthropic_api_key:
        return {}
    allowed = {f: questions[f].options for f in fields if f in questions}
    if not allowed:
        return {}
    prompt = (
        f"Transcript:\n{transcript}\n\nMissing fields and allowed options:\n"
        + json.dumps(allowed, ensure_ascii=False)
    )
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        res = await asyncio.wait_for(
            client.messages.create(
                model=GAPFILL_MODEL,
                max_tokens=200,
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            ),
            GAPFILL_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 - la llamada sigue con el valor seguro
        log.warning("gapfill: %s", exc)
        return {}
    text = "".join(getattr(b, "text", "") for b in res.content)
    return _parse(text, allowed)
