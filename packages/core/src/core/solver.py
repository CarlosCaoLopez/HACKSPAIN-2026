"""Nivel 3 · asignación: Policy → Plan. Determinista, instantáneo, explicable.

Matriz de coste unidad × tarea con los pesos de la `Policy` más la distancia real
sobre el grafo, y `scipy.optimize.linear_sum_assignment` resuelve el emparejamiento
óptimo en microsegundos.

Las restricciones duras se aplican poniendo coste infinito en las celdas
prohibidas, así que el solver no puede violarlas ni queriendo. Una ambulancia no
extingue: `Unit.capabilities` contra `Task.required_capability`, coste infinito.

El grafo vive dentro de core (nada de `from sim import ...`): se construye desde el
`Scenario` compartido, que ya recibe `Core.__init__`, y se refresca con los cortes
vivos de `WorldState.roads`. `solve` lo acepta como parámetro opcional para seguir
siendo puro y testeable en aislamiento; si falta, cae a distancia euclídea.
"""

import heapq
import math
from itertools import pairwise

from contracts.calls import Fact
from contracts.factkeys import road_open_key
from contracts.plan import (
    UNKNOWN_CONSTRAINT,
    Assignment,
    Assumption,
    Plan,
    PlanContext,
    Policy,
    Violation,
    is_known_constraint,
    parse_constraint,
)
from contracts.scenario import Scenario
from contracts.world import POI, Cell, Task, Unit, WorldState

INFEASIBLE = float("inf")

UNIT_SPEED_MPS = 8.0
"""Velocidad plana para pasar de metros de ruta a `eta_s`. No es física, es un
orden de magnitud estable para que `response_time` compare peras con peras."""

WEIGHT_DISCOUNT = 0.35
"""Cada peso pertinente abarata la tarea multiplicando el coste por este factor.
Menor = el LLM manda más. El solver sigue siendo quien asigna."""


class RoadGraph:
    """Copia de solo lectura del grafo de carreteras, propiedad de core.

    Reimplementa un Dijkstra mínimo; NO importa `sim/graph.py`. Las coordenadas de
    los waypoints salen del `Scenario` (las aristas de `WorldState.roads` no las
    traen), y los cortes vivos se refrescan con `with_cuts`.
    """

    def __init__(
        self,
        coords: dict[str, tuple[float, float]],
        adj: dict[str, list[tuple[str, float, str]]],
        origin: tuple[float, float] = (0.0, 0.0),
        cell_size: int = 4,
    ) -> None:
        # adj[a] = [(b, length_m, edge_id), ...], simétrico
        self.coords = coords
        self.adj = adj
        self.origin = origin
        self.cell_size = cell_size

    @classmethod
    def from_scenario(cls, scenario: Scenario) -> "RoadGraph":
        coords = {wp.id: (wp.x, wp.z) for wp in scenario.waypoints}
        adj: dict[str, list[tuple[str, float, str]]] = {
            wp.id: [] for wp in scenario.waypoints
        }
        for edge in scenario.roads:
            if edge.cut:
                continue
            adj.setdefault(edge.a, []).append((edge.b, edge.length_m, edge.id))
            adj.setdefault(edge.b, []).append((edge.a, edge.length_m, edge.id))
        return cls(coords, adj, scenario.origin, scenario.hazard.cell_size)

    def with_cuts(self, state: WorldState) -> "RoadGraph":
        """Devuelve un grafo nuevo con los cortes vivos de `state.roads` aplicados.
        No muta: el estado y el grafo base siguen intactos."""
        cut_ids = {e.id for e in state.roads.values() if e.cut}
        if not cut_ids:
            return self
        adj = {
            node: [(b, length, eid) for (b, length, eid) in edges if eid not in cut_ids]
            for node, edges in self.adj.items()
        }
        return RoadGraph(self.coords, adj, self.origin, self.cell_size)

    def nearest_waypoint(self, x: float, z: float) -> str | None:
        best: str | None = None
        best_d = INFEASIBLE
        for wid, (wx, wz) in self.coords.items():
            d = math.hypot(wx - x, wz - z)
            if d < best_d:
                best_d, best = d, wid
        return best

    def shortest_path(self, a: str, b: str) -> list[str] | None:
        """Polilínea de waypoint ids (incluye extremos), o None si no hay ruta viva."""
        if a == b:
            return [a]
        if a not in self.adj or b not in self.adj:
            return None
        dist = {a: 0.0}
        prev: dict[str, str] = {}
        pq: list[tuple[float, str]] = [(0.0, a)]
        while pq:
            d, node = heapq.heappop(pq)
            if node == b:
                break
            if d > dist.get(node, INFEASIBLE):
                continue
            for nb, length, _eid in self.adj.get(node, []):
                nd = d + length
                if nd < dist.get(nb, INFEASIBLE):
                    dist[nb] = nd
                    prev[nb] = node
                    heapq.heappush(pq, (nd, nb))
        if b not in dist:
            return None
        route = [b]
        while route[-1] != a:
            route.append(prev[route[-1]])
        route.reverse()
        return route

    def route_length_m(self, route: list[str]) -> float:
        total = 0.0
        for a, b in pairwise(route):
            leg = next(
                (length for (nb, length, _e) in self.adj.get(a, []) if nb == b), None
            )
            if leg is None:  # arista cortada tras resolver: ruta ya no viva
                return INFEASIBLE
            total += leg
        return total

    def cell_of(self, x: float, z: float) -> str:
        cx = int((x - self.origin[0]) // self.cell_size)
        cz = int((z - self.origin[1]) // self.cell_size)
        return f"cell_{cx}_{cz}"


def _unit_waypoint(unit: Unit, graph: RoadGraph) -> str | None:
    return graph.nearest_waypoint(unit.x, unit.z)


def _task_waypoint(state: WorldState, task: Task, graph: RoadGraph) -> str | None:
    """Waypoint objetivo: el del POI si la tarea apunta a un POI; si apunta a una
    celda, el waypoint más cercano al centro de esa celda."""
    if task.target_poi is not None:
        poi: POI | None = state.pois.get(task.target_poi)
        return poi.waypoint_id if poi is not None else None
    if task.target_cell is not None:
        cell: Cell | None = state.cells.get(task.target_cell)
        if cell is None:
            return None
        cx = graph.origin[0] + (cell.cx + 0.5) * graph.cell_size
        cz = graph.origin[1] + (cell.cz + 0.5) * graph.cell_size
        return graph.nearest_waypoint(cx, cz)
    return None


def _active_units(state: WorldState) -> list[Unit]:
    return [u for u in state.units.values() if u.status != "unavailable"]


def _open_tasks(state: WorldState) -> list[Task]:
    return [t for t in state.tasks.values() if not t.done]


def _is_windward(state: WorldState, task: Task) -> bool:
    """Celda a barlovento: el viento sopla hacia ella desde el frente. Aproximación
    barata — cualquier celda `burning`/`at_risk` cuenta como frente relevante para
    `containment`. Sin celda objetivo, no aplica."""
    if task.target_cell is None:
        return False
    cell = state.cells.get(task.target_cell)
    return cell is not None and cell.state in ("burning", "at_risk")


def _weighted_cost(base: float, state: WorldState, task: Task, policy: Policy) -> float:
    """Aplica los pesos del catálogo que abaratan esta tarea. Peso desconocido se
    ignora aquí (lo caza `make check` contra los prompts, no el solver en runtime)."""
    cost = base
    civs = [c for c in state.civilians.values() if c.poi_id == task.target_poi]
    poi = state.pois.get(task.target_poi) if task.target_poi else None

    # Cada predicado dice si ese peso abarata esta tarea. `response_time` es el único
    # que penaliza en vez de abaratar, así que va aparte.
    applies = {
        "life_safety": any(c.state == "exposed" and c.count > 0 for c in civs),
        "immobile_first": any(c.immobile > 0 for c in civs),
        "structure_protection": poi is not None
        and poi.kind in ("village", "hospital", "shelter"),
        "containment": task.kind == "extinguish" and _is_windward(state, task),
    }

    for name, value in policy.weights.items():
        if value <= 0:
            continue
        if name == "response_time":
            cost *= 1.0 + value  # penaliza ETA alta, proporcional al peso
        elif applies.get(name):
            # el peso modula cuánto abarata: value alto = descuento más fuerte
            cost *= 1.0 - (1.0 - WEIGHT_DISCOUNT) * min(value, 1.0)
    return cost


def cost_matrix(
    state: WorldState,
    policy: Policy,
    graph: RoadGraph,
    vetoes: set[tuple[str, str]] | None = None,
) -> tuple[list[list[float]], list[Unit], list[Task], list[list[list[str]]]]:
    """Filas = unidades activas, columnas = tareas abiertas. Puro, inspeccionable.

    Devuelve además las rutas resueltas por par (para no recalcular en `solve` ni en
    `apply_hard_constraints`). Ruta vacía = par ya infactible.

    `vetoes` son pares (unit_id, task_id) que un `veto_assignment` humano ha puesto a
    coste infinito: el mismo tratamiento que una capacidad que no cuadra."""
    units = _active_units(state)
    tasks = _open_tasks(state)
    vetoes = vetoes or set()
    matrix: list[list[float]] = []
    routes: list[list[list[str]]] = []

    for unit in units:
        row: list[float] = []
        row_routes: list[list[str]] = []
        u_wp = _unit_waypoint(unit, graph)
        for task in tasks:
            if (unit.id, task.id) in vetoes:
                row.append(INFEASIBLE)
                row_routes.append([])
                continue
            if task.required_capability not in unit.capabilities:
                row.append(INFEASIBLE)
                row_routes.append([])
                continue
            t_wp = _task_waypoint(state, task, graph)
            route = graph.shortest_path(u_wp, t_wp) if (u_wp and t_wp) else None
            if not route:
                row.append(INFEASIBLE)
                row_routes.append([])
                continue
            base = graph.route_length_m(route)
            row.append(
                _weighted_cost(base, state, task, policy)
                if base != INFEASIBLE
                else INFEASIBLE
            )
            row_routes.append(route)
        matrix.append(row)
        routes.append(row_routes)
    return matrix, units, tasks, routes


def apply_hard_constraints(
    matrix: list[list[float]],
    state: WorldState,
    policy: Policy,
    graph: RoadGraph,
    units: list[Unit],
    tasks: list[Task],
    routes: list[list[list[str]]],
) -> list[Violation]:
    """Pone INFEASIBLE donde toca, in situ. Devuelve las violaciones de restricciones
    que no reconoce: `Violation(verifier=UNKNOWN_CONSTRAINT, severity="soft")`.

    Una restricción desconocida NO se ignora en silencio — es el bug más caro."""
    violations: list[Violation] = []

    for expr in policy.hard_constraints:
        if not is_known_constraint(expr):
            violations.append(
                Violation(
                    verifier=UNKNOWN_CONSTRAINT,
                    severity="soft",
                    message=f"restricción desconocida o aridad incorrecta: {expr!r}",
                    involved=[expr],
                )
            )
            continue

        name, args = parse_constraint(expr)

        if name == "no_unit_into_burning_cell":
            for i, _u in enumerate(units):
                for j, _t in enumerate(tasks):
                    route = routes[i][j]
                    if route and _route_crosses_burning(route, state, graph):
                        matrix[i][j] = INFEASIBLE

        elif name == "no_civilian_route_through":
            (wp_id,) = args
            for i, _u in enumerate(units):
                for j, task in enumerate(tasks):
                    if task.kind == "evacuate" and wp_id in routes[i][j]:
                        matrix[i][j] = INFEASIBLE

        # `hospital_min_coverage:n` y `reserve_capability:cap:n` son restricciones de
        # cardinalidad global (no de par); las verifica `verifiers.py` en H3 sobre el
        # plan ya construido. Aquí se reconocen para no marcarlas como desconocidas.

    return violations


def _route_crosses_burning(route: list[str], state: WorldState, graph: RoadGraph) -> bool:
    for wid in route:
        if wid not in graph.coords:
            continue
        wx, wz = graph.coords[wid]
        cell = state.cells.get(graph.cell_of(wx, wz))
        if cell is not None and cell.state == "burning":
            return True
    return False


ASSUMED_WEIGHT = 2.0
"""Un `assumed_default` es una suposición pre-rota: el sistema la puso porque no hubo
tiempo de preguntar, así que pesa el doble que una arista abierta y es la primera que
hay que reevaluar cuando llega información nueva (regla 4)."""


def _assumed_facts(state: WorldState) -> list[Assumption]:
    """Una suposición por cada clave cuyo hecho VIGENTE sea `assumed_default`. Si después
    entró uno observado, ya la falsó y no hay nada que vigilar."""
    latest: dict[str, Fact] = {}
    for f in state.facts:
        latest[f.key] = f
    return [
        Assumption(key=f.key, expected=f.value, weight=ASSUMED_WEIGHT)
        for f in latest.values()
        if f.kind == "assumed_default"
    ]


def build_context(
    state: WorldState, assignments: list[Assignment], graph: RoadGraph
) -> PlanContext:
    """Construye `PlanContext.assumptions`: lo que este plan da por cierto. Es lo que
    vigila `divergence.py`, así que si aquí falta una suposición, el replan no salta.

    Suponemos que cada arista de cada ruta asignada sigue abierta, y que el viento no
    cambia de forma que invalide el coste."""
    assumptions: list[Assumption] = []
    seen: set[str] = set()
    for a in assignments:
        for u, v in pairwise(a.route):
            eid = next((e for (nb, _l, e) in graph.adj.get(u, []) if nb == v), None)
            if eid is None or eid in seen:
                continue
            seen.add(eid)
            assumptions.append(
                Assumption(key=road_open_key(eid), expected=True, weight=1.0)
            )
    assumptions.append(
        Assumption(key="wind:bearing_deg", expected=state.wind.bearing_deg, weight=0.5)
    )
    assumptions.extend(_assumed_facts(state))
    return PlanContext(assumptions=assumptions, world_seq=state.seq)


def solve(
    state: WorldState,
    policy: Policy,
    graph: RoadGraph | None = None,
    vetoes: set[tuple[str, str]] | None = None,
) -> Plan:
    """El plan óptimo bajo esos pesos. Lo que no se pudo cubrir sale en
    `unassigned_tasks`, y se muestra: un hueco visible es información.

    Firma de contrato (`solve(state, policy) -> Plan`). Las violaciones blandas por
    restricción desconocida las devuelve `solve_with_violations`, que es lo que usa
    `loop.py` para publicarlas: aquí se descartan solo porque la firma no tiene
    dónde ponerlas."""
    plan, _violations = solve_with_violations(state, policy, graph, vetoes)
    return plan


def solve_with_violations(
    state: WorldState,
    policy: Policy,
    graph: RoadGraph | None = None,
    vetoes: set[tuple[str, str]] | None = None,
) -> tuple[Plan, list[Violation]]:
    """`solve` más las `Violation(verifier=UNKNOWN_CONSTRAINT, severity="soft")` que
    levantó `apply_hard_constraints`. Una restricción desconocida no se ignora: sale
    en el dashboard y vuelve al planner como crítica.

    `graph` lo inyecta `loop.py` desde el escenario; si falta, se cae a un grafo vacío
    y la asignación degrada a lo que permita el estado (la demo nunca se queda sin
    plan). `vetoes` son los pares vetados por un humano (coste infinito)."""
    live_graph = (graph or RoadGraph({}, {})).with_cuts(state)

    matrix, units, tasks, routes = cost_matrix(state, policy, live_graph, vetoes)
    violations = apply_hard_constraints(
        matrix, state, policy, live_graph, units, tasks, routes
    )

    assignments: list[Assignment] = []
    for i, j in _match(matrix):
        route = routes[i][j]
        length = live_graph.route_length_m(route)
        eta = length / UNIT_SPEED_MPS if math.isfinite(length) else 0.0
        assignments.append(
            Assignment(
                unit_id=units[i].id,
                task_id=tasks[j].id,
                route=route,
                eta_s=eta,
                cost=matrix[i][j],
            )
        )
    assigned_tasks = {a.task_id for a in assignments}
    unassigned = [t.id for t in tasks if t.id not in assigned_tasks]

    context = build_context(state, assignments, live_graph)
    plan = Plan(
        id=f"plan_{state.run_id}_{state.seq}",
        run_id=state.run_id,
        created_t=state.t_sim,
        policy=policy,
        assignments=assignments,
        unassigned_tasks=unassigned,
        context=context,
    )
    return plan, violations


def _match(matrix: list[list[float]]) -> list[tuple[int, int]]:
    """`linear_sum_assignment` sobre la matriz; devuelve los pares (fila, columna)
    factibles. Los de coste `inf` (capability, ruta cortada, restricción dura) se
    descartan: scipy los empareja pero no valen, y el hueco se muestra."""
    if not matrix or not matrix[0]:
        return []

    import numpy as np
    from scipy.optimize import linear_sum_assignment

    # scipy no traga `inf`: se sustituye por un coste-penalización grande pero finito,
    # y luego se descarta cualquier par que originalmente fuera infactible.
    arr = np.array(matrix, dtype=float)
    finite = arr[np.isfinite(arr)]
    big = (float(finite.max()) + 1.0) * (arr.size + 1.0) if finite.size else 1.0
    solvable = np.where(np.isfinite(arr), arr, big)

    rows, cols = linear_sum_assignment(solvable)
    return [
        (i, j)
        for i, j in zip(rows.tolist(), cols.tolist())
        if math.isfinite(matrix[i][j])
    ]
