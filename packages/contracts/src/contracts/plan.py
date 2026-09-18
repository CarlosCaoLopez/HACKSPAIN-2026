"""Política, plan y violaciones.

`Policy` la escribe el modelo, `Plan` lo escribe el solver, `Violation` la escribe
un verificador. El catálogo de pesos y restricciones de abajo es contrato: el
prompt del planner lo enumera y el solver lo interpreta.
"""

from typing import Literal

from pydantic import BaseModel

from contracts.calls import Audience, Urgency


class NotifyIntent(BaseModel):
    poi_id: str
    audience: Audience
    message_intent: str  # intención, no guion literal
    urgency: Urgency


class Policy(BaseModel):
    """Lo único que produce el LLM. Nunca acciones.

    LA REGLA DE ORO: si `Policy` tuviera un campo `assignments`, todo el diseño se
    viene abajo. NO LO AÑADÁIS NUNCA, por muy tentador que sea a las cuatro de la
    mañana. El solver dando un resultado raro es información; el LLM asignando es
    una demo que no podéis defender.
    """

    rationale: str  # una frase, va al banner
    weights: dict[str, float] = {}  # claves de WEIGHTS
    hard_constraints: list[str] = []  # sintaxis "nombre" o "nombre:arg"
    horizon_s: int = 600
    escalate_to_human: bool = False
    notify: list[NotifyIntent] = []  # a quién hay que llamar y por qué


class Assignment(BaseModel):
    unit_id: str
    task_id: str
    route: list[str]  # waypoint ids, ya resuelta
    eta_s: float
    cost: float


class Assumption(BaseModel):
    key: str  # "road:wp_sur_03-wp_sur_04:open"
    expected: str | float | bool
    weight: float = 1.0  # cuánto pesa si se rompe


class PlanContext(BaseModel):
    """Lo que el plan da por cierto. Es lo que vigila el detector de divergencia."""

    assumptions: list[Assumption] = []
    world_seq: int  # estado sobre el que se planificó


class Plan(BaseModel):
    id: str
    run_id: str
    created_t: float
    policy: Policy
    assignments: list[Assignment] = []
    unassigned_tasks: list[str] = []  # lo que no se pudo cubrir, se muestra
    context: PlanContext


class Violation(BaseModel):
    verifier: str  # "route_feasible"
    severity: Literal["hard", "soft"]
    message: str  # legible, va al dashboard y al planner
    involved: list[str] = []  # ids afectados


# --- El catálogo -----------------------------------------------------------
#
# Si P1 añade un peso que el solver no conoce, se ignora en silencio y nadie se
# entera hasta la demo. Por eso `make check` valida los prompts contra esto.

WEIGHTS: frozenset[str] = frozenset(
    {
        "life_safety",  # tareas que tocan civiles expuestos
        "immobile_first",  # grupos con immobile > 0
        "structure_protection",  # tareas sobre POIs con edificios
        "containment",  # extinción en celdas a barlovento
        "response_time",  # penaliza ETA alta
    }
)


class ConstraintSpec(BaseModel):
    """Una restricción dura del catálogo y la forma de sus argumentos."""

    name: str
    args: list[str] = []  # nombres de los argumentos, en orden
    forbids: str  # qué prohíbe, legible


CONSTRAINTS: dict[str, ConstraintSpec] = {
    "no_unit_into_burning_cell": ConstraintSpec(
        name="no_unit_into_burning_cell",
        args=[],
        forbids="rutas que crucen celdas burning",
    ),
    "hospital_min_coverage": ConstraintSpec(
        name="hospital_min_coverage",
        args=["n"],
        forbids="bajar de n unidades en POIs de tipo hospital",
    ),
    "no_civilian_route_through": ConstraintSpec(
        name="no_civilian_route_through",
        args=["wp_id"],
        forbids="evacuaciones por ese waypoint",
    ),
    "reserve_capability": ConstraintSpec(
        name="reserve_capability",
        args=["cap", "n"],
        forbids="deja n unidades con esa capacidad libres",
    ),
}

UNKNOWN_CONSTRAINT = "unknown_constraint"
"""Verificador sintético: una restricción desconocida no se ignora, el solver
emite `Violation(verifier=UNKNOWN_CONSTRAINT, severity="soft")` y sale en el
dashboard."""

DIVERGENCE_THRESHOLD = 0.25
"""Por encima de esto, replan. Los otros dos disparadores son inmediatos:
violación de restricción dura y hecho con `severity="critical"`."""

MAX_REPLAN_ROUNDS = 2
"""Dos vueltas planner→solver→verifiers. A la tercera se cae al plan del solver
con pesos neutros, que siempre es factible. La demo nunca se queda sin plan."""


def parse_constraint(expr: str) -> tuple[str, list[str]]:
    """Parte "nombre:arg:arg" en (nombre, args). No valida contra el catálogo."""
    name, _, rest = expr.partition(":")
    return name, rest.split(":") if rest else []


def is_known_constraint(expr: str) -> bool:
    """True si el nombre existe en CONSTRAINTS y la aridad cuadra."""
    name, args = parse_constraint(expr)
    spec = CONSTRAINTS.get(name)
    return spec is not None and len(args) == len(spec.args)
