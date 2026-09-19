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
        nombres = ", ".join(_plural(n, k) for k, n in sorted(cuenta.items()))
        partes.append(f"{nombres} {word}")
    return "; ".join(partes) if partes else "sin medios registrados"


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


CREW_CHECKLIST = (
    "Solo necesitas una cosa: si pueden salir ya. Pregúntalo directamente y, en "
    "cuanto te contesten, llama a la herramienta `reportar_situacion` con "
    "`confirmed_order` a true si van y a false si no pueden. Si te dicen que no, "
    "pregunta por qué en una frase y añádelo en `notes`. No alargues la llamada: "
    "son treinta segundos."
)
"""Al retén y a la ambulancia se les llama para despachar, no para conversar: la
llamada útil es la que acaba en «voy» o «no puedo» y libera la línea."""


def _situation_brief_crew(live: dict[str, str], hazard: str, donde: str) -> str:
    return (
        f"Tiene un {hazard} declarado {donde}. {live['fire_status'].capitalize()}. "
        f"{live['roads_status'].capitalize()}. Dígalo en dos frases, sin rodeos, y "
        "pregunte si pueden salir ya."
    )


def _situation_brief_ambulance(
    live: dict[str, str], hazard: str, poi_name: str, immobile: int
) -> str:
    cuantos = (
        f"{immobile} personas que no pueden moverse solas"
        if immobile > 1
        else "una persona que no puede moverse sola"
    )
    return (
        f"Le piden una ambulancia en {poi_name}, por un {hazard}: hay {cuantos}. "
        f"{live['roads_status'].capitalize()}. Dígalo en dos frases y pregunte si "
        "pueden ir ya."
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
    live: dict[str, str], hazard: str, poi_name: str, immobile: int, prioritario: bool
) -> str:
    cuantos = (
        f"{immobile} personas que no pueden moverse solas"
        if immobile > 1
        else "una persona que no puede moverse sola"
    )
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
) -> CallRequest:
    """Al retén, en cuanto se detecta el fuego: dónde es, qué tiene delante y si
    pueden salir. `unit_id` viaja con la llamada para que un «no podemos» entre al
    estado como `unit:<id>:available=false` y el solver reparta con lo que queda."""
    hazard = hazard_name(hazard_kind)
    live = _live_or_blank(state, station, graph, None, aliases)
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
            "unit_id": unit_id,
            "poi_name": station.name,
            "hazard_kind": hazard,
            "situation_brief": _situation_brief_crew(live, hazard, where),
            "checklist": CREW_CHECKLIST,
            "advice_rules": ADVICE_RULES,
            **live,
        },
        expect=["confirmation"],
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
    graph: RoadGraph | None = None,
    aliases: dict[str, str] | None = None,
    queued: bool = False,
    priority: bool = False,
    waiting_call_id: str = "",
) -> CallRequest:
    """A la ambulancia, cuando alguien la ha pedido por teléfono (un rescate nace de
    un hecho `poi:<id>:immobile` de una llamada).

    Con `queued` la llamada es otra: no queda ninguna libre, alguien espera al
    teléfono, y lo que se le pregunta a la dotación es *cuándo* estará libre, para
    poder decírselo al que espera (`waiting_call_id`). `priority` lo decide la
    gravedad del rescate: con un caso crítico no se les pregunta si quieren, se les
    dice que en cuanto terminen van allí."""
    hazard = hazard_name(hazard_kind)
    live = _live_or_blank(state, poi, graph, None, aliases)
    brief = (
        _situation_brief_queued(live, hazard, poi.name, immobile, priority)
        if queued
        else _situation_brief_ambulance(live, hazard, poi.name, immobile)
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
            "unit_id": unit_id,
            "poi_name": poi.name,
            "base_name": base.name,
            "hazard_kind": hazard,
            "immobile": str(immobile),
            "must_go_next": "sí" if priority else "no",
            "waiting_call_id": waiting_call_id,
            "situation_brief": brief,
            "checklist": QUEUED_CHECKLIST if queued else CREW_CHECKLIST,
            "advice_rules": ADVICE_RULES,
            **live,
        },
        expect=["confirmation", "available_after_min"] if queued else ["confirmation"],
    )


__all__ = [
    "ambulance_call",
    "evacuation_call",
    "fire_crew_call",
    "hazard_name",
    "neighbor_alert_call",
    "route_name",
    "unit_name",
    "urgency_of",
]
