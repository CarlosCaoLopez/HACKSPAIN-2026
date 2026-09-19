"""Lo que el core le pide a voice: la intención de una llamada y las señales en vivo.

El core decide que hay que llamar, a quién y con qué variables de guion; voice elige
plataforma, número saliente y guion. Aquí solo hay funciones puras que traducen un
plan a esas intenciones: nombres humanos de rutas y unidades, y el `CallRequest`.
"""

from __future__ import annotations

import math
import re
from itertools import pairwise

from contracts.calls import CallRequest, Urgency
from contracts.factkeys import road_bare
from contracts.plan import Assignment, Plan
from contracts.world import POI, RoadEdge, Task, Unit, WorldState
from core.solver import RoadGraph, fire_eta_s

DEADLINE_MARGIN_MIN = 5
"""Minutos que se suman a la ETA de la unidad para el plazo que se dicta al vecino."""

HAZARD_NAMES = {
    "wildfire": "incendio forestal",
    "flood": "inundación",
    "blackout": "apagón",
}

UNIT_NAMES = {
    "fire_truck": "camión",
    "ambulance": "ambulancia",
    "drone": "dron",
    "crew": "brigada",
}

UNIT_PLURALS = {
    "camión": "camiones",  # pierde la tilde: por regla salía «camiónes» en voz alta
    "ambulancia": "ambulancias",
    "dron": "drones",
    "brigada": "brigadas",
}

_SEVERITY_URGENCY: dict[str, Urgency] = {
    "low": "low",
    "medium": "medium",
    "high": "medium",
    "critical": "critical",
}


def hazard_name(kind: str) -> str:
    return HAZARD_NAMES.get(kind, kind)


def unit_name(unit: Unit) -> str:
    """`unit_truck2` → "camión 2", `unit_ambulance` → "ambulancia"."""
    base = UNIT_NAMES.get(unit.kind, unit.kind)
    m = re.search(r"(\d+)$", unit.id)
    return f"{base} {m.group(1)}" if m else base


def route_name(
    route: list[str],
    roads: dict[str, RoadEdge] | None = None,
    aliases: dict[str, str] | None = None,
) -> str:
    """El nombre que se dice por teléfono. Si la ruta pasa por el desvío norte o sur
    (waypoints con `nor`/`sur`), "pista norte"/"pista sur": la pista por la que se
    recorren más waypoints, sin contar el de salida (la unidad puede estar parada en
    una pista cortada y salir por la otra; al molino se llega por la norte y Pueblo A
    aunque su waypoint sea `wp_sur_02`). Empate: el tramo más cercano al destino. Si
    no, cada arista de la ruta por su nombre de calle (`Scenario.road_aliases`,
    alias → id; se usa el primero que apunte a la arista: «pista de pueblo b») y, sin
    alias, su id."""
    hops = route[1:] if len(route) > 1 else route
    sur = sum("sur" in wp.lower() for wp in hops)
    nor = sum("nor" in wp.lower() for wp in hops)
    if sur or nor:
        if sur != nor:
            return "pista sur" if sur > nor else "pista norte"
        for wp in reversed(hops):
            low = wp.lower()
            if "sur" in low:
                return "pista sur"
            if "nor" in low:
                return "pista norte"
    if len(route) < 2:
        return route[0] if route else ""
    names = _alias_by_edge(aliases)
    return ", ".join(_edge_name(a, b, roads, names) for a, b in pairwise(route))


def _alias_by_edge(aliases: dict[str, str] | None) -> dict[str, str]:
    """Mapa inverso arista → PRIMER alias del escenario (el YAML lista primero el
    nombre más natural)."""
    out: dict[str, str] = {}
    for alias, edge_id in (aliases or {}).items():
        out.setdefault(edge_id, alias)
        out.setdefault(road_bare(edge_id), alias)
    return out


def _edge_name(
    a: str, b: str, roads: dict[str, RoadEdge] | None, names: dict[str, str]
) -> str:
    for e in (roads or {}).values():
        if {e.a, e.b} == {a, b}:
            return names.get(e.id) or names.get(road_bare(e.id)) or road_bare(e.id)
    bare = f"{a}-{b}"
    return names.get(bare) or names.get(f"{b}-{a}") or bare


def urgency_of(task: Task) -> Urgency:
    return _SEVERITY_URGENCY.get(task.severity, "medium")


def _assignment_for(plan: Plan | None, task_id: str) -> Assignment | None:
    """La asignación del solver para esa tarea. Es lo que convierte una llamada de
    despacho en una petición concreta: sin esto se le decía al retén que no había
    ninguna unidad asignada mientras se le mandaba su propio camión."""
    if plan is None:
        return None
    return next((a for a in plan.assignments if a.task_id == task_id), None)


def _extinguish_assignments(
    state: WorldState | None, plan: Plan | None
) -> list[Assignment]:
    """Todo lo que el plan manda al fuego. Al retén no se le pide un camión: se le
    pide el conjunto que el solver ha puesto sobre los frentes."""
    if plan is None or state is None:
        return []
    return [
        a
        for a in plan.assignments
        if (t := state.tasks.get(a.task_id)) is not None and t.kind == "extinguish"
    ]


UNIT_STATUS_WORDS = {
    "working": "trabajando el fuego",
    "moving": "de camino",
    "idle": "disponible",
    "unavailable": "fuera de servicio",
}

DOWNWIND_HALF_ANGLE_DEG = 45.0
"""Igual que en `core.tasks`: el cono dentro del cual el viento empuja hacia el POI."""


def _plural(n: int, singular: str) -> str:
    plural = UNIT_PLURALS.get(singular, f"{singular}s")
    return f"{n} {singular}" if n == 1 else f"{n} {plural}"


def _count_kinds(kinds: list[str]) -> str:
    """`["camión","camión","ambulancia"]` → "1 ambulancia, 2 camiones". Contar por
    tipo es lo que hace legible una lista de medios dicha en voz alta."""
    cuenta: dict[str, int] = {}
    for k in kinds:
        cuenta[k] = cuenta.get(k, 0) + 1
    return ", ".join(_plural(n, k) for k, n in sorted(cuenta.items()))


def _minutes(eta_s: float) -> str:
    """Segundos del solver → los minutos que se dicen por teléfono. Nunca "cero
    minutos": una unidad que ya está allí llega "en un minuto"."""
    minutos = max(1, math.ceil(eta_s / 60.0))
    return "un minuto" if minutos == 1 else f"unos {minutos} minutos"


def resources_line(state: WorldState) -> str:
    """Qué medios hay y qué están haciendo, ahora mismo. Es lo que el operador puede
    prometer por teléfono sin inventarse nada: sale del `WorldState`, no del guion."""
    by_status: dict[str, list[str]] = {}
    for unit in sorted(state.units.values(), key=lambda u: u.id):
        word = UNIT_STATUS_WORDS.get(unit.status, unit.status)
        by_status.setdefault(word, []).append(UNIT_NAMES.get(unit.kind, unit.kind))
    partes = [f"{_count_kinds(kinds)} {word}" for word, kinds in by_status.items()]
    return "; ".join(partes) if partes else "sin medios registrados"


def requested_units_line(
    state: WorldState,
    assignments: list[Assignment],
    roads: dict[str, RoadEdge] | None = None,
    aliases: dict[str, str] | None = None,
) -> str:
    """Lo que se le PIDE al medio: qué unidades, por qué ruta y en cuánto.

    `resources_line` dice qué hay y qué está haciendo; esto dice qué se le está
    pidiendo. Sale del `Plan`, no del `WorldState`: es la decisión del solver dicha en
    voz alta, y es lo único que el medio puede confirmar o negar por teléfono."""
    pares = [
        (a, state.units[a.unit_id])
        for a in sorted(assignments, key=lambda a: a.unit_id)
        if a.unit_id in state.units
    ]
    if not pares:
        return "todavía no hay ninguna unidad asignada"
    detalle = "; ".join(
        f"{unit_name(u)} por {route_name(a.route, roads, aliases)}, {_minutes(a.eta_s)}"
        for a, u in pares
    )
    return (
        f"{_count_kinds([UNIT_NAMES.get(u.kind, u.kind) for _, u in pares])}: {detalle}"
    )


def committed_resources_line(
    state: WorldState,
    assignments: list[Assignment],
    refused: list[Unit],
    roads: dict[str, RoadEdge] | None = None,
    aliases: dict[str, str] | None = None,
) -> str:
    """Los medios que van DE VERDAD, y los que han dicho que no pueden.

    Es la diferencia entre `requested_units_line` (lo que se pidió) y esto (lo que
    salió). Existe porque la orden de evacuación se dicta DESPUÉS de las llamadas de
    despacho: al pueblo no se le promete un camión hasta que su dotación ha dicho
    «vamos». Un «no ha podido salir» se dice también: es el beat honesto."""
    partes: list[str] = []
    if assignments:
        partes.append(f"van {requested_units_line(state, assignments, roads, aliases)}")
    if refused:
        nombres = _count_kinds([UNIT_NAMES.get(u.kind, u.kind) for u in refused])
        verbo = "ha podido" if len(refused) == 1 else "han podido"
        partes.append(f"{nombres} no {verbo} salir")
    return "; ".join(partes) if partes else "todavía no hay ningún medio confirmado"


def _burning_area(n_cells: int, graph: RoadGraph | None) -> str:
    """Celdas ardiendo → superficie. El lado de la celda es del escenario
    (`hazard.cell_size`, 4 bloques en `wildfire_ridge`), así que la cifra no se
    inventa aquí."""
    if graph is None:
        return f"{n_cells} celdas"
    ha = n_cells * graph.cell_size**2 / 10_000.0
    if ha < 1:
        return "menos de una hectárea"
    return "una hectárea" if round(ha) == 1 else f"unas {round(ha)} hectáreas"


def coverage_line(state: WorldState, plan: Plan, graph: RoadGraph | None = None) -> str:
    """Lo que el plan NO cubre, dicho como se puede decir por teléfono.

    `plan.unassigned_tasks` no está vacío casi nunca —medido sobre cinco runs: el
    100 % de los planes, con medianas de 23 a 284 tareas sin cubrir— pero «284 tareas»
    no es una frase que se le pueda decir a nadie: una tarea de extinción es una celda
    del grid, no un foco. Se dice en superficie ardiendo y medios encima, y quien
    decide si falta gente es el solver (si dejó tareas de extinción sin asignar), no
    una heurística de aquí."""
    ardiendo = sum(1 for c in state.cells.values() if c.state == "burning")
    if not ardiendo:
        return "no hay superficie ardiendo ahora mismo"
    encima = [
        UNIT_NAMES.get(state.units[a.unit_id].kind, state.units[a.unit_id].kind)
        for a in plan.assignments
        if a.unit_id in state.units
        and (t := state.tasks.get(a.task_id)) is not None
        and t.kind == "extinguish"
    ]
    superficie = _burning_area(ardiendo, graph)
    sin_cubrir = any(
        (t := state.tasks.get(tid)) is not None and t.kind == "extinguish"
        for tid in plan.unassigned_tasks
    )
    if not encima:
        return f"hay {superficie} ardiendo y ningún medio encima"
    medios = _count_kinds(encima)
    if sin_cubrir:
        return f"hay {superficie} ardiendo y solo {medios} encima: no llegamos a todo el frente"
    return f"hay {superficie} ardiendo, cubierta con {medios}"


def nearest_fire(
    state: WorldState, poi: POI, graph: RoadGraph | None
) -> tuple[float, bool]:
    """(metros hasta el frente más cercano, si el viento lo empuja hacia el POI).
    `inf` si no hay fuego o no hay grafo con el que medir.

    Es la medida con la que se decide a quién se evacúa y a quién se avisa: en un
    valle con dos pueblos, los dos están «amenazados» casi desde el primer minuto, y
    lo que los distingue no es una etiqueta sino cuál tiene el fuego más cerca."""
    burning = [c for c in state.cells.values() if c.state == "burning"]
    if not burning or graph is None:
        return math.inf, False
    best = math.inf
    downwind = False
    push = (state.wind.bearing_deg + 180.0) % 360.0  # el viento EMPUJA hacia aquí
    for cell in burning:
        cx, cz = graph.cell_center(cell)
        dx, dz = poi.x - cx, poi.z - cz
        d = math.hypot(dx, dz)
        if d < best:
            best = d
            bearing = math.degrees(math.atan2(dx, -dz)) % 360.0  # 0 = norte (-z)
            gap = abs((bearing - push + 180.0) % 360.0 - 180.0)
            downwind = state.wind.speed > 0 and gap <= DOWNWIND_HALF_ANGLE_DEG
    return best, downwind


def fire_line(state: WorldState, poi: POI, graph: RoadGraph | None) -> str:
    """A qué distancia está el frente más cercano a ese pueblo y si el viento empuja
    hacia él. Sin grafo (o sin fuego) se dice lo que se sabe, que es nada."""
    best, downwind = nearest_fire(state, poi, graph)
    if not math.isfinite(best):
        return "no hay ningún frente activo cerca ahora mismo"
    metros = int(round(best / 10.0) * 10)
    empuje = (
        " y el viento lo empuja hacia ustedes"
        if downwind
        else " y el viento no lo empuja hacia ustedes"
    )
    return f"el frente más cercano está a unos {metros} metros{empuje}"


def roads_line(state: WorldState, aliases: dict[str, str] | None = None) -> str:
    """Las carreteras cortadas, por su nombre y con el motivo si se sabe: es lo que
    hay que decirle a alguien al que se le pide que salga por una ruta."""
    names = _alias_by_edge(aliases)
    cortadas: list[str] = []
    for edge in sorted(state.roads.values(), key=lambda e: e.id):
        if not edge.cut:
            continue
        nombre = names.get(edge.id) or names.get(road_bare(edge.id)) or road_bare(edge.id)
        causa = getattr(edge, "cut_cause", None)
        cortadas.append(
            f"{nombre} (cortada por {causa})" if causa else f"{nombre} (cortada)"
        )
    if not cortadas:
        return "no hay ninguna carretera cortada"
    return "carreteras cortadas: " + ", ".join(cortadas)


def unit_eta_line(state: WorldState, assignment: Assignment | None) -> str:
    """Qué unidad va y cuándo llega, con el nombre que se dice por teléfono."""
    if assignment is None:
        return "todavía no hay ninguna unidad asignada a su pueblo"
    unit = state.units.get(assignment.unit_id)
    if unit is None:
        return "todavía no hay ninguna unidad asignada a su pueblo"
    cuando = _minutes(assignment.eta_s)
    articulo = "el" if unit_name(unit).startswith(("camión", "dron")) else "la"
    return f"va {articulo} {unit_name(unit)} y llega en {cuando}"


def fire_eta_min(state: WorldState, poi: POI, graph: RoadGraph | None) -> float:
    """Minutos que tarda el frente más cercano en llegar al POI con el viento de
    ahora. `inf` si no hay fuego o no hay grafo con el que medir."""
    burning = [c for c in state.cells.values() if c.state == "burning"]
    if not burning or graph is None:
        return math.inf
    mejor = min(
        fire_eta_s(*graph.cell_center(c), poi.x, poi.z, state.wind, graph)
        for c in burning
    )
    return mejor / 60.0 if math.isfinite(mejor) else math.inf


def on_foot_deadline_min(
    state: WorldState | None, poi: POI, graph: RoadGraph | None
) -> int:
    """El plazo que se le dicta a un pueblo que sale a pie. Lo marca el fuego, no la
    ETA de un vehículo: los minutos que faltan para que el frente llegue, menos el
    margen con el que nadie debería jugársela. Nunca menos de uno, y sin fuego
    medible el margen a secas: un plazo hay que dar."""
    eta = fire_eta_min(state, poi, graph) if state is not None else math.inf
    if not math.isfinite(eta):
        return DEADLINE_MARGIN_MIN
    return max(1, int(eta) - DEADLINE_MARGIN_MIN)


def evacuation_target(
    state: WorldState | None,
    poi: POI,
    graph: RoadGraph | None,
    ya_avisados: set[str] | None = None,
) -> POI | None:
    """A dónde va un pueblo que evacúa: **el pueblo vecino que esté a salvo** y, si no
    hay ninguno, el refugio.

    Al vecino ya se le ha avisado por teléfono de que puede llegarle gente
    (`neighbor_alert`), así que mandarlos allí es lo que cierra la historia: antes se
    le avisaba de veinticuatro personas que nunca aparecían porque el sim los
    teletransportaba al refugio. Un vecino que también arde no vale —no se manda a
    nadie a un sitio que se está evacuando—, y para eso está el refugio."""
    if state is None:
        return None
    avisados = ya_avisados or set()
    vecinos = [
        p
        for p in sorted(state.pois.values(), key=lambda p: p.id)
        if p.kind == "village"
        and p.id != poi.id
        and p.id not in avisados
        and _a_salvo(state, p, graph, poi)
    ]
    if vecinos and graph is not None:
        cerca = min(vecinos, key=lambda p: math.hypot(p.x - poi.x, p.z - poi.z))
        return cerca
    return next((p for p in state.pois.values() if p.kind == "shelter"), None)


def _ya_le_toca_salir(state: WorldState, poi: POI) -> bool:
    """¿A ese pueblo ya se le ha dicho que salga?

    No vale mirar la severidad de su tarea: con viento del oeste los dos pueblos del
    valle quedan a sotavento y nacen `critical` a la vez, así que ningún vecino sería
    destino nunca. Lo que distingue es si **ya ha recibido su propia orden** —el hecho
    `poi:<id>:confirmed` o su gente ya en la carretera—, que es lo que de verdad lo
    descarta como sitio a donde mandar a nadie. El caso de la orden ya PEDIDA pero aún
    sin confirmar lo cubre `ya_avisados`, que lo sabe el loop y el estado no."""
    for fact in reversed(state.facts):
        if fact.key == f"poi:{poi.id}:confirmed" and fact.kind != "assumed_default":
            return True
    return any(
        g.poi_id == poi.id and g.state in ("evacuating", "safe")
        for g in state.civilians.values()
    )


SAFE_DESTINATION_M = 60.0
"""Con el frente a menos de esto, un pueblo no es destino de nadie: es una emergencia
propia. Mismo umbral que `loop.AT_THE_DOOR_M`, que es el que decide si a ese pueblo se
le avisa o se le da su propia orden."""


def _a_salvo(
    state: WorldState, poi: POI, graph: RoadGraph | None, origen: POI
) -> bool:
    """Un pueblo al que se puede mandar gente.

    No vale mirar si tiene tarea de evacuación abierta: `tasks._evacuate` abre una por
    **cada** pueblo en cuanto arde una celda en cualquier parte del mapa, así que con
    ese criterio ningún vecino es destino nunca y todo el mundo acaba en el refugio
    (medido en `runs/run_984da0622d93.jsonl`: Pueblo A salió hacia el refugio teniendo
    a Pueblo B a salvo). Lo que decide es el fuego: que no lo tenga en la puerta y que
    esté más lejos de él que el pueblo que sale."""
    if _ya_le_toca_salir(state, poi):
        return False
    d = nearest_fire(state, poi, graph)[0]
    if d <= SAFE_DESTINATION_M:
        return False
    return d > nearest_fire(state, origen, graph)[0]


def evacuation_route(
    state: WorldState | None,
    poi: POI,
    graph: RoadGraph | None,
    ya_avisados: set[str] | None = None,
) -> list[str]:
    """Por dónde sale el pueblo, con los cortes de ahora. Es la ruta de los vecinos,
    no la de ningún medio: a quien evacúa se le dice por dónde irse, no por dónde
    viene alguien. Y es la misma que después caminan (`loop._emit_rescue`), para que
    «la carretera que se les ha instruido» sea literal y no una manera de hablar."""
    destino = evacuation_target(state, poi, graph, ya_avisados)
    if state is None or graph is None or destino is None:
        return []
    if not poi.waypoint_id or not destino.waypoint_id:
        return []
    return (
        graph.with_cuts(state).shortest_path(poi.waypoint_id, destino.waypoint_id) or []
    )


def live_facts(
    state: WorldState,
    poi: POI,
    graph: RoadGraph | None = None,
    assignment: Assignment | None = None,
    aliases: dict[str, str] | None = None,
) -> dict[str, str]:
    """Las variables en vivo que viajan con la llamada: medios, frente, carreteras y
    unidad en camino. El agente las usa para recomendar, y solo puede recomendar lo
    que esté aquí."""
    return {
        "resources": resources_line(state),
        "fire_status": fire_line(state, poi, graph),
        "roads_status": roads_line(state, aliases),
        "unit_eta": unit_eta_line(state, assignment),
    }


BLANK_LIVE = dict.fromkeys(
    ("resources", "fire_status", "roads_status", "unit_eta"), "sin datos"
)


def _live_or_blank(
    state: WorldState | None,
    poi: POI,
    graph: RoadGraph | None,
    assignment: Assignment | None,
    aliases: dict[str, str] | None,
) -> dict[str, str]:
    """Los datos en vivo, o «sin datos» si a esta llamada no le pasaron el estado.
    Nunca una frase inventada: el prompt prohíbe recomendar sobre lo que no está."""
    if state is None:
        return dict(BLANK_LIVE)
    return live_facts(state, poi, graph, assignment, aliases)


EVACUATION_CHECKLIST = (
    "Antes de colgar necesitas saber tres cosas, una detrás de otra: cuántas "
    "personas hay en el pueblo, si hay algún herido y si alguien no puede moverse "
    "por su cuenta. En cuanto tengas cada dato, llama a la herramienta "
    "`reportar_situacion` con lo que sepas hasta ese momento; no esperes a tener "
    "las tres respuestas para llamarla la primera vez, y vuelve a llamarla si te "
    "dan un dato más tarde. Si te confirma que acepta la orden, dilo también en la "
    "llamada a la herramienta."
)
"""El guion de la llamada al pueblo que se quema vive aquí, no en la plataforma: es
lo único así que se puede probar con `pytest` antes del domingo."""

NEIGHBOR_CHECKLIST = (
    "Antes de colgar pregunta si tienen sitio para acoger a la gente que llegue "
    "huyendo, y si necesitan algún recurso adicional para eso. En cuanto tengas la "
    "respuesta, llama a la herramienta `reportar_situacion` con lo que te digan."
)


def _situation_brief_evacuation(
    poi: POI,
    route: str,
    deadline_min: int,
    hazard: str,
    live: dict[str, str],
    committed: str = "",
    destino: str = "",
) -> str:
    """El parte que el operador lee al alcalde: la orden y, detrás, el estado real
    del incendio y de los medios. Todo sale del `WorldState` del momento en que se
    pide la llamada, así que lo que diga por teléfono es verdad mientras lo dice.

    `committed` son los medios cuya dotación ya ha confirmado por teléfono. Es lo
    único que se le puede prometer a un pueblo sin mentirle, y es la razón de que
    esta llamada salga después de las de despacho y no antes."""
    # «En camino», no «confirmados»: la lista incluye unidades que no se telefonean
    # (las ambulancias solo reciben llamada si alguien las pide). Van de verdad, que
    # es lo que importa, pero decir «confirmado» de algo que nadie confirmó sería la
    # misma clase de mentira que este cambio existe para quitar.
    confirmados = f" Medios en camino: {committed}." if committed else ""
    por_donde = f" por {route}" if route else ""
    # El destino se dice: antes la orden daba una carretera y ningún sitio al que ir,
    # y el aviso al vecino prometía gente que el sim mandaba al refugio.
    a_donde = f" hacia {destino}" if destino else ""
    return (
        f"Ha llegado la orden de evacuar {poi.name}{por_donde}{a_donde} en los próximos "
        f"{deadline_min} minutos, por el {hazard}. Dígala completa una vez, "
        "despacio, y confirme que la persona la ha entendido y que la acepta. "
        f"Situación ahora mismo: {live['fire_status']}; {live['roads_status']}; "
        # Sin «hacia su pueblo» delante: una evacuación ya no lleva vehículo, y
        # `unit_eta` dice entonces «todavía no hay ninguna unidad asignada a su
        # pueblo», que con el prefijo se leía dos veces.
        f"medios: {live['resources']}; {live['unit_eta']}."
        f"{confirmados}"
    )


def _situation_brief_neighbor(
    poi: POI, source_poi: POI, hazard: str, live: dict[str, str], incoming: int
) -> str:
    gente = (
        f"Pueden llegarles del orden de {incoming} personas."
        if incoming
        else "No sabemos todavía cuánta gente puede llegarles."
    )
    return (
        f"Está usted llamando a {poi.name}, que ahora mismo no tiene el fuego "
        f"encima. Le avisa porque se está evacuando {source_poi.name} por el "
        f"{hazard} y es posible que en las próximas horas lleguen a {poi.name} "
        f"personas que huyen. {gente} No hace falta que hagan nada todavía, pero "
        "conviene que lo sepan y puedan organizarse. Situación ahora mismo: "
        f"{live['fire_status']}; {live['roads_status']}; medios: {live['resources']}."
    )


CREW_CHECKLIST = (
    "Necesitas dos cosas, en este orden. Primero: si pueden salir ya con lo que se "
    "les pide. En cuanto te contesten, llama a la herramienta `reportar_situacion` "
    "con `confirmed_order` a true si van y a false si no pueden; si te dicen que no, "
    "pregunta por qué en una frase y añádelo en `notes`. Segundo: si pueden "
    "movilizar más dotaciones de las pedidas, y cuántas. Vuelve a llamar a la "
    "herramienta con `extra_units` (el número que te digan, 0 si ninguna) y el plazo "
    "en `notes`. No alargues la llamada: es un minuto."
)
"""Al retén y a la ambulancia se les llama para despachar, no para conversar: la
llamada útil es la que acaba en «voy» o «no puedo» y libera la línea. La segunda
pregunta existe porque el solver deja tareas de extinción sin cubrir en la práctica
totalidad de los planes: no preguntarla era ocultar el único dato que el retén sí
puede cambiar."""

AMBULANCE_CHECKLIST = (
    "Necesitas dos cosas, en este orden. Primero: si pueden ir ya con lo que se les "
    "pide. En cuanto te contesten, llama a la herramienta `reportar_situacion` con "
    "`confirmed_order` a true si van y a false si no pueden, y el motivo en `notes` "
    "si es que no. Segundo: si hay más unidades que puedan movilizar si hiciera "
    "falta, y cuántas; vuelve a llamar a la herramienta con `extra_units`. No "
    "alargues la llamada."
)


def _situation_brief_crew(
    live: dict[str, str], hazard: str, donde: str, peticion: str, cobertura: str
) -> str:
    """El parte al retén: dónde arde, qué se le pide exactamente y qué se queda sin
    cubrir. La petición sale del `Plan`; la cobertura, de lo que el solver no pudo
    asignar. Nada de esto es guion fijo."""
    pide = f"Les pedimos {peticion}. " if peticion else ""
    falta = f"{cobertura.capitalize()}. " if cobertura else ""
    return (
        f"Tiene un {hazard} declarado {donde}. {live['fire_status'].capitalize()}. "
        f"{live['roads_status'].capitalize()}. {pide}{falta}"
        "Dígalo sin rodeos: pregunte primero si pueden salir ya con lo que se les "
        "pide y después si pueden movilizar más dotaciones."
    )


def _people_line(immobile: int, injuries: int) -> str:
    """A quién hay que ir a buscar, dicho como se dice por teléfono. Los dos números
    por separado: un rescate puede nacer de heridos sin que nadie esté inmovilizado,
    y decir «una persona que no puede moverse» cuando lo que hay es un herido manda a
    la dotación con la idea equivocada."""
    partes: list[str] = []
    if immobile > 0:
        partes.append(
            f"{immobile} personas que no pueden moverse solas"
            if immobile > 1
            else "una persona que no puede moverse sola"
        )
    if injuries > 0:
        partes.append(f"{injuries} heridos" if injuries > 1 else "una persona herida")
    return " y ".join(partes) if partes else "personas que necesitan ayuda"


def exact_point(task: Task) -> str:
    """«en el punto 187, 94» si la tarea trae coordenadas del pin, o cadena vacía.

    Un rescate nacido de un pin de GPS sabe dónde está el vecino de verdad, no solo
    a qué pueblo se ancló. Decírselo a la dotación es la diferencia entre plantarse
    en la plaza y llegar hasta él.
    """
    if task.target_x is None or task.target_z is None:
        return ""
    return f" en el punto {task.target_x:.0f}, {task.target_z:.0f}"


def _situation_brief_ambulance(
    live: dict[str, str],
    hazard: str,
    poi_name: str,
    immobile: int,
    peticion: str,
    injuries: int = 0,
    punto: str = "",
) -> str:
    cuantos = _people_line(immobile, injuries)
    pide = f"Les pedimos {peticion}. " if peticion else ""
    donde = f"{poi_name}{punto}"
    return (
        f"Le piden una ambulancia en {donde}, por un {hazard}: hay {cuantos}. "
        f"{live['roads_status'].capitalize()}. {pide}Dígalo en dos frases, pregunte "
        "si pueden ir ya y después si tienen otra unidad disponible."
    )


ADVICE_RULES = (
    "Todo lo que digas sobre medios, rutas, distancias y tiempos tiene que salir de "
    "los datos de esta llamada: no inventes unidades, ni plazos, ni carreteras. Si "
    "te preguntan algo que no está en los datos, dilo con naturalidad («eso no lo "
    "tengo aquí, lo consulto y le llamamos»). Si te preguntan qué hacer, recomienda "
    "con lo que tienes: por qué ruta salir (nunca una carretera cortada), a qué "
    "hora, y qué unidad va de camino. Los datos son del momento de la llamada: si "
    "algo cambia, se lo diremos por otra vía. "
    "No cuelgues porque tarden en contestar: al otro lado hay alguien mirando un "
    "incendio. Espera en silencio, y si no te responden repite la pregunta al menos "
    "dos veces antes de dar por hecho que no hay nadie. Si no entiendes algo, pide "
    "que te lo repitan: una mala línea o un ruido no son motivo para terminar la "
    "llamada, y menos a mitad de un dato. Y nunca le digas a nadie que llame al 112: "
    "el 112 eres tú."
)
"""Las reglas que impiden que un LLM con buena voluntad prometa un helicóptero.

Y que cuelgue antes de tiempo, que es lo que pasó en el ensayo de las 20:28. El
prompt de la plataforma se reserva colgar «salvo que la persona cuelgue o no
conteste», y con 4,7 s de silencio el modelo decidió que el vecino de Pueblo A no
contestaba: se despidió justo antes de que empezara a hablar. En otra llamada se
inventó un «la comunicación no es suficientemente clara, llame al 112» y cortó
justo después de que le dijeran que había tres personas que no podían andar. Ninguna
de esas dos frases sale de aquí: las improvisa el modelo porque nada se lo impedía."""


def evacuation_call(
    task: Task,
    poi: POI,
    assignment: Assignment | None,
    to: str,
    hazard_kind: str,
    roads: dict[str, RoadEdge] | None = None,
    aliases: dict[str, str] | None = None,
    state: WorldState | None = None,
    graph: RoadGraph | None = None,
    committed: str = "",
    destino: POI | None = None,
    ruta: list[str] | None = None,
) -> CallRequest:
    """La orden de evacuación para el POI de una tarea `evacuate`.

    `assignment` es opcional porque una evacuación **no necesita vehículo**: quien
    puede andar se va solo en cuanto se le avisa, y las ambulancias quedan para quien
    no puede. Sin asignación el plazo lo marca el fuego (`on_foot_deadline_min`) y la
    ruta es la de los vecinos a donde vayan (`evacuation_route`), no la de un medio.

    Al alcalde del pueblo que arde: se le dicta la orden, la ruta y el plazo, y se
    le pregunta cuánta gente hay, si hay heridos y si alguien no puede moverse por
    su cuenta. Esa respuesta entra por la herramienta `reportar_situacion`
    (`voice/webhooks.py`) y, si hay inmóviles, `core.tasks._rescue` crea el rescate
    solo: el guion decide qué preguntar, el estado decide si hace falta ambulancia.

    Con `state` viajan además los datos en vivo (medios, frente, carreteras) para
    que el operador recomiende con el mundo de ese segundo y no con un guion fijo."""
    hazard = hazard_name(hazard_kind)
    if assignment is not None:
        deadline = math.ceil(assignment.eta_s / 60.0) + DEADLINE_MARGIN_MIN
        route = route_name(assignment.route, roads, aliases)
    else:
        deadline = on_foot_deadline_min(state, poi, graph)
        # El destino y la ruta los decide quien llama (`loop._emit_evacuation`) y se
        # guardan para que sea LO MISMO que después caminan. Recalcularlos aquí hacía
        # que la orden dijera un sitio y el pueblo saliera hacia otro.
        if destino is None:
            destino = evacuation_target(state, poi, graph)
        if ruta is None:
            ruta = evacuation_route(state, poi, graph)
        route = route_name(ruta, roads, aliases)
    live = _live_or_blank(state, poi, graph, assignment, aliases)
    return CallRequest(
        task_id=task.id,
        poi_id=poi.id,
        to=to,
        audience="official",
        intent="evacuation_order",
        urgency=urgency_of(task),
        facts={
            "role": "evacuation",
            "poi_name": poi.name,
            "route_name": route,
            "deadline_min": str(deadline),
            "hazard_kind": hazard,
            "committed_resources": committed,
            "situation_brief": _situation_brief_evacuation(
                poi, route, deadline, hazard, live, committed,
                destino.name if destino is not None else "",
            ),
            "checklist": EVACUATION_CHECKLIST,
            "advice_rules": ADVICE_RULES,
            **live,
        },
        expect=["confirmation", "headcount", "immobile", "injuries"],
    )


def neighbor_alert_call(
    task: Task,
    poi: POI,
    source_poi: POI,
    to: str,
    hazard_kind: str,
    state: WorldState | None = None,
    graph: RoadGraph | None = None,
    aliases: dict[str, str] | None = None,
) -> CallRequest:
    """El aviso al pueblo vecino de que se está evacuando otro por el mismo peligro:
    puede llegarle gente huyendo, y conviene que lo sepa antes de que llamen a su
    puerta. Urgencia siempre `medium`: no es su emergencia, todavía."""
    hazard = hazard_name(hazard_kind)
    live = _live_or_blank(state, poi, graph, None, aliases)
    incoming = 0
    if state is not None:
        incoming = sum(
            g.count for g in state.civilians.values() if g.poi_id == source_poi.id
        )
    return CallRequest(
        task_id=task.id,
        poi_id=poi.id,
        to=to,
        audience="official",
        intent="neighbor_alert",
        urgency="medium",
        facts={
            "role": "neighbor_alert",
            "poi_name": poi.name,
            "source_poi_name": source_poi.name,
            "hazard_kind": hazard,
            "incoming_people": str(incoming),
            "situation_brief": _situation_brief_neighbor(
                poi, source_poi, hazard, live, incoming
            ),
            "checklist": NEIGHBOR_CHECKLIST,
            "advice_rules": ADVICE_RULES,
            **live,
        },
        expect=["capacity_available"],
    )


QUEUED_CHECKLIST = (
    "Pregunta dos cosas y cuelga: si podrán ir en cuanto terminen lo que tienen "
    "ahora, y en cuántos minutos calculan estar libres. Llama a la herramienta "
    "`reportar_situacion` con `confirmed_order` (true si van después) y "
    "`available_after_min` (los minutos que te digan). Si el encargo dice que es "
    "prioritario, no lo negocies: dile que en cuanto terminen van directos allí y "
    "que te lo confirme."
)
"""Cuando no queda ninguna libre, la pregunta ya no es «¿pueden ir?» sino «¿cuándo?».
Los minutos son lo único que se le puede decir al que está esperando al teléfono."""


def _situation_brief_queued(
    live: dict[str, str],
    hazard: str,
    poi_name: str,
    immobile: int,
    prioritario: bool,
    injuries: int = 0,
) -> str:
    cuantos = _people_line(immobile, injuries)
    orden = (
        "Es prioritario: en cuanto terminen lo que tienen ahora, van directos allí."
        if prioritario
        else "No es prioritario: si no pueden, lo cubrimos con otro medio."
    )
    return (
        "Están todas las ambulancias ocupadas y hay alguien esperando al teléfono "
        f"por un {hazard}: en {poi_name} hay {cuantos}. {orden} "
        f"{live['roads_status'].capitalize()}."
    )


def fire_crew_call(
    task: Task,
    station: POI,
    to: str,
    hazard_kind: str,
    unit_id: str,
    where: str,
    state: WorldState | None = None,
    graph: RoadGraph | None = None,
    aliases: dict[str, str] | None = None,
    plan: Plan | None = None,
    roads: dict[str, RoadEdge] | None = None,
) -> CallRequest:
    """Al retén, en cuanto se detecta el fuego: dónde es, **qué se le pide** y si
    pueden salir. `unit_id` viaja con la llamada para que un «no podemos» entre al
    estado como `unit:<id>:available=false` y el solver reparta con lo que queda.

    Con `plan` la llamada deja de ser un aviso y pasa a ser una petición: las
    unidades que el solver puso sobre los frentes, con su ruta y su ETA, y lo que se
    queda sin cubrir. Sin `plan` se comporta como antes."""
    hazard = hazard_name(hazard_kind)
    asignada = _assignment_for(plan, task.id)
    # Manda el `Assignment`: `unit_id` viene de `_free_unit`, que responde a «quién
    # puede coger el teléfono», no a «a quién ha asignado el solver».
    unidad = asignada.unit_id if asignada is not None else unit_id
    live = _live_or_blank(state, station, graph, asignada, aliases)
    pedidas = _extinguish_assignments(state, plan)
    peticion = (
        requested_units_line(state, pedidas, roads, aliases)
        if state is not None and pedidas
        else ""
    )
    cobertura = (
        coverage_line(state, plan, graph)
        if state is not None and plan is not None
        else ""
    )
    return CallRequest(
        task_id=task.id,
        poi_id=station.id,
        to=to,
        audience="responder",
        intent="fire_crew_dispatch",
        urgency="critical",
        facts={
            "role": "fire_crew",
            "callee": "el retén de bomberos",
            "unit_id": unidad,
            "poi_name": station.name,
            "hazard_kind": hazard,
            # Viaja para que el ack del tool pueda decirla al confirmar: es la única
            # forma de cumplir el «les mandamos la ruta» que el ack ya prometía.
            "route_name": (
                route_name(asignada.route, roads, aliases) if asignada is not None else ""
            ),
            "requested_units": peticion,
            "coverage": cobertura,
            "situation_brief": _situation_brief_crew(
                live, hazard, where, peticion, cobertura
            ),
            "checklist": CREW_CHECKLIST,
            "advice_rules": ADVICE_RULES,
            **live,
        },
        expect=["confirmation", "extra_units"],
    )


def ambulance_call(
    task: Task,
    poi: POI,
    base: POI,
    to: str,
    hazard_kind: str,
    unit_id: str,
    immobile: int,
    state: WorldState | None = None,
    injuries: int = 0,
    graph: RoadGraph | None = None,
    aliases: dict[str, str] | None = None,
    queued: bool = False,
    priority: bool = False,
    waiting_call_id: str = "",
    plan: Plan | None = None,
    roads: dict[str, RoadEdge] | None = None,
) -> CallRequest:
    """A la ambulancia, cuando alguien la ha pedido por teléfono (un rescate nace de
    un hecho `poi:<id>:immobile` de una llamada).

    Con `queued` la llamada es otra: no queda ninguna libre, alguien espera al
    teléfono, y lo que se le pregunta a la dotación es *cuándo* estará libre, para
    poder decírselo al que espera (`waiting_call_id`). `priority` lo decide la
    gravedad del rescate: con un caso crítico no se les pregunta si quieren, se les
    dice que en cuanto terminen van allí."""
    hazard = hazard_name(hazard_kind)
    # Una ambulancia en cola no tiene asignación a este rescate todavía: de eso va la
    # llamada. Una libre sí, y es lo que se le pide.
    asignada = None if queued else _assignment_for(plan, task.id)
    unidad = asignada.unit_id if asignada is not None else unit_id
    live = _live_or_blank(state, poi, graph, asignada, aliases)
    peticion = (
        requested_units_line(state, [asignada], roads, aliases)
        if state is not None and asignada is not None
        else ""
    )
    brief = (
        _situation_brief_queued(live, hazard, poi.name, immobile, priority, injuries)
        if queued
        else _situation_brief_ambulance(
            live, hazard, poi.name, immobile, peticion, injuries, exact_point(task)
        )
    )
    return CallRequest(
        task_id=task.id,
        poi_id=poi.id,
        to=to,
        audience="responder",
        intent="ambulance_dispatch",
        urgency="critical",
        facts={
            "role": "ambulance_queued" if queued else "ambulance",
            "callee": "la dotación de la ambulancia",
            "unit_id": unidad,
            "route_name": (
                route_name(asignada.route, roads, aliases) if asignada is not None else ""
            ),
            "requested_units": peticion,
            "poi_name": poi.name,
            "base_name": base.name,
            "hazard_kind": hazard,
            "immobile": str(immobile),
            "injuries": str(injuries),
            "must_go_next": "sí" if priority else "no",
            "waiting_call_id": waiting_call_id,
            "situation_brief": brief,
            "checklist": QUEUED_CHECKLIST if queued else AMBULANCE_CHECKLIST,
            "advice_rules": ADVICE_RULES,
            **live,
        },
        expect=(
            ["confirmation", "available_after_min"]
            if queued
            else ["confirmation", "extra_units"]
        ),
    )


__all__ = [
    "ambulance_call",
    "committed_resources_line",
    "coverage_line",
    "evacuation_call",
    "fire_crew_call",
    "hazard_name",
    "neighbor_alert_call",
    "requested_units_line",
    "route_name",
    "unit_name",
    "urgency_of",
]
