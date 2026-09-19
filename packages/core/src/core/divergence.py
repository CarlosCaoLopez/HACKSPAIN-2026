"""El detector de divergencia. La pieza que puntúa en Adaptación.

Cada tick se reevalúan las suposiciones del plan vigente contra el estado real:
`divergence = suposiciones_rotas_ponderadas / total_ponderado`.

Por encima de 0,25, replan. Además hay dos disparadores inmediatos: violación de
restricción dura, y llegada de un hecho con `severity: critical`.

El valor va al dashboard como una línea que sube y cruza el umbral justo antes del
banner rojo.
"""

from contracts.calls import Fact
from contracts.plan import DIVERGENCE_THRESHOLD, PlanContext
from contracts.world import WorldState
from core.belief import road_of

WIND_TOLERANCE_DEG = 20.0
"""Cuánto puede virar el rumbo del viento antes de contar la suposición como rota.
El coste de ruta apenas cambia con un viraje pequeño; solo un giro real invalida el
plan. Sin tolerancia, cualquier ruido de un grado dispara replans en cadena."""


def divergence(state: WorldState, ctx: PlanContext) -> tuple[float, list[str]]:
    """(valor, claves de las suposiciones rotas). Puro.

    `divergence = suposiciones_rotas_ponderadas / total_ponderado`. Las suposiciones
    no evaluables (clave que no se sabe leer, arista desaparecida) se saltan: ni suman
    al numerador ni al denominador, para no inflar falsos positivos."""
    broken_weight = 0.0
    total_weight = 0.0
    broken: list[str] = []

    for a in ctx.assumptions:
        actual = evaluate_assumption(state, a.key)
        if actual is None:
            continue
        total_weight += a.weight
        if _is_broken(a.key, actual, a.expected):
            broken_weight += a.weight
            broken.append(a.key)

    if total_weight == 0.0:
        return 0.0, []
    return broken_weight / total_weight, broken


def should_replan(
    value: float, violations_hard: int, critical_facts: list[Fact]
) -> tuple[bool, str]:
    """(bandera, motivo). El motivo va al banner y al prompt del planner tal cual.

    Si esto devuelve False no se llama al modelo, y eso es lo que mantiene el coste
    y la latencia bajo control."""
    if violations_hard > 0:
        return True, "restricción dura violada"
    if critical_facts:
        keys = ", ".join(f.key for f in critical_facts)
        return True, f"hecho crítico: {keys}"
    if value > DIVERGENCE_THRESHOLD:
        return True, f"divergencia {value:.2f} > {DIVERGENCE_THRESHOLD}"
    return False, ""


def evaluate_assumption(state: WorldState, key: str) -> str | float | bool | None:
    """El valor actual de una suposición ("road:wp_sur_03-wp_sur_04:open").

    Espejo de las claves que emite `solver.build_context`. `None` = clave que no se
    sabe leer o cuyo objeto ya no existe (suposición no evaluable)."""
    seg = key.split(":")

    if seg[0] == "road" and len(seg) == 3 and seg[2] == "open":
        edge = road_of(state.roads, seg[1])
        return None if edge is None else not edge.cut

    if key == "wind:bearing_deg":
        return state.wind.bearing_deg
    if key == "wind:speed":
        return state.wind.speed

    if seg[0] == "cell" and len(seg) == 3 and seg[2] == "state":
        cell = state.cells.get(seg[1])
        return None if cell is None else cell.state

    # Fallback: el último hecho con esa clave, si alguno entró al estado.
    for fact in reversed(state.facts):
        if fact.key == key:
            return fact.value
    return None


def _is_broken(
    key: str, actual: str | float | bool, expected: str | float | bool
) -> bool:
    """`actual != expected`. Los rumbos (grados) llevan tolerancia circular; el resto
    de floats, tolerancia relativa; bools y strings, igualdad exacta."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual != expected
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if key.endswith("bearing_deg"):
            return _angular_gap(float(actual), float(expected)) > WIND_TOLERANCE_DEG
        return abs(float(actual) - float(expected)) > 1e-6 * max(
            1.0, abs(float(expected))
        )
    return actual != expected


def _angular_gap(a: float, b: float) -> float:
    """Diferencia mínima entre dos ángulos en grados, con el wrap 0–360."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)
