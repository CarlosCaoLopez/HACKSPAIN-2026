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
from contracts.world import POI, Cell, Task, Unit, Wind, WorldState

INFEASIBLE = float("inf")

SELF_EVACUATE = "self_evacuate"
"""La capacidad que pide una evacuación, y que **ninguna unidad tiene**: es lo que
hace que toda su columna salga `INFEASIBLE` y no se le asigne nadie. Un pueblo avisado
sale andando; mandar una ambulancia a los que pueden caminar era gastar el único medio
capaz de sacar a los que no. Vive aquí, y no en `core.tasks`, porque quien la usa es el
cruce con `Unit.capabilities` (y porque `tasks` importa de `solver`, no al revés)."""

UNIT_SPEED_MPS = 4.0
"""Velocidad plana para pasar de metros de ruta a `eta_s`. No es física, es un
orden de magnitud estable para que `response_time` compare peras con peras. Va
igualada a `sim.runner.DEFAULT_SPEED_MPS` (4 m/s): el `eta_s` se le dice al vecino
por teléfono («llega en 33 s») y con 8 m/s la ambulancia tardaba el doble."""

WEIGHT_DISCOUNT = 0.35

SEVERITY_FACTOR: dict[str, float] = {
    "critical": 0.25,
    "high": 0.5,
    "medium": 1.0,
    "low": 1.5,
}
"""Multiplicador del coste por `Task.severity`. Sin él, dejar una tarea sin cubrir es
gratis para la asignación 1:1 y la ambulancia se quedaba en una evacuación de mobiles
con tres inmóviles esperando en el molino: un rescate crítico tiene que ganar a una
evacuación `high` aunque esté algo más lejos."""
"""Cada peso pertinente abarata la tarea multiplicando el coste por este factor.
Menor = el LLM manda más. El solver sigue siendo quien asigna."""


DEFAULT_REACH_M = 24.0
DEFAULT_BASE_SPREAD = 0.1
"""Defaults de `HazardSpec.suppress_reach_m` y `base_spread`, para un grafo construido
a mano (tests) sin escenario detrás."""

HOLD_PENALTY_M = 10_000.0
"""Coste de cambio: lo que le cuesta a una unidad retenida (`holds`, la permanencia
mínima que calcula `loop`) abandonar el waypoint al que va o en el que está. Finito
para que, si en su waypoint no queda nada que hacer, aún se la pueda mandar a otro
sitio; grande para que ningún ahorro de ruta lo compense. Sin esto un camión
recibía tres `goto` en diez segundos (sur_02 → sur_01 → sur_02) cada vez que una
celda nueva prendía y el emparejamiento óptimo cambiaba de lado."""

ALT_RADIUS = 2
"""Al buscar otra celda del mismo frente para el segundo camión se recorre el frente
con el mismo vecindario dilatado con el que `tasks` lo forma."""

ANCHOR_BIAS_M = 5_000.0
"""Histéresis del emparejamiento en un frente de dos columnas (primary + alt). El
óptimo global voltea qué camión va a cada columna cuando el fuego se extiende y los
costes cambian unos metros: el segundo camión recibía `goto` sur_01→sur_02→sur_01 y
rebotaba (`superseded` a mitad de camino). Se ancla cada columna a un camión estable
—el más cercano por carretera a su waypoint, desempate por `unit.id`— sumando este
margen al camión ancla en la columna del otro. Grande para que la deriva de coste no
lo voltee, pero por debajo de `HOLD_PENALTY_M` (una permanencia mínima manda más) y
sin tocar el reparto entre frentes distintos: solo sesga las dos columnas del mismo."""


class RoadGraph:
    """Copia de solo lectura del grafo de carreteras, propiedad de core.

    Reimplementa un Dijkstra mínimo; NO importa `sim/graph.py`. Las coordenadas de
    los waypoints salen del `Scenario` (las aristas de `WorldState.roads` no las
    traen), y los cortes vivos se refrescan con `with_cuts`.

    Lleva también los dos números del peligro que hacen falta para decidir desde dónde
    se ataca un frente: `reach_m` (hasta dónde sofoca un camión parado en un waypoint,
    `HazardSpec.suppress_reach_m`) y `base_spread` (celdas/min sin viento, con lo que
    se estima cuándo llega el fuego a un sitio). Van aquí y no en otro objeto porque
    `tasks.sync(state, graph)` y el solver ya reciben el grafo y no el escenario.
    """

    def __init__(
        self,
        coords: dict[str, tuple[float, float]],
        adj: dict[str, list[tuple[str, float, str]]],
        origin: tuple[float, float] = (0.0, 0.0),
        cell_size: int = 4,
        reach_m: float = DEFAULT_REACH_M,
        base_spread: float = DEFAULT_BASE_SPREAD,
    ) -> None:
        # adj[a] = [(b, length_m, edge_id), ...], simétrico
        self.coords = coords
        self.adj = adj
        self.origin = origin
        self.cell_size = cell_size
        self.reach_m = reach_m
        self.base_spread = base_spread

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
        return cls(
            coords,
            adj,
            scenario.origin,
            scenario.hazard.cell_size,
            reach_m=scenario.hazard.suppress_reach_m,
            base_spread=scenario.hazard.base_spread,
        )

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
        return RoadGraph(
            self.coords,
            adj,
            self.origin,
            self.cell_size,
            reach_m=self.reach_m,
            base_spread=self.base_spread,
        )

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

    def cell_center(self, cell: Cell) -> tuple[float, float]:
        return (
            self.origin[0] + (cell.cx + 0.5) * self.cell_size,
            self.origin[1] + (cell.cz + 0.5) * self.cell_size,
        )

    def waypoint_distance(self, wp_id: str, x: float, z: float) -> float:
        wx, wz = self.coords[wp_id]
        return math.hypot(wx - x, wz - z)


# --- desde dónde se ataca una celda ------------------------------------------


def _angular_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def bearing_deg(x: float, z: float, tx: float, tz: float) -> float:
    """Rumbo de (x, z) a (tx, tz), 0 = norte (-z), horario, como `Wind.bearing_deg`."""
    return math.degrees(math.atan2(tx - x, -(tz - z))) % 360.0


def fire_eta_s(
    x: float, z: float, tx: float, tz: float, wind: Wind, graph: RoadGraph
) -> float:
    """Segundos que tarda un frente en (x, z) en llegar a (tx, tz), con la misma
    ley que el autómata del sim: `base_spread + viento · max(0, cos(ángulo))`
    celdas/min. Es lo que dice hacia dónde va el fuego de verdad: a favor del viento
    un pueblo a 90 m está más amenazado que otro a 60 m en contra."""
    d = math.hypot(tx - x, tz - z)
    if d == 0.0:
        return 0.0
    push = (wind.bearing_deg + 180.0) % 360.0  # el viento EMPUJA hacia aquí
    gap = _angular_gap(bearing_deg(x, z, tx, tz), push)
    rate = graph.base_spread + wind.speed * max(0.0, math.cos(math.radians(gap)))
    if rate <= 0.0:
        return INFEASIBLE
    return d / (graph.cell_size * rate) * 60.0


def attackable_from(x: float, z: float, graph: RoadGraph) -> str | None:
    """El waypoint desde el que un camión parado sofoca esa celda (a ≤ `reach_m`),
    o None: el camión solo puede estar en waypoints, así que solo ataca el fuego
    que tiene la carretera a tiro."""
    wp = graph.nearest_waypoint(x, z)
    if wp is None or graph.waypoint_distance(wp, x, z) > graph.reach_m:
        return None
    return wp


def intercept_waypoint(x: float, z: float, wind: Wind, graph: RoadGraph) -> str | None:
    """El waypoint al que el fuego en (x, z) llega antes: donde un camión lo espera
    como cortafuegos. No es el más cercano: un waypoint a barlovento a 10 m no verá
    nunca el frente, y el que está a sotavento a 60 m sí."""
    best: str | None = None
    best_key: tuple[float, str] | None = None
    for wid, (wx, wz) in graph.coords.items():
        key = (fire_eta_s(x, z, wx, wz, wind, graph), wid)
        if best_key is None or key < best_key:
            best, best_key = wid, key
    return best


def attack_waypoint(x: float, z: float, wind: Wind, graph: RoadGraph) -> str | None:
    """Desde dónde se trabaja la celda (x, z): su waypoint a tiro si lo hay, y si no
    el de intercepción."""
    return attackable_from(x, z, graph) or intercept_waypoint(x, z, wind, graph)


def _unit_waypoint(unit: Unit, graph: RoadGraph) -> str | None:
    return graph.nearest_waypoint(unit.x, unit.z)


def _task_waypoint(state: WorldState, task: Task, graph: RoadGraph) -> str | None:
    """Waypoint objetivo: el del POI si la tarea apunta a un POI; si apunta a una
    celda, desde donde se ataca esa celda (`attack_waypoint`): a tiro si se puede, y
    si no donde el fuego va a llegar antes."""
    if task.target_poi is not None:
        poi: POI | None = state.pois.get(task.target_poi)
        return poi.waypoint_id if poi is not None else None
    if task.target_cell is not None:
        cell: Cell | None = state.cells.get(task.target_cell)
        if cell is None:
            return None
        cx, cz = graph.cell_center(cell)
        return attack_waypoint(cx, cz, state.wind, graph)
    return None


def _offroad_m(
    state: WorldState, cell_id: str | None, wp_id: str, graph: RoadGraph
) -> float:
    """Metros a pie del waypoint de ataque al centro de la celda objetivo. Un frente
    a 20 m de la carretera cuesta poco más que su ruta; uno a 100 m, mucho más: el
    camión no puede hacer nada con él salvo esperarlo."""
    if cell_id is None:
        return 0.0
    cell = state.cells.get(cell_id)
    if cell is None or wp_id not in graph.coords:
        return 0.0
    cx, cz = graph.cell_center(cell)
    return graph.waypoint_distance(wp_id, cx, cz)


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
    cost = base * SEVERITY_FACTOR.get(task.severity, 1.0)
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


def _columns(state: WorldState, units: list[Unit], tasks: list[Task]) -> list[Task]:
    """Las columnas de la matriz: las tareas abiertas y, si hay más unidades con
    `extinguish` activas que tareas de extinción, las tareas de extinción repetidas
    (de mayor a menor gravedad) hasta cubrirlas. El emparejamiento es 1:1 y con un
    solo frente conexo el segundo camión se quedaba sin asignación toda la demo."""
    ext_units = sum("extinguish" in u.capabilities for u in units)
    ext_tasks = sorted(
        (t for t in tasks if t.kind == "extinguish"),
        key=lambda t: (-_severity_rank(t.severity), t.created_t, t.id),
    )
    columns = list(tasks)
    if not ext_tasks:
        return columns
    i = 0
    while len(ext_tasks) + (i) < ext_units:
        columns.append(ext_tasks[i % len(ext_tasks)])
        i += 1
    return columns


_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _severity_rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity, 0)


def _alt_target(
    state: WorldState, task: Task, primary_wp: str | None, graph: RoadGraph
) -> tuple[str | None, str | None]:
    """(waypoint, celda) desde donde el SEGUNDO camión trabaja el mismo frente: la
    celda a tiro de otro waypoint más lejana de la celda objetivo del primero. Si
    todo el frente se ataca desde el mismo sitio, el waypoint donde el fuego va a
    tocar carretera (`intercept_waypoint`: el segundo camión hace de cortafuegos
    mientras el primero remata la cabeza), y si es el mismo, (primary_wp, None):
    los dos camiones juntos también sofocan el doble."""
    if task.target_cell is None or primary_wp is None:
        return primary_wp, None
    origin = state.cells.get(task.target_cell)
    if origin is None:
        return primary_wp, None
    burning = {(c.cx, c.cz): c for c in state.cells.values() if c.state == "burning"}
    seen = {(origin.cx, origin.cz)}
    stack = [(origin.cx, origin.cz)]
    ox, oz = graph.cell_center(origin)
    best: tuple[float, str, str] | None = None
    while stack:
        cx, cz = stack.pop()
        for dx in range(-ALT_RADIUS, ALT_RADIUS + 1):
            for dz in range(-ALT_RADIUS, ALT_RADIUS + 1):
                nb = (cx + dx, cz + dz)
                if nb in seen or nb not in burning:
                    continue
                seen.add(nb)
                stack.append(nb)
                cell = burning[nb]
                x, z = graph.cell_center(cell)
                wp = attackable_from(x, z, graph)
                if wp is None or wp == primary_wp:
                    continue
                key = (-math.hypot(x - ox, z - oz), wp, cell.id)
                if best is None or key < best:
                    best = key
    if best is None:
        contact = intercept_waypoint(ox, oz, state.wind, graph)
        if contact is not None and contact != primary_wp:
            return contact, None
        return primary_wp, None
    return best[1], best[2]


def cost_matrix(
    state: WorldState,
    policy: Policy,
    graph: RoadGraph,
    vetoes: set[tuple[str, str]] | None = None,
    holds: dict[str, str] | None = None,
) -> tuple[list[list[float]], list[Unit], list[Task], list[list[list[str]]]]:
    """Filas = unidades activas, columnas = tareas abiertas (una tarea de extinción
    puede aparecer en dos columnas: `_columns`). Puro, inspeccionable.

    Devuelve además las rutas resueltas por par (para no recalcular en `solve` ni en
    `apply_hard_constraints`). Ruta vacía = par ya infactible.

    `vetoes` son pares (unit_id, task_id) que un `veto_assignment` humano ha puesto a
    coste infinito: el mismo tratamiento que una capacidad que no cuadra. `holds` es
    unidad → waypoint que no debe abandonar todavía (permanencia mínima): cualquier
    columna que se trabaje desde otro waypoint le cuesta `HOLD_PENALTY_M` más."""
    units = _active_units(state)
    tasks = _columns(state, units, _open_tasks(state))
    vetoes = vetoes or set()
    holds = holds or {}
    matrix: list[list[float]] = []
    routes: list[list[list[str]]] = []

    # Waypoint de ataque y metros a pie por columna, una vez y no por par. La
    # segunda columna de un frente se trabaja desde otro waypoint si lo hay.
    col_wp: list[str | None] = []
    offroad: list[float] = []
    seen_tasks: dict[str, int] = {}
    # Frentes con dos columnas: (col primary, col alt). Se anclan al final para que el
    # emparejamiento no voltee qué camión toma cada una (`ANCHOR_BIAS_M`).
    front_pairs: list[tuple[int, int]] = []
    for j, task in enumerate(tasks):
        wp = _task_waypoint(state, task, graph)
        cell_id = task.target_cell
        if task.id in seen_tasks:
            wp, alt_cell = _alt_target(state, task, wp, graph)
            cell_id = alt_cell or cell_id
            primary_j = seen_tasks[task.id]
            if wp is not None and wp != col_wp[primary_j]:
                front_pairs.append((primary_j, j))
        else:
            seen_tasks[task.id] = j
        col_wp.append(wp)
        offroad.append(_offroad_m(state, cell_id, wp, graph) if wp else 0.0)

    for unit in units:
        row: list[float] = []
        row_routes: list[list[str]] = []
        u_wp = _unit_waypoint(unit, graph)
        held_at = holds.get(unit.id)
        for j, task in enumerate(tasks):
            if (unit.id, task.id) in vetoes:
                row.append(INFEASIBLE)
                row_routes.append([])
                continue
            if task.required_capability not in unit.capabilities:
                row.append(INFEASIBLE)
                row_routes.append([])
                continue
            t_wp = col_wp[j]
            route = graph.shortest_path(u_wp, t_wp) if (u_wp and t_wp) else None
            if not route:
                row.append(INFEASIBLE)
                row_routes.append([])
                continue
            base = graph.route_length_m(route)
            if base == INFEASIBLE:
                row.append(INFEASIBLE)
            else:
                cost = _weighted_cost(base + offroad[j], state, task, policy)
                if held_at is not None and t_wp != held_at:
                    cost += HOLD_PENALTY_M
                row.append(cost)
            row_routes.append(route)
        matrix.append(row)
        routes.append(row_routes)

    _anchor_front_pairs(matrix, routes, units, graph, front_pairs)
    return matrix, units, tasks, routes


def _anchor_front_pairs(
    matrix: list[list[float]],
    routes: list[list[list[str]]],
    units: list[Unit],
    graph: RoadGraph,
    front_pairs: list[tuple[int, int]],
) -> None:
    """Histéresis por frente de dos columnas, in situ. A cada columna se le fija un
    camión ancla —el más cercano por carretera a su waypoint, desempate por `unit.id`—
    y se le suma `ANCHOR_BIAS_M` al ancla de una columna en la columna de la otra, para
    que el óptimo global no los intercambie cuando la deriva de coste es de unos metros.
    Solo mira los camiones factibles para AMBAS columnas: el que solo llega a una no
    entra en la disputa."""
    for primary_j, alt_k in front_pairs:
        eligible = [
            i
            for i in range(len(units))
            if math.isfinite(matrix[i][primary_j]) and math.isfinite(matrix[i][alt_k])
        ]
        if len(eligible) < 2:
            continue
        anchor_p = min(
            eligible,
            key=lambda i: (graph.route_length_m(routes[i][primary_j]), units[i].id),
        )
        anchor_a = min(
            (i for i in eligible if i != anchor_p),
            key=lambda i: (graph.route_length_m(routes[i][alt_k]), units[i].id),
        )
        matrix[anchor_p][alt_k] += ANCHOR_BIAS_M
        matrix[anchor_a][primary_j] += ANCHOR_BIAS_M


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
                for j, task in enumerate(tasks):
                    route = routes[i][j]
                    if route and _route_crosses_burning(
                        route, state, graph, skip_last=task.kind == "extinguish"
                    ):
                        matrix[i][j] = INFEASIBLE

        elif name == "no_civilian_route_through":
            (wp_id,) = args
            for i, _u in enumerate(units):
                for j, task in enumerate(tasks):
                    # `[1:]`: el primer waypoint es donde YA está la unidad; salir de
                    # él no es "pasar por" él.
                    if task.kind == "evacuate" and wp_id in routes[i][j][1:]:
                        matrix[i][j] = INFEASIBLE

        # `hospital_min_coverage:n` y `reserve_capability:cap:n` son restricciones de
        # cardinalidad global (no de par); las verifica `verifiers.py` en H3 sobre el
        # plan ya construido. Aquí se reconocen para no marcarlas como desconocidas.

    return violations


def _route_crosses_burning(
    route: list[str], state: WorldState, graph: RoadGraph, skip_last: bool = False
) -> bool:
    """¿Entra la ruta en una celda en llamas? El primer waypoint no cuenta: es donde la
    unidad ya está, y si el fuego le llega, salir de ahí es justo lo que hay que poder
    hacer (con el origen contando, un camión alcanzado por el frente no podía ir a
    ningún sitio y el plan salía vacío). Con `skip_last`, tampoco el destino: una
    tarea `extinguish` apunta por definición a la celda que arde."""
    body = route[1:-1] if skip_last else route[1:]
    for wid in body:
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
    holds: dict[str, str] | None = None,
) -> Plan:
    """El plan óptimo bajo esos pesos. Lo que no se pudo cubrir sale en
    `unassigned_tasks`, y se muestra: un hueco visible es información.

    Firma de contrato (`solve(state, policy) -> Plan`). Las violaciones blandas por
    restricción desconocida las devuelve `solve_with_violations`, que es lo que usa
    `loop.py` para publicarlas: aquí se descartan solo porque la firma no tiene
    dónde ponerlas."""
    plan, _violations = solve_with_violations(state, policy, graph, vetoes, holds)
    return plan


def solve_with_violations(
    state: WorldState,
    policy: Policy,
    graph: RoadGraph | None = None,
    vetoes: set[tuple[str, str]] | None = None,
    holds: dict[str, str] | None = None,
) -> tuple[Plan, list[Violation]]:
    """`solve` más las `Violation(verifier=UNKNOWN_CONSTRAINT, severity="soft")` que
    levantó `apply_hard_constraints`. Una restricción desconocida no se ignora: sale
    en el dashboard y vuelve al planner como crítica.

    `graph` lo inyecta `loop.py` desde el escenario; si falta, se cae a un grafo vacío
    y la asignación degrada a lo que permita el estado (la demo nunca se queda sin
    plan). `vetoes` son los pares vetados por un humano (coste infinito); `holds`,
    las unidades con permanencia mínima en su waypoint (`HOLD_PENALTY_M`).

    Dos asignaciones pueden llevar el mismo `task_id` (dos camiones en un frente):
    `verifiers.no_double_booking` solo mira unidades repetidas."""
    live_graph = (graph or RoadGraph({}, {})).with_cuts(state)

    matrix, units, tasks, routes = cost_matrix(state, policy, live_graph, vetoes, holds)
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
    # Una evacuación no pide vehículo: no está «sin cubrir», está hecha a pie. Contarla
    # como hueco pintaba dos tareas en rojo todo el run y una luz de «esperando
    # ambulancia» con las ambulancias paradas en el hospital, que es justo la clase de
    # pantalla que miente.
    unassigned = list(
        dict.fromkeys(
            t.id
            for t in tasks
            if t.id not in assigned_tasks and t.required_capability != SELF_EVACUATE
        )
    )

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

    # Desempate reproducible: una perturbación minúscula y estable por celda (i, j) para
    # que un empate exacto resuelva siempre igual entre replans. Muy por debajo de
    # cualquier coste real y de `ANCHOR_BIAS_M`; solo ordena empates, no cambia óptimos.
    ncols = solvable.shape[1]
    tiebreak = np.fromfunction(
        lambda i, j: (i * ncols + j) * 1e-9, solvable.shape, dtype=float
    )
    solvable = solvable + tiebreak

    rows, cols = linear_sum_assignment(solvable)
    return [
        (i, j)
        for i, j in zip(rows.tolist(), cols.tolist())
        if math.isfinite(matrix[i][j])
    ]
