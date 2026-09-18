"""Nivel 2 · política: WorldState → Policy. La única llamada al LLM.

Una sola llamada al modelo de razonamiento por replan, y solo si se ha levantado
la bandera: divergencia > 0,25, violación de restricción dura, o hecho con
`severity: critical`. Si no, no se llama al modelo.

El modelo NUNCA devuelve acciones. Devuelve pesos y restricciones duras del
catálogo de `contracts.plan`; el solver hace el resto.
"""

from contracts.plan import Policy, Violation
from contracts.world import WorldState

TIMEOUT_S = 4.0
"""Si el planner tarda o alucina, se cae al plan del solver con pesos neutros."""


async def plan(state: WorldState, reason: str) -> Policy:
    """Una llamada, salida tipada corta. `reason` es el motivo del replan y va al
    prompt tal cual."""
    raise NotImplementedError


async def replan_with_critique(state: WorldState, violations: list[Violation]) -> Policy:
    """Segunda vuelta: el `Violation` vuelve al planner como crítica textual.

    Máximo dos vueltas (`contracts.plan.MAX_REPLAN_ROUNDS`). LLM-Modulo: el
    verificador es externo y correcto, no el propio LLM criticándose."""
    raise NotImplementedError


def neutral_policy() -> Policy:
    """Pesos neutros, sin restricciones. El plan degradado siempre es factible
    aunque sea subóptimo: la demo nunca se queda sin plan."""
    raise NotImplementedError


def render_prompt(state: WorldState, reason: str, rules: str) -> str:
    """`prompts/planner.md` + el estado + las reglas aprendidas entre runs.

    Todo peso y toda restricción que aparezca en el prompt tiene que existir en el
    catálogo: `make check` lo verifica, y es el fallo silencioso más probable de
    toda la arquitectura."""
    raise NotImplementedError
