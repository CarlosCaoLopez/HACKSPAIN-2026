"""Nivel 3 · asignación: Policy → Plan. Determinista, instantáneo, explicable.

Matriz de coste unidad × tarea con los pesos de la `Policy` más la distancia real
sobre el grafo, y `scipy.optimize.linear_sum_assignment` resuelve el emparejamiento
óptimo en microsegundos.

Las restricciones duras se aplican poniendo coste infinito en las celdas
prohibidas, así que el solver no puede violarlas ni queriendo. Una ambulancia no
extingue: `Unit.capabilities` contra `Task.required_capability`, coste infinito.
"""

from contracts.plan import Assignment, Plan, PlanContext, Policy, Violation
from contracts.world import WorldState

INFEASIBLE = float("inf")


def solve(state: WorldState, policy: Policy) -> Plan:
    """El plan óptimo bajo esos pesos. Lo que no se pudo cubrir sale en
    `unassigned_tasks`, y se muestra: un hueco visible es información."""
    raise NotImplementedError


def cost_matrix(state: WorldState, policy: Policy) -> list[list[float]]:
    """Filas = unidades, columnas = tareas. Puro, inspeccionable en un test."""
    raise NotImplementedError


def apply_hard_constraints(
    matrix: list[list[float]], state: WorldState, policy: Policy
) -> list[Violation]:
    """Pone INFEASIBLE donde toca. Devuelve las violaciones de restricciones que
    no reconoce: `Violation(verifier="unknown_constraint", severity="soft")`.

    Una restricción desconocida NO se ignora en silencio."""
    raise NotImplementedError


def build_context(state: WorldState, assignments: list[Assignment]) -> PlanContext:
    """Construye `PlanContext.assumptions`: lo que este plan da por cierto. Es lo que
    vigila el detector de divergencia, así que si aquí falta una suposición, el
    replan no se dispara."""
    raise NotImplementedError
