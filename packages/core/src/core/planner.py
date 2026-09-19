"""Nivel 2 · política: WorldState → Policy. La única llamada al LLM.

Una sola llamada al modelo de razonamiento por replan, y solo si se ha levantado
la bandera: divergencia > 0,25, violación de restricción dura, o hecho con
`severity: critical`. Si no, no se llama al modelo.

El modelo NUNCA devuelve acciones. Devuelve pesos y restricciones duras del
catálogo de `contracts.plan`; el solver hace el resto.

Modelo: GPT-5.6 Luna (OpenAI). El tier barato/rápido cabe en el presupuesto de 4 s.
Salida tipada por function-calling forzado, con el propio modelo Pydantic `Policy`
como esquema (`Policy.model_json_schema()`): el mismo contrato es el esquema.
"""

import asyncio
import copy
import json
import logging
from pathlib import Path
from typing import get_args

from openai import AsyncOpenAI
from pydantic import ValidationError

from contracts.calls import Audience, Urgency
from contracts.plan import Policy, Violation
from contracts.settings import settings
from contracts.world import WorldState

log = logging.getLogger("core.planner")

MODEL = "gpt-5.6-luna"
"""OpenAI. Se cambia aquí y en ningún otro sitio."""

TIMEOUT_S = 4.0
"""Si el planner tarda o alucina, se cae al plan del solver con pesos neutros."""

MAX_TOKENS = 1024

_PROMPTS = Path(__file__).parent / "prompts"

URGENCIES: tuple[str, ...] = get_args(Urgency)
AUDIENCES: tuple[str, ...] = get_args(Audience)
URGENCY_FALLBACK: dict[str, str] = {"high": "medium", "urgent": "critical"}
"""Lo que el modelo escribe cuando se sale del contrato («high» no existe en
`Urgency`) y a qué valor del contrato se lleva. Con el error de validación entero se
caía a la política neutra y se perdían los pesos buenos por un aviso mal escrito."""


def _policy_schema() -> dict:
    """El esquema de `Policy` con los `$defs` en línea y cada campo `Literal` con su
    `enum` explícito a la vista del modelo. Pydantic ya emite el `enum`, pero dentro
    de `$defs/NotifyIntent` tras un `$ref`, y el modelo devolvió `urgency="high"`."""
    schema = copy.deepcopy(Policy.model_json_schema())
    defs = schema.pop("$defs", {})

    def inline(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                return inline(copy.deepcopy(defs[name]))
            return {k: inline(v) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    schema = inline(schema)
    notify = schema["properties"]["notify"]["items"]["properties"]
    notify["urgency"]["enum"] = list(URGENCIES)
    notify["urgency"]["description"] = "Exactamente uno de: " + ", ".join(URGENCIES)
    notify["audience"]["enum"] = list(AUDIENCES)
    notify["audience"]["description"] = "Exactamente uno de: " + ", ".join(AUDIENCES)
    return schema


# El function-calling forzado garantiza salida estructurada; el esquema es el
# propio modelo Pydantic, así lo que valida el solver y lo que pide el prompt no
# pueden divergir.
_POLICY_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_policy",
        "description": (
            "Emite la Policy para esta situación: pesos de objetivo y "
            "restricciones duras del catálogo. Nunca acciones ni asignaciones."
        ),
        "parameters": _policy_schema(),
    },
}


def parse_policy(args: str | dict) -> Policy:
    """`Policy` desde lo que devolvió el modelo. Un `notify` con `urgency` o
    `audience` fuera del contrato se corrige (`URGENCY_FALLBACK`) o se descarta, con
    aviso, antes de validar: el resto de la política (pesos, restricciones) vale y no
    se tira por un campo de un aviso. Lanza `ValidationError` si aun así no valida."""
    data = json.loads(args) if isinstance(args, str) else dict(args)
    kept: list[dict] = []
    for item in data.get("notify") or []:
        if not isinstance(item, dict):
            log.warning("planner: notify no es un objeto, se descarta: %r", item)
            continue
        urgency = item.get("urgency")
        if urgency not in URGENCIES:
            fixed = URGENCY_FALLBACK.get(str(urgency).lower(), "medium")
            log.warning("planner: urgency %r fuera del contrato → %s", urgency, fixed)
            item = {**item, "urgency": fixed}
        if item.get("audience") not in AUDIENCES:
            log.warning(
                "planner: audience %r fuera del contrato → resident", item.get("audience")
            )
            item = {**item, "audience": "resident"}
        kept.append(item)
    if "notify" in data:
        data["notify"] = kept
    try:
        return Policy.model_validate(data)
    except ValidationError:
        log.warning("planner: la Policy no valida: %s", json.dumps(data)[:300])
        raise


_TOOL_CHOICE = {"type": "function", "function": {"name": "emit_policy"}}


def neutral_policy() -> Policy:
    """Pesos neutros, sin restricciones. El plan degradado siempre es factible
    aunque sea subóptimo: la demo nunca se queda sin plan."""
    return Policy(
        rationale="Plan degradado: pesos neutros",
        weights={},
        hard_constraints=[],
    )


def _summarize(state: WorldState) -> str:
    """Resumen compacto del `WorldState` para el prompt. Solo lo que el planner
    necesita para pesar objetivos; nada de píxeles ni de estado interno."""
    lines: list[str] = []
    lines.append(f"t_sim={state.t_sim:.0f}s")
    lines.append(
        f"viento: rumbo={state.wind.bearing_deg:.0f}° velocidad={state.wind.speed:.1f} celdas/min"
    )

    lines.append("Unidades:")
    for u in state.units.values():
        caps = ",".join(u.capabilities) or "-"
        lines.append(
            f"  {u.id} {u.kind} status={u.status} pos=({u.x:.0f},{u.z:.0f}) capacidades=[{caps}]"
        )

    lines.append("Tareas abiertas:")
    open_tasks = [t for t in state.tasks.values() if not t.done]
    if not open_tasks:
        lines.append("  (ninguna)")
    for t in open_tasks:
        target = t.target_poi or t.target_cell or "-"
        lines.append(
            f"  {t.id} {t.kind} objetivo={target} severidad={t.severity} "
            f"requiere={t.required_capability}"
        )

    lines.append("Civiles:")
    for c in state.civilians.values():
        lines.append(
            f"  {c.id} en {c.poi_id} count={c.count} inmóviles={c.immobile} estado={c.state}"
        )

    lines.append("POIs:")
    for p in state.pois.values():
        lines.append(f"  {p.id} '{p.name}' {p.kind} cobertura_mínima={p.min_coverage}")

    hot = [c.id for c in state.cells.values() if c.state in ("burning", "at_risk")]
    if hot:
        lines.append("Celdas en llamas/en riesgo: " + ", ".join(sorted(hot)))

    cut = [e.id for e in state.roads.values() if e.cut]
    if cut:
        lines.append("Aristas cortadas: " + ", ".join(sorted(cut)))

    return "\n".join(lines)


def render_prompt(state: WorldState, reason: str, rules: str) -> str:
    """`prompts/planner.md` + el estado + las reglas aprendidas entre runs.

    Todo peso y toda restricción que aparezca en el prompt tiene que existir en el
    catálogo: `make check` lo verifica, y es el fallo silencioso más probable de
    toda la arquitectura."""
    template = (_PROMPTS / "planner.md").read_text()
    return (
        template.replace("<<STATE>>", _summarize(state))
        .replace("<<REASON>>", reason)
        .replace("<<RULES>>", rules or "(ninguna)")
    )


def _render_critique(state: WorldState, violations: list[Violation]) -> str:
    template = (_PROMPTS / "replan_critique.md").read_text()
    viol_text = "\n".join(f"- {v.message}" for v in violations) or "(ninguna)"
    return template.replace("<<STATE>>", _summarize(state)).replace(
        "<<VIOLATIONS>>", viol_text
    )


async def _call(prompt: str) -> Policy:
    """Una llamada a OpenAI con tool-use forzado, dentro del presupuesto de tiempo.
    Cualquier fallo (timeout, red, JSON inválido, validación, sin key) cae a pesos
    neutros: la demo nunca se queda sin `Policy`. Pero se anota siempre: un servicio
    caído se degrada y se registra, nunca un `except: pass`."""
    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=MODEL,
                max_completion_tokens=MAX_TOKENS,
                # gpt-5.6-luna rechaza tools con razonamiento en chat.completions
                # (400: «use /v1/responses or set reasoning_effort to 'none'»).
                reasoning_effort="none",
                tools=[_POLICY_TOOL],
                tool_choice=_TOOL_CHOICE,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=TIMEOUT_S,
        )
        args = resp.choices[0].message.tool_calls[0].function.arguments
        return parse_policy(args)
    except Exception as exc:  # noqa: BLE001 — degradar a pesos neutros es el diseño
        log.error("planner %s falló; se cae a la política neutra: %r", MODEL, exc)
        return neutral_policy()


async def plan(state: WorldState, reason: str, rules: str = "") -> Policy:
    """Una llamada, salida tipada corta. `reason` es el motivo del replan y va al
    prompt tal cual. `rules` son las reglas de memoria ya seleccionadas por trigger
    (solo las que casan con el estado), resumidas a una frase cada una."""
    return await _call(render_prompt(state, reason, rules))


async def replan_with_critique(state: WorldState, violations: list[Violation]) -> Policy:
    """Segunda vuelta: el `Violation` vuelve al planner como crítica textual.

    Máximo dos vueltas (`contracts.plan.MAX_REPLAN_ROUNDS`). LLM-Modulo: el
    verificador es externo y correcto, no el propio LLM criticándose. El límite de
    vueltas lo aplica `loop.py`; aquí solo se produce una `Policy` corregida."""
    return await _call(_render_critique(state, violations))
