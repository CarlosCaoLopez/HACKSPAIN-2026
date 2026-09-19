"""Ciclo de vida de las tareas: qué nace, qué cambia y qué se cierra. P1.

`belief` es un fold y no decide nada; aquí vive la política de creación. `sync` es
pura: mira el estado (ya con el evento aplicado) y devuelve las tareas que hay que
publicar como `task.changed`. El loop las publica y las pliega con `belief.apply`,
así el journal y el dashboard ven cada alta y cada cierre.

Reglas, todas deterministas y sin LLM:
- `extinguish`: una por FRENTE, no por celda. Un frente es una componente conexa
  de celdas `burning` con DILATACIÓN: dos celdas a ≤ `MERGE_RADIUS` (2) de distancia
  son el mismo frente. Con una tarea por celda salían más de ochenta en cinco
  minutos; con 8 vecinas estrictas, tras la avería de un camión el fuego se rompía
  en 42 frentes (9 vivos a la vez) y 56 planes en 100 s. El id
  (`task_front_<cx>_<cz>`, de la celda con que nació) sobrevive mientras el frente
  crece o se desplaza; cuando dos frentes se juntan queda la tarea más grave y la
  otra se cierra; cuando un frente se apaga entero, se cierra.
  Tres filtros más para que las tareas sean «los frentes que importan»:
  (b) una componente pequeña (< `MIN_FRONT_CELLS`) que no tiene ningún POI con
  gente a < `HIGH_DISTANCE_M` no crea tarea (cuenta como chispa del frente más
  cercano); si no hay ningún frente que merezca tarea, el más amenazante la tiene
  igual (la ignición es una sola celda y el camión tiene que salir ya).
  (c) como mucho `MAX_FRONTS` frentes con tarea, elegidos por amenaza; los demás no
  tienen tarea hasta que uno se cierre o ellos suban.
  (d) la gravedad es por RANGO de amenaza: la amenaza de un frente es el tiempo
  estimado (`fire_eta_s`, la ley del autómata con viento) hasta el POI con gente
  que antes alcanza; el frente que antes llega es `critical`, el segundo `high`, el
  resto `medium`; un frente a más de `FAR_HORIZON_S` de cualquiera es `medium` como
  mucho, y uno que ningún camión puede sofocar desde un waypoint baja un escalón
  (el camión solo puede esperarlo). Con bandas absolutas todos los frentes eran
  `critical` a la vez (con 270 y 300 los dos pueblos quedan a sotavento).
  `target_cell` es la celda del frente que un camión puede sofocar desde la
  carretera (`suppress_reach_m`) más cercana al POI amenazado, o si no hay ninguna
  a tiro, la que antes llegará a él (el camión la espera en la carretera como
  cortafuegos).
- `evacuate`: una por POI de tipo `village` con civiles `exposed`/`warned` mientras
  haya fuego. Severidad por distancia al frente y por sotavento. Se cierra cuando
  una unidad llega a su waypoint o cuando todos sus grupos están `safe`.
- `rescue`: una por POI con un hecho `poi:<id>:immobile > 0` de `kind` `observed` o
  `inferred` (un `assumed_default` no funda una tarea: regla 4). Crítica. Se cierra
  cuando una unidad llega a su waypoint.
"""

from __future__ import annotations

import logging
import math

from contracts.calls import Fact
from contracts.events import Event, EventType, UnitArrived
from contracts.world import POI, Cell, Task, TaskSeverity, Wind, WorldState
from core.solver import RoadGraph, attack_waypoint, attackable_from, fire_eta_s

log = logging.getLogger("core.tasks")

EVAC_STATES: frozenset[str] = frozenset({"exposed", "warned"})
"""Civiles que todavía hay que sacar. `evacuating`/`safe` ya van; `trapped` es rescate."""

CRITICAL_DISTANCE_M = 60.0
"""A menos de esto de una celda en llamas, la evacuación (y el frente) es crítica."""

HIGH_DISTANCE_M = 120.0
"""A menos de esto de un POI con gente, una chispa suelta merece tarea aunque sea
de una celda."""

DOWNWIND_DISTANCE_M = 200.0
DOWNWIND_HALF_ANGLE_DEG = 45.0
"""A sotavento (el viento empuja el frente hacia el POI) la evacuación es crítica a
más distancia."""

MERGE_RADIUS = 2
"""Dos celdas en llamas a esta distancia (Chebyshev, en celdas) o menos son el mismo
frente: la dilatación que impide que un hueco de una celda parta un frente en dos."""

MIN_FRONT_CELLS = 3
"""Una componente más pequeña, lejos de la gente, no crea tarea."""

MAX_FRONTS = 4
"""Frentes con tarea a la vez, como mucho."""

FAR_HORIZON_S = 3600.0
"""Un frente que no llega a nadie en una hora es `medium` como mucho."""

STICKY_EXISTING = 0.9
STICKY_CRITICAL = 0.9
"""Histéresis del rango: un frente que ya tiene tarea compite con su amenaza al 90 %,
y el crítico vigente otro 90 %. Sin esto dos frentes parejos se intercambiaban la
gravedad en cada celda que prendía, y con ella los camiones."""

ARRIVAL_CLOSES: frozenset[str] = frozenset({"evacuate", "rescue"})
"""Tareas que se cierran al llegar una unidad al waypoint del POI."""

FRONT_PREFIX = "task_front_"

HEAD_BAND_S = 60.0
"""Una celda a tiro cuenta como cabeza del frente si llega al POI amenazado como
mucho un minuto después que la que antes llega (≈ una celda a favor del viento)."""

INTERCEPT_HORIZON_S = 300.0
"""Si el frente va a tocar carretera en menos de esto, el camión lo espera allí en
vez de rematar la cola que tenga a tiro."""

INTERCEPT_HYSTERESIS = 0.8
"""Un frente que nadie tiene a tiro cambia de celda de contacto (y con ella de
waypoint de espera) solo si la nueva llega a la carretera en menos del 80 % del
tiempo de la vigente. Sin esto, cada celda que prendía movía el contacto entre
`wp_sur_02` y `wp_pueblo_b` (ETAs de 900 y 1000 s) y los camiones iban y venían."""

NEIGHBOURS_8: tuple[tuple[int, int], ...] = tuple(
    (dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if (dx, dz) != (0, 0)
)
"""Ocho vecinas: el vecindario con el que el fuego se propaga en el sim."""

NEIGHBOURHOOD: tuple[tuple[int, int], ...] = tuple(
    (dx, dz)
    for dx in range(-MERGE_RADIUS, MERGE_RADIUS + 1)
    for dz in range(-MERGE_RADIUS, MERGE_RADIUS + 1)
    if (dx, dz) != (0, 0)
)
"""El vecindario dilatado (`MERGE_RADIUS`) con el que se forman los frentes: incluye
las ocho vecinas, así que dos celdas que se contagian son siempre el mismo frente."""


def front_task_id(cell_id: str) -> str:
    """`cell_18_7` → `task_front_18_7`: la celda con que nació el frente."""
    return FRONT_PREFIX + cell_id.removeprefix("cell_")


def evac_task_id(poi_id: str) -> str:
    return f"task_evac_{poi_id}"


def rescue_task_id(poi_id: str) -> str:
    return f"task_rescue_{poi_id}"


def sync(state: WorldState, graph: RoadGraph, ev: Event | None = None) -> list[Task]:
    """Las tareas que hay que crear, actualizar o cerrar dado el estado tras `ev`.
    Devuelve solo lo que cambia respecto a `state.tasks`; lista vacía = nada que
    publicar. Determinista: mismo estado, mismas tareas, en el mismo orden."""
    changes: list[Task] = []
    changes.extend(_extinguish(state, graph))
    changes.extend(_evacuate(state, graph))
    changes.extend(_rescue(state))
    if ev is not None and ev.type == EventType.WORLD_UNIT_ARRIVED:
        changes.extend(_arrivals(state, ev))
    return changes


# --- extinguish: frentes ----------------------------------------------------


def fronts(cells: dict[str, Cell]) -> list[list[Cell]]:
    """Componentes conexas (vecindario dilatado, `NEIGHBOURHOOD`) de las celdas
    `burning`, cada una ordenada por id y la lista ordenada por su primera celda.
    Puro."""
    burning = {(c.cx, c.cz): c for c in cells.values() if c.state == "burning"}
    seen: set[tuple[int, int]] = set()
    out: list[list[Cell]] = []
    for key in sorted(burning, key=lambda k: burning[k].id):
        if key in seen:
            continue
        seen.add(key)
        stack = [key]
        comp: list[Cell] = []
        while stack:
            cx, cz = stack.pop()
            comp.append(burning[(cx, cz)])
            for dx, dz in NEIGHBOURHOOD:
                nb = (cx + dx, cz + dz)
                if nb in burning and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        out.append(sorted(comp, key=lambda c: c.id))
    return out


class _Front:
    """Un frente evaluado, antes de decidir si tiene tarea y con qué gravedad."""

    __slots__ = ("attackable", "comp", "keep", "score", "target", "threat_s")

    def __init__(
        self,
        comp: list[Cell],
        keep: Task | None,
        threat_s: float,
        attackable: bool,
        target: str,
    ) -> None:
        self.comp = comp
        self.keep = keep
        self.threat_s = threat_s
        self.attackable = attackable
        self.target = target
        # Con lo que compite: la amenaza con la histéresis de lo que ya tiene tarea.
        score = threat_s * (STICKY_EXISTING if keep is not None else 1.0)
        if keep is not None and keep.severity == "critical":
            score *= STICKY_CRITICAL
        self.score = score

    @property
    def key(self) -> tuple[float, str]:
        return (self.score, self.comp[0].id)


def _extinguish(state: WorldState, graph: RoadGraph) -> list[Task]:
    comps = fronts(state.cells)
    members = [{c.id for c in comp} for comp in comps]
    previous = sorted(
        (t for t in state.tasks.values() if t.kind == "extinguish" and not t.done),
        key=lambda t: t.id,
    )

    # Cada tarea previa hereda la componente en la que cae su celda objetivo (o sus
    # vecinas: la celda pudo quemarse y el frente seguir al lado). Sin componente,
    # el frente se apagó y la tarea se cierra.
    heirs: dict[int, list[Task]] = {}
    out: list[Task] = []
    for task in previous:
        idx = _component_of(task.target_cell, members)
        if idx is None:
            out.append(task.model_copy(update={"done": True}))
        else:
            heirs.setdefault(idx, []).append(task)

    peopled = _peopled_pois(state)
    evaluated: list[_Front] = []
    for idx, comp in enumerate(comps):
        # Dos frentes que se juntan: se queda la tarea más grave (empate: la más
        # antigua) y la otra se cierra.
        claimants = sorted(
            heirs.get(idx, []), key=lambda t: (-_rank(t.severity), t.created_t, t.id)
        )
        keep = claimants[0] if claimants else None
        out.extend(t.model_copy(update={"done": True}) for t in claimants[1:])
        evaluated.append(_assess_front(comp, peopled, state.wind, graph, keep))

    # (b) Una chispa pequeña lejos de la gente no merece tarea propia; una que ya
    # la tiene (un frente que se está apagando) la conserva hasta cerrarse.
    # (c) Como mucho MAX_FRONTS con tarea, los más amenazantes. Si ninguno lo merece
    # por tamaño (la ignición es una celda), el más amenazante la tiene igual.
    candidates = [
        f
        for f in evaluated
        if f.keep is not None or _worth_a_task(f.comp, peopled, graph)
    ]
    limit = MAX_FRONTS
    if not candidates:
        candidates, limit = evaluated, 1
    candidates.sort(key=lambda f: f.key)
    chosen, dropped = candidates[:limit], candidates[limit:]
    out.extend(f.keep.model_copy(update={"done": True}) for f in dropped if f.keep)

    # (d) Gravedad por rango de amenaza entre los elegidos.
    for rank, front in enumerate(chosen):
        severity = _rank_severity(rank, front.threat_s, front.attackable)
        keep = front.keep
        if keep is None:
            out.append(
                Task(
                    id=_fresh_front_id(front.target, front.comp, state),
                    kind="extinguish",
                    target_cell=front.target,
                    required_capability="extinguish",
                    severity=severity,
                    created_t=state.t_sim,
                )
            )
        elif keep.severity != severity or keep.target_cell != front.target:
            out.append(
                keep.model_copy(
                    update={"severity": severity, "target_cell": front.target}
                )
            )
    return sorted(out, key=lambda t: t.id)


def _worth_a_task(comp: list[Cell], peopled: list[POI], graph: RoadGraph) -> bool:
    """(b) Un frente de `MIN_FRONT_CELLS` o más, o una chispa a < `HIGH_DISTANCE_M`
    de un POI con gente."""
    if len(comp) >= MIN_FRONT_CELLS:
        return True
    for cell in comp:
        x, z = graph.cell_center(cell)
        if any(math.hypot(p.x - x, p.z - z) < HIGH_DISTANCE_M for p in peopled):
            return True
    return False


def _rank_severity(rank: int, threat_s: float, attackable: bool) -> TaskSeverity:
    """(d) `critical` el que antes llega a la gente, `high` el segundo, `medium` el
    resto y cualquiera a más de `FAR_HORIZON_S`; sin waypoint a tiro, un escalón
    menos (el camión solo puede esperarlo)."""
    if threat_s > FAR_HORIZON_S:
        level = 1
    else:
        level = {0: 3, 1: 2}.get(rank, 1)
    if not attackable:
        level = max(level - 1, 0)
    return _LEVELS[level]


_LEVELS: tuple[TaskSeverity, ...] = ("low", "medium", "high", "critical")


def _component_of(cell_id: str | None, members: list[set[str]]) -> int | None:
    """La componente que contiene la celda; si ya no arde, la que más vecinas suyas
    contiene (empate: la primera). None si ninguna la toca."""
    if cell_id is None:
        return None
    for idx, ids in enumerate(members):
        if cell_id in ids:
            return idx
    try:
        cx, cz = (int(v) for v in cell_id.removeprefix("cell_").split("_"))
    except ValueError:
        return None
    around = {f"cell_{cx + dx}_{cz + dz}" for dx, dz in NEIGHBOURHOOD}
    best: int | None = None
    best_n = 0
    for idx, ids in enumerate(members):
        n = len(around & ids)
        if n > best_n:
            best, best_n = idx, n
    return best


def _fresh_front_id(target: str, comp: list[Cell], state: WorldState) -> str:
    """Id del frente nuevo: su celda objetivo, o la primera del frente cuyo id no se
    haya usado ya (una tarea cerrada por fusión no revive con otro frente)."""
    for cid in [target, *(c.id for c in comp)]:
        tid = front_task_id(cid)
        if tid not in state.tasks:
            return tid
    return f"{front_task_id(target)}_{int(state.t_sim)}"


def _peopled_pois(state: WorldState) -> list[POI]:
    """POIs con gente que todavía está allí: pueblos, hospital, refugio o un molino
    con un grupo de civiles no `safe`. Un POI vacío no hace crítico a nadie."""
    out: list[POI] = []
    for poi in sorted(state.pois.values(), key=lambda p: p.id):
        if any(
            g.poi_id == poi.id and g.count > 0 and g.state != "safe"
            for g in state.civilians.values()
        ):
            out.append(poi)
    return out


def _assess_front(
    comp: list[Cell],
    peopled: list[POI],
    wind: Wind,
    graph: RoadGraph,
    keep: Task | None,
) -> _Front:
    """Amenaza, atacabilidad y celda objetivo de un frente. La gravedad no se decide
    aquí: es por rango entre los frentes elegidos (`_rank_severity`).

    Amenaza: segundos (`fire_eta_s`) hasta el POI con gente que antes alcanza desde
    su celda más favorable; infinito si no hay gente en ningún sitio.

    Objetivo, por este orden:
    1. La CABEZA del frente (las celdas que antes llegan al POI amenazado, ±
       `HEAD_BAND_S`) si algún waypoint la tiene a tiro: se ataca desde la
       carretera lo que va hacia la gente, no lo que ya pasó.
    2. Si no, la celda con la que el frente va a tocar carretera antes, siempre que
       sea en menos de `INTERCEPT_HORIZON_S`: el camión la espera allí como
       cortafuegos (el solver traduce la celda al waypoint de intercepción).
       Rematar la cola a tiro mientras la cabeza se va hacia el pueblo dejaba a los
       camiones en `wp_sur_01` toda la demo.
    3. Si el fuego no va a tocar carretera pronto, lo que haya a tiro, la celda más
       cercana al POI; y si no hay nada a tiro, la de primer contacto igualmente.

    La celda objetivo vigente se conserva mientras siga valiendo desde el mismo
    waypoint y del mismo modo (a tiro / en espera): cambiarla cada vez que prende
    una celda era un `task.changed` y un re-solve por segundo sin que el camión se
    moviera."""
    current = keep.target_cell if keep is not None else None
    centers = {c.id: graph.cell_center(c) for c in comp}
    reach = {cid: attackable_from(x, z, graph) for cid, (x, z) in centers.items()}
    attackable = {cid for cid, wp in reach.items() if wp is not None}
    threatened, threat_s = _threatened_poi(centers.values(), peopled, wind, graph)

    def to_poi(cid: str) -> float:
        if threatened is None:
            return math.inf
        x, z = centers[cid]
        return fire_eta_s(x, z, threatened.x, threatened.z, wind, graph)

    def dist_poi(cid: str) -> float:
        if threatened is None:
            return 0.0
        x, z = centers[cid]
        return math.hypot(threatened.x - x, threatened.z - z)

    contact_cid, contact_eta = _first_contact(centers, wind, graph)
    head_eta = min(to_poi(cid) for cid in centers)
    head_attackable = {
        cid
        for cid in attackable
        if threatened is not None and to_poi(cid) <= head_eta + HEAD_BAND_S
    }
    if head_attackable:
        best = min(head_attackable, key=lambda c: (dist_poi(c), c))
    elif contact_cid is not None and (
        contact_eta <= INTERCEPT_HORIZON_S or not attackable
    ):
        best = contact_cid
    elif attackable:
        best = min(attackable, key=lambda c: (dist_poi(c), c))
    else:
        best = min(centers, key=lambda c: (dist_poi(c), c))

    if current in centers and current != best:
        same_mode = (reach[current] is None) == (reach[best] is None)
        wp_now = attack_waypoint(*centers[current], wind, graph)
        wp_best = attack_waypoint(*centers[best], wind, graph)
        if same_mode and wp_now == wp_best:
            best = current
        elif same_mode and reach[best] is None:
            # Dos celdas de contacto en espera: la nueva solo gana si toca carretera
            # claramente antes que la vigente.
            _, cur_eta = _first_contact({current: centers[current]}, wind, graph)
            if contact_eta >= INTERCEPT_HYSTERESIS * cur_eta:
                best = current
    return _Front(comp, keep, threat_s, bool(attackable), best)


def _first_contact(
    centers: dict[str, tuple[float, float]], wind: Wind, graph: RoadGraph
) -> tuple[str | None, float]:
    """(celda, segundos) con que el frente toca antes un waypoint: la celda y el
    waypoint que minimizan `fire_eta_s`, con el mismo desempate que
    `solver.intercept_waypoint` para que el solver mande al camión justo ahí."""
    best: tuple[float, str, str] | None = None
    for cid, (x, z) in centers.items():
        for wid, (wx, wz) in graph.coords.items():
            key = (fire_eta_s(x, z, wx, wz, wind, graph), wid, cid)
            if best is None or key < best:
                best = key
    if best is None:
        return None, math.inf
    return best[2], best[0]


def _threatened_poi(
    centers, peopled: list[POI], wind: Wind, graph: RoadGraph
) -> tuple[POI | None, float]:
    """(POI, segundos): el POI con gente al que el frente llega antes con este viento
    (`fire_eta_s` desde su celda más favorable). Con viento 270 el fuego de la
    cresta va a Pueblo A; a 300, a Pueblo B: es lo que hace que el cambio de viento
    cambie el objetivo y el rango. Sin gente, (None, inf)."""
    best: POI | None = None
    best_key: tuple[float, str] | None = None
    for poi in peopled:
        eta = min(fire_eta_s(x, z, poi.x, poi.z, wind, graph) for x, z in centers)
        key = (eta, poi.id)
        if best_key is None or key < best_key:
            best, best_key = poi, key
    return best, best_key[0] if best_key is not None else math.inf


# --- evacuate ---------------------------------------------------------------


def _evacuate(state: WorldState, graph: RoadGraph) -> list[Task]:
    out: list[Task] = []
    burning = [c for c in state.cells.values() if c.state == "burning"]
    for poi in sorted(state.pois.values(), key=lambda p: p.id):
        tid = evac_task_id(poi.id)
        existing = state.tasks.get(tid)
        groups = [g for g in state.civilians.values() if g.poi_id == poi.id]
        if existing is not None:
            if existing.done:
                continue
            if groups and all(g.state == "safe" for g in groups):
                out.append(existing.model_copy(update={"done": True}))
            elif burning:
                severity = _evac_severity(poi, burning, state.wind, graph)
                if _rank(severity) > _rank(existing.severity):
                    out.append(existing.model_copy(update={"severity": severity}))
            continue
        if poi.kind != "village" or not burning:
            continue
        if not any(g.state in EVAC_STATES and g.count > 0 for g in groups):
            continue
        out.append(
            Task(
                id=tid,
                kind="evacuate",
                target_poi=poi.id,
                required_capability="transport",
                severity=_evac_severity(poi, burning, state.wind, graph),
                created_t=state.t_sim,
            )
        )
    return out


def _evac_severity(
    poi: POI, burning: list[Cell], wind: Wind, graph: RoadGraph
) -> TaskSeverity:
    """`critical` si el frente está cerca, o a sotavento a distancia media; si no,
    `high`. Nunca menos: hay civiles expuestos y fuego."""
    for cell in burning:
        cx, cz = graph.cell_center(cell)
        d = math.hypot(poi.x - cx, poi.z - cz)
        if d <= CRITICAL_DISTANCE_M:
            return "critical"
        if d <= DOWNWIND_DISTANCE_M and _downwind(cx, cz, poi, wind):
            return "critical"
    return "high"


def _downwind(x: float, z: float, poi: POI, wind: Wind) -> bool:
    """¿El viento empuja el fuego que hay en (x, z) hacia el POI (±45°)?"""
    if wind.speed <= 0:
        return False
    push = (wind.bearing_deg + 180.0) % 360.0  # el viento EMPUJA hacia aquí
    bearing = math.degrees(math.atan2(poi.x - x, -(poi.z - z))) % 360.0  # 0 = norte
    return _angular_gap(bearing, push) <= DOWNWIND_HALF_ANGLE_DEG


def _angular_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _rank(severity: str) -> int:
    return _RANK.get(severity, 0)


# --- rescue -----------------------------------------------------------------


def _rescue(state: WorldState) -> list[Task]:
    out: list[Task] = []
    seen: set[str] = set()  # un POI, una tarea: immobile e injuries no la duplican
    for key, fact in sorted(_latest_facts(state).items()):
        seg = key.split(":")
        if len(seg) != 3 or seg[0] != "poi" or seg[2] not in ("immobile", "injuries"):
            continue
        if fact.kind == "assumed_default":
            continue  # regla 4: lo asumido no funda una tarea
        if _as_int(fact.value) <= 0:
            continue
        poi_id = seg[1]
        tid = rescue_task_id(poi_id)
        if tid in state.tasks or poi_id in seen:
            continue
        seen.add(poi_id)
        if poi_id not in state.pois:
            log.warning("rescate sobre un POI que no está en el estado: %s", poi_id)
        x, z = _rescue_point(state, poi_id)
        out.append(
            Task(
                id=tid,
                kind="rescue",
                target_poi=poi_id,
                target_x=x,
                target_z=z,
                required_capability="transport",
                severity="critical",
                created_t=state.t_sim,
            )
        )
    return out


def _rescue_point(state: WorldState, poi_id: str) -> tuple[float | None, float | None]:
    """El punto exacto que ha mandado un vecino por GPS para este POI, si lo hay.

    Un pin se ancla al POI más cercano dentro de `snap_m`, pero un perdido no está
    EN el pueblo: está donde dice el pin. Sin esto el rescate hereda el waypoint del
    POI y la ambulancia se planta en la plaza mientras el vecino sigue en el monte.

    Solo `observed`: la regla 4 vale también aquí. Un punto deducido no manda a nadie
    a ninguna parte; el del GPS lo ha mandado el propio vecino.
    """
    fact = _latest_facts(state).get(f"poi:{poi_id}:rescue_point")
    if fact is None or fact.kind != "observed":
        return None, None
    try:
        x, z = str(fact.value).split(",")
        return float(x), float(z)
    except (ValueError, AttributeError):
        log.warning("punto de rescate ilegible en %s: %r", poi_id, fact.value)
        return None, None


def _latest_facts(state: WorldState) -> dict[str, Fact]:
    latest: dict[str, Fact] = {}
    for f in state.facts:
        latest[f.key] = f
    return latest


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


# --- cierres por llegada ----------------------------------------------------


def _arrivals(state: WorldState, ev: Event) -> list[Task]:
    """`world.unit.arrived` en el waypoint de un POI cierra su evacuación o rescate.
    Si la unidad va asignada a otra tarea todavía abierta, no cierra esta; sin
    asignación (o con una ya cerrada), cualquier tarea de ese POI."""
    ua = UnitArrived.model_validate(ev.payload)
    unit = state.units.get(ua.unit_id)
    busy_with = unit.task_id if unit is not None else None
    if busy_with is not None:
        assigned = state.tasks.get(busy_with)
        if assigned is None or assigned.done:
            busy_with = None
    out: list[Task] = []
    for task in sorted(state.tasks.values(), key=lambda t: t.id):
        if task.done or task.kind not in ARRIVAL_CLOSES or task.target_poi is None:
            continue
        poi = state.pois.get(task.target_poi)
        if poi is None or poi.waypoint_id != ua.waypoint_id:
            continue
        if busy_with is not None and busy_with != task.id:
            continue
        out.append(task.model_copy(update={"done": True}))
    return out


__all__ = ["evac_task_id", "front_task_id", "fronts", "rescue_task_id", "sync"]
