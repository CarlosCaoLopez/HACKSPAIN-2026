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
from contracts.plan import Assignment
from contracts.world import POI, RoadEdge, Task, Unit, WorldState
from core.solver import RoadGraph

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


UNIT_STATUS_WORDS = {
    "working": "trabajando el fuego",
    "moving": "de camino",
    "idle": "disponible",
    "unavailable": "fuera de servicio",
}

DOWNWIND_HALF_ANGLE_DEG = 45.0
"""Igual que en `core.tasks`: el cono dentro del cual el viento empuja hacia el POI."""


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular}" if n == 1 else f"{n} {plural}"


def resources_line(state: WorldState) -> str:
    """Qué medios hay y qué están haciendo, ahora mismo. Es lo que el operador puede
    prometer por teléfono sin inventarse nada: sale del `WorldState`, no del guion."""
    by_status: dict[str, list[str]] = {}
    for unit in sorted(state.units.values(), key=lambda u: u.id):
        word = UNIT_STATUS_WORDS.get(unit.status, unit.status)
        by_status.setdefault(word, []).append(UNIT_NAMES.get(unit.kind, unit.kind))
    partes: list[str] = []
    for word, kinds in by_status.items():
        cuenta: dict[str, int] = {}
        for k in kinds:
            cuenta[k] = cuenta.get(k, 0) + 1
        nombres = ", ".join(
            _plural(n, k, f"{k}es" if k.endswith("n") else f"{k}s")
            for k, n in sorted(cuenta.items())
        )
        partes.append(f"{nombres} {word}")
    return "; ".join(partes) if partes else "sin medios registrados"


def fire_line(state: WorldState, poi: POI, graph: RoadGraph | None) -> str:
    """A qué distancia está el frente más cercano a ese pueblo y si el viento empuja
    hacia él. Sin grafo (o sin fuego) se dice lo que se sabe, que es nada."""
    burning = [c for c in state.cells.values() if c.state == "burning"]
    if not burning or graph is None:
        return "no hay ningún frente activo cerca ahora mismo"
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
    minutos = max(1, math.ceil(assignment.eta_s / 60.0))
    cuando = "un minuto" if minutos == 1 else f"unos {minutos} minutos"
    articulo = "el" if unit_name(unit).startswith(("camión", "dron")) else "la"
    return f"va {articulo} {unit_name(unit)} y llega en {cuando}"


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
    poi: POI, route: str, deadline_min: int, hazard: str, live: dict[str, str]
) -> str:
    """El parte que el operador lee al alcalde: la orden y, detrás, el estado real
    del incendio y de los medios. Todo sale del `WorldState` del momento en que se
    pide la llamada, así que lo que diga por teléfono es verdad mientras lo dice."""
    return (
        f"Ha llegado la orden de evacuar {poi.name} por {route} en los próximos "
        f"{deadline_min} minutos, por el {hazard}. Dígala completa una vez, "
        "despacio, y confirme que la persona la ha entendido y que la acepta. "
        f"Situación ahora mismo: {live['fire_status']}; {live['roads_status']}; "
        f"medios: {live['resources']}; hacia su pueblo {live['unit_eta']}."
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


ADVICE_RULES = (
    "Todo lo que digas sobre medios, rutas, distancias y tiempos tiene que salir de "
    "los datos de esta llamada: no inventes unidades, ni plazos, ni carreteras. Si "
    "te preguntan algo que no está en los datos, dilo con naturalidad («eso no lo "
    "tengo aquí, lo consulto y le llamamos»). Si te preguntan qué hacer, recomienda "
    "con lo que tienes: por qué ruta salir (nunca una carretera cortada), a qué "
    "hora, y qué unidad va de camino. Los datos son del momento de la llamada: si "
    "algo cambia, se lo diremos por otra vía."
)
"""Las reglas que impiden que un LLM con buena voluntad prometa un helicóptero."""


def evacuation_call(
    task: Task,
    poi: POI,
    assignment: Assignment,
    to: str,
    hazard_kind: str,
    roads: dict[str, RoadEdge] | None = None,
    aliases: dict[str, str] | None = None,
    state: WorldState | None = None,
    graph: RoadGraph | None = None,
) -> CallRequest:
    """La orden de evacuación para el POI de una tarea `evacuate` ya asignada.

    Al alcalde del pueblo que arde: se le dicta la orden, la ruta y el plazo, y se
    le pregunta cuánta gente hay, si hay heridos y si alguien no puede moverse por
    su cuenta. Esa respuesta entra por la herramienta `reportar_situacion`
    (`voice/webhooks.py`) y, si hay inmóviles, `core.tasks._rescue` crea el rescate
    solo: el guion decide qué preguntar, el estado decide si hace falta ambulancia.

    Con `state` viajan además los datos en vivo (medios, frente, carreteras) para
    que el operador recomiende con el mundo de ese segundo y no con un guion fijo."""
    deadline = math.ceil(assignment.eta_s / 60.0) + DEADLINE_MARGIN_MIN
    hazard = hazard_name(hazard_kind)
    route = route_name(assignment.route, roads, aliases)
    live = (
        live_facts(state, poi, graph, assignment, aliases)
        if state is not None
        else dict.fromkeys(
            ("resources", "fire_status", "roads_status", "unit_eta"), "sin datos"
        )
    )
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
            "situation_brief": _situation_brief_evacuation(
                poi, route, deadline, hazard, live
            ),
            "checklist": EVACUATION_CHECKLIST,
            "advice_rules": ADVICE_RULES,
            **live,
        },
        expect=["confirmation", "headcount", "immobile"],
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
    live = (
        live_facts(state, poi, graph, None, aliases)
        if state is not None
        else dict.fromkeys(
            ("resources", "fire_status", "roads_status", "unit_eta"), "sin datos"
        )
    )
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


__all__ = [
    "evacuation_call",
    "hazard_name",
    "neighbor_alert_call",
    "route_name",
    "unit_name",
    "urgency_of",
]
