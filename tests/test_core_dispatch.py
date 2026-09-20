"""Despacho por teléfono: nadie sale hasta que su dotación conteste.

Antes de esto, `_after_plan` emitía las acciones ANTES que las llamadas: medido en
`runs/run_25b73a7f8a7d.jsonl`, el camión llevaba treinta segundos conduciendo cuando
el retén dijo «vamos», y la llamada al retén decía literalmente «todavía no hay
ninguna unidad asignada a su pueblo» mientras le mandaba su propio camión.

Aquí se fija lo contrario: la llamada lleva lo que el solver decidió, la unidad espera
a que cuelguen, y la orden de evacuación sale detrás con los medios que van de verdad.
"""

import pytest

from contracts import bus
from contracts.events import ActionRequested, CallRequest, Event, EventType
from contracts.plan import Assignment, Plan, PlanContext, Policy
from contracts.scenario import HazardSpec, Scenario, Waypoint
from contracts.settings import settings
from contracts.world import POI, CivilianGroup, RoadEdge, Unit, Wind
from core import loop, planner

RUN = "run_dispatch"
JUDGE = "+34600000000"
CREW = "+34600000001"

pytestmark = pytest.mark.anyio


def _scenario() -> Scenario:
    """La Y mínima, con parque de bomberos y dos camiones: sin un POI `base` el core
    no llama al retén, y sin dos camiones no se ve que la petición es un conjunto."""
    return Scenario(
        id="sc_dispatch",
        name="dispatch",
        origin=(0.0, 0.0),
        hazard=HazardSpec(
            kind="wildfire",
            origin_cell="cell_5_0",
            cell_size=4,
            wind=Wind(bearing_deg=270, speed=1.0),
        ),
        waypoints=[
            Waypoint(id="wp_base", x=0, z=0),
            Waypoint(id="wp_cruce", x=30, z=0),
            Waypoint(id="wp_sur_01", x=60, z=40),
            Waypoint(id="wp_pueblo_a", x=100, z=0),
        ],
        roads=[
            RoadEdge(id="road:wp_base-wp_cruce", a="wp_base", b="wp_cruce", length_m=30),
            RoadEdge(
                id="road:wp_cruce-wp_sur_01", a="wp_cruce", b="wp_sur_01", length_m=50
            ),
            RoadEdge(
                id="road:wp_sur_01-wp_pueblo_a",
                a="wp_sur_01",
                b="wp_pueblo_a",
                length_m=56,
            ),
        ],
        pois=[
            POI(
                id="poi_base",
                name="Parque de bomberos",
                kind="base",
                x=0,
                z=0,
                waypoint_id="wp_base",
            ),
            POI(
                id="poi_pueblo_a",
                name="Pueblo A",
                kind="village",
                x=100,
                z=0,
                waypoint_id="wp_pueblo_a",
            ),
        ],
        units=[
            Unit(
                id="unit_truck1", kind="fire_truck", x=0, z=0, capabilities=["extinguish"]
            ),
            Unit(
                id="unit_truck2", kind="fire_truck", x=0, z=0, capabilities=["extinguish"]
            ),
            # Dos ambulancias: con una sola no se ve el relevo de retención, que es
            # el caso que se coló en `runs/run_bc8247ef1ed1.jsonl`.
            Unit(
                id="unit_ambulance",
                kind="ambulance",
                x=0,
                z=0,
                capabilities=["transport"],
            ),
            Unit(
                id="unit_ambulance2",
                kind="ambulance",
                x=0,
                z=0,
                capabilities=["transport"],
            ),
        ],
        civilians=[CivilianGroup(id="civ_a", poi_id="poi_pueblo_a", count=10)],
    )


@pytest.fixture
def journal(monkeypatch) -> list[Event]:
    events: list[Event] = []
    bus.reset()
    bus.configure(run_id=RUN, writer=events.append)
    monkeypatch.setattr(settings, "phone_override", JUDGE)
    monkeypatch.setattr(settings, "phone_fire_crew", CREW)
    monkeypatch.setattr(settings, "phone_ambulance", "")
    yield events
    bus.reset()


@pytest.fixture
def fixed_planner(monkeypatch) -> None:
    async def _plan(state, reason, rules=""):
        return Policy(rationale="test", weights={"life_safety": 0.8})

    async def _critique(state, violations):
        return Policy(rationale="test")

    monkeypatch.setattr(planner, "plan", _plan)
    monkeypatch.setattr(planner, "replan_with_critique", _critique)


def _ev(type_: EventType, payload: dict, source="sim", t_sim=0.0) -> Event:
    return bus.make_event(type_, payload, source=source, t_sim=t_sim)


async def _feed(core: loop.Core, ev: Event) -> Event:
    await bus.publish(ev)
    await core.on_event(ev)
    return ev


def _tick(t_sim: float) -> Event:
    return _ev(
        EventType.WORLD_TICK,
        {"t_sim": t_sim, "wind": {"bearing_deg": 270.0, "speed": 1.0}},
        t_sim=t_sim,
    )


async def _ignite(core: loop.Core) -> Event:
    return await _feed(
        core,
        _ev(EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_5_0", "hazard": "wildfire"}),
    )


def _calls(journal, intent: str) -> list[CallRequest]:
    return [
        CallRequest.model_validate(e.payload)
        for e in journal
        if e.type == EventType.CALL_REQUESTED and e.payload["intent"] == intent
    ]


def _gotos(journal, unit_id: str) -> list[ActionRequested]:
    out = []
    for e in journal:
        if e.type != EventType.ACTION_REQUESTED:
            continue
        a = ActionRequested.model_validate(e.payload)
        if a.verb == "goto" and a.args.get("unit_id") == unit_id:
            out.append(a)
    return out


async def _hang_up(core: loop.Core, task_id: str, outcome="answered", t_sim=10.0):
    """El `call.ended` que HappyRobot publica al colgar."""
    return await _feed(
        core,
        _ev(
            EventType.CALL_ENDED,
            {
                "call_id": f"hl_{task_id}",
                "task_id": task_id,
                "direction": "outbound",
                "started_t": 0.0,
                "ended_t": t_sim,
                "outcome": outcome,
                "transcript": "",
                "facts": None,
            },
            source=f"call:hl_{task_id}",
            t_sim=t_sim,
        ),
    )


# --- la petición -------------------------------------------------------------


async def test_la_llamada_al_reten_pide_lo_que_decidio_el_solver(
    journal, fixed_planner
) -> None:
    """El defecto original: `fire_crew_call` recibía `assignment=None`, así que le
    decía al retén que no había unidad asignada mientras le mandaba su camión."""
    core = loop.Core(bus, _scenario())
    await _ignite(core)

    crew = _calls(journal, "fire_crew_dispatch")
    assert len(crew) == 1
    r = crew[0]
    assert "todavía no hay ninguna unidad asignada" not in r.facts["unit_eta"]
    assert r.facts["requested_units"], "la petición sale del Plan, no del censo"
    assert "camión" in r.facts["requested_units"]
    assert r.facts["route_name"], "la ruta viaja para que el ack pueda decirla"
    assert r.facts["coverage"], "lo que no se cubre también se dice"
    assert r.facts["unit_id"] in ("unit_truck1", "unit_truck2")
    assert "extra_units" in r.expect, "se pregunta si pueden mandar más"


# --- la retención ------------------------------------------------------------


async def test_el_camion_no_sale_hasta_que_cuelgan(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)

    crew = _calls(journal, "fire_crew_dispatch")[0]
    unidad = crew.facts["unit_id"]
    assert _gotos(journal, unidad) == [], "sale antes de que la dotación conteste"

    await _hang_up(core, crew.task_id)

    salidas = _gotos(journal, unidad)
    assert len(salidas) == 1
    assert salidas[0].dispatch_confirmed is True


async def test_sin_respuesta_sale_igual_y_se_anota(journal, fixed_planner) -> None:
    """Una demo con los camiones congelados no es una degradación aceptable: se
    suelta al agotar el plazo y el `action.requested` lo dice."""
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    crew = _calls(journal, "fire_crew_dispatch")[0]
    unidad = crew.facts["unit_id"]

    await _feed(core, _tick(5.0))
    assert _gotos(journal, unidad) == [], "el plazo no se ha agotado todavía"

    await _feed(core, _tick(loop.DISPATCH_RING_S + 1.0))

    salidas = _gotos(journal, unidad)
    assert len(salidas) == 1
    assert salidas[0].dispatch_confirmed is False


async def test_un_no_podemos_deja_a_esa_unidad_en_tierra(journal, fixed_planner) -> None:
    """El «no podemos salir» entra como `unit:<id>:available=false` por el tool,
    antes de colgar. Esa unidad no sale y el solver reparte sin ella."""
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    crew = _calls(journal, "fire_crew_dispatch")[0]
    unidad = crew.facts["unit_id"]

    await _feed(
        core,
        _ev(
            EventType.WORLD_FACT_ASSERTED,
            {
                "key": f"unit:{unidad}:available",
                "value": False,
                "confidence": 0.9,
                "source": "call:hl_crew",
                "severity": "critical",
                "kind": "observed",
                "call_id": "hl_crew",
            },
            source="call:hl_crew",
            t_sim=8.0,
        ),
    )
    await _hang_up(core, crew.task_id, t_sim=9.0)

    assert _gotos(journal, unidad) == [], "no se manda a quien ha dicho que no puede"


# --- la orden al pueblo, detrás ----------------------------------------------


async def test_la_orden_al_pueblo_espera_a_los_medios(journal, fixed_planner) -> None:
    """La orden de evacuación no promete un camión que nadie ha confirmado."""
    core = loop.Core(bus, _scenario())
    await _ignite(core)

    assert _calls(journal, "evacuation_order") == [], "sale antes que los medios"

    crew = _calls(journal, "fire_crew_dispatch")[0]
    await _hang_up(core, crew.task_id)

    evac = _calls(journal, "evacuation_order")
    assert len(evac) == 1, "y sale una sola vez, no una por tick"
    assert evac[0].facts["committed_resources"], "con los medios que van de verdad"
    assert "Medios en camino" in evac[0].facts["situation_brief"]


async def test_descolgar_reinicia_el_plazo(journal, fixed_planner) -> None:
    """Lo que falló en vivo (`runs/run_4699e9e46f2f.jsonl`): la llamada descolgó a
    los 3 s y colgó a los 69, pero el plazo único de 45 la cortó por la mitad y
    soltó los camiones con `dispatch_confirmed=False` mientras seguían hablando.

    Sonar y hablar son dos plazos distintos."""
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    crew = _calls(journal, "fire_crew_dispatch")[0]
    unidad = crew.facts["unit_id"]

    await _feed(
        core,
        _ev(
            EventType.CALL_STARTED,
            {
                "call_id": "hl_crew",
                "task_id": crew.task_id,
                "to": CREW,
                "direction": "outbound",
            },
            source="call:hl_crew",
            t_sim=3.0,
        ),
    )
    # Pasado el plazo de SONAR, pero hablando: no se suelta a nadie.
    await _feed(core, _tick(loop.DISPATCH_RING_S + 10.0))
    assert _gotos(journal, unidad) == [], "cortaba la llamada a media frase"

    await _hang_up(core, crew.task_id, t_sim=69.0)
    salidas = _gotos(journal, unidad)
    assert len(salidas) == 1
    assert salidas[0].dispatch_confirmed is True


# --- la ambulancia: solo sale si alguien la pide, y tampoco antes de colgar ------


def _fact(key: str, value, kind: str = "observed", t_sim: float = 20.0) -> Event:
    """Un `world.fact.asserted` como el que publica una llamada."""
    return _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": key,
            "value": value,
            "confidence": 0.9,
            "source": "call:hl_vecino",
            "severity": "critical",
            "kind": kind,
            "call_id": "hl_vecino",
        },
        source="call:hl_vecino",
        t_sim=t_sim,
    )


async def test_sin_telefono_de_ambulancia_salen_sin_llamar(
    journal, fixed_planner
) -> None:
    """Que arda un pueblo saca a las ambulancias a por él: la evacuación pide
    `transport` y el solver le abre una columna a cada ambulancia
    (`solver._evac_columns`). Sin `PHONE_AMBULANCE` no hay a quién pedírselas, y
    salen en el mismo segundo de la ignición, como el camión sin `PHONE_FIRE_CREW`."""
    core = loop.Core(bus, _scenario())
    await _ignite(core)

    evac = core.state().tasks["task_evac_poi_pueblo_a"]
    assert evac.required_capability == "transport"
    assert {
        a.unit_id for a in core.current_plan().assignments if a.task_id == evac.id
    } == {
        "unit_ambulance",
        "unit_ambulance2",
    }
    for unidad in ("unit_ambulance", "unit_ambulance2"):
        salidas = _gotos(journal, unidad)
        assert len(salidas) == 1 and salidas[0].args["route"][-1] == "wp_pueblo_a"
        assert (
            salidas[0].dispatch_confirmed is None
        )  # nadie la pidió: no hay a quién esperar
    assert not _calls(journal, "ambulance_dispatch")

    # Y colgar el retén no les manda nada nuevo: ya iban.
    await _hang_up(core, _calls(journal, "fire_crew_dispatch")[0].task_id)
    await _feed(core, _tick(loop.DISPATCH_RING_S + 10.0))
    assert len(_gotos(journal, "unit_ambulance")) == 1


async def test_la_evacuacion_pide_las_ambulancias_antes_de_moverlas(
    journal, fixed_planner, monkeypatch
) -> None:
    """El mismo despacho que el retén: se llama a la dotación de la ambulancia
    pidiéndole TODAS las que el solver manda al pueblo, ninguna sale hasta que
    cuelguen, y solo entonces se le dicta la orden al pueblo con lo que va de verdad."""
    monkeypatch.setattr(settings, "phone_ambulance", "+34600000002")
    core = loop.Core(bus, _scenario())
    await _ignite(core)

    amb = _calls(journal, "ambulance_dispatch")
    assert len(amb) == 1
    assert amb[0].task_id == "task_evac_poi_pueblo_a" and amb[0].to == "+34600000002"
    assert amb[0].facts["role"] == "ambulance"
    assert amb[0].facts["evacuating"] == "10"
    assert "2 ambulancias" in amb[0].facts["requested_units"]
    assert "para evacuar Pueblo A" in amb[0].facts["situation_brief"]
    assert "10 vecinos" in amb[0].facts["situation_brief"]
    for unidad in ("unit_ambulance", "unit_ambulance2"):
        assert core._held.get(unidad) == "task_evac_poi_pueblo_a"
        assert _gotos(journal, unidad) == [], "sale antes de que la dotación conteste"

    # El retén cuelga: salen los camiones, las ambulancias siguen al teléfono y la
    # orden al pueblo sigue esperando a saber qué medios van.
    await _hang_up(core, _calls(journal, "fire_crew_dispatch")[0].task_id)
    assert _gotos(journal, "unit_truck1") and _gotos(journal, "unit_ambulance") == []
    assert not _calls(journal, "evacuation_order")

    await _hang_up(core, amb[0].task_id, t_sim=20.0)
    for unidad in ("unit_ambulance", "unit_ambulance2"):
        salidas = _gotos(journal, unidad)
        assert len(salidas) == 1 and salidas[0].dispatch_confirmed is True
        assert salidas[0].args["route"][-1] == "wp_pueblo_a"
    orden = _calls(journal, "evacuation_order")
    assert len(orden) == 1 and orden[0].poi_id == "poi_pueblo_a"
    assert "ambulancia" in orden[0].facts["committed_resources"]


async def test_la_orden_al_pueblo_sale_aunque_nadie_evacue(
    journal, fixed_planner
) -> None:
    """La orden de evacuación colgaba de que el plan asignara una unidad a la tarea.
    Al quitarle el vehículo a la evacuación se habría quedado sin orden —y con ella el
    pueblo sin quien reporte inmóviles, y el rescate sin nacer—, así que ahora sale de
    la TAREA abierta."""
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    await _hang_up(core, _calls(journal, "fire_crew_dispatch")[0].task_id)

    orden = _calls(journal, "evacuation_order")
    assert len(orden) == 1
    assert orden[0].poi_id == "poi_pueblo_a"
    assert orden[0].facts["role"] == "evacuation"
    # Y el plazo y la ruta son los de quien sale andando, no los de un vehículo.
    assert int(orden[0].facts["deadline_min"]) >= 1
    assert "immobile" in orden[0].expect and "injuries" in orden[0].expect


async def test_la_ambulancia_no_sale_hasta_que_cuelgan(
    journal, fixed_planner, monkeypatch
) -> None:
    """El mismo invariante que el camión, que es lo que no se cumplía: un ciudadano
    dice que hay alguien que no puede moverse, se le pide la ambulancia al centro, y
    la unidad no arranca hasta que esa dotación cuelga."""
    monkeypatch.setattr(settings, "phone_ambulance", "+34600000002")
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    await _hang_up(core, _calls(journal, "fire_crew_dispatch")[0].task_id)
    # Las ambulancias ya van a evacuar el pueblo, con su «vamos» dado.
    await _hang_up(core, "task_evac_poi_pueblo_a", t_sim=20.0)
    assert all(
        len(_gotos(journal, u)) == 1 for u in ("unit_ambulance", "unit_ambulance2")
    )

    await _feed(core, _fact("poi:poi_pueblo_a:immobile", 2))

    amb = [
        c
        for c in _calls(journal, "ambulance_dispatch")
        if c.task_id != "task_evac_poi_pueblo_a"
    ]
    assert len(amb) == 1 and amb[0].task_id == "task_rescue_poi_pueblo_a"
    unidad = amb[0].facts["unit_id"]
    # Una que ya va a ESE pueblo cuenta como libre: se la despacha, no se la pone en cola.
    assert amb[0].facts["role"] == "ambulance"
    assert amb[0].to == "+34600000002"
    assert amb[0].facts["immobile"] == "2"
    assert len(_gotos(journal, unidad)) == 1, "sale al rescate antes de que contesten"

    await _hang_up(core, amb[0].task_id, t_sim=30.0)

    salidas = _gotos(journal, unidad)
    assert len(salidas) == 2
    assert salidas[-1].dispatch_confirmed is True


async def test_un_inmovil_asumido_no_saca_ninguna_ambulancia(
    journal, fixed_planner, monkeypatch
) -> None:
    """Regla 4 de punta a punta: `budget.safe_default` asume un inmóvil cuando se
    agota el presupuesto, y ese hecho se ve en gris cursiva, pero no manda a nadie.
    Una ambulancia sale porque alguien la pidió, no porque nadie contestara."""
    monkeypatch.setattr(settings, "phone_ambulance", "+34600000002")
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    await _hang_up(core, _calls(journal, "fire_crew_dispatch")[0].task_id)

    antes = len(_gotos(journal, "unit_ambulance"))
    llamadas = len(_calls(journal, "ambulance_dispatch"))  # la de la evacuación
    await _feed(core, _fact("poi:poi_pueblo_a:immobile", 1, kind="assumed_default"))

    assert "task_rescue_poi_pueblo_a" not in core.state().tasks
    assert len(_calls(journal, "ambulance_dispatch")) == llamadas
    assert len(_gotos(journal, "unit_ambulance")) == antes


async def test_un_herido_saca_la_ambulancia_igual_que_un_inmovil(
    journal, fixed_planner, monkeypatch
) -> None:
    """«Hay un herido» y «hay alguien que no puede moverse» piden lo mismo. Y el
    número llega a la dotación: sin guardarlo en el estado, el parte decía «una
    persona que no puede moverse sola» de alguien que lo que tenía era una herida."""
    monkeypatch.setattr(settings, "phone_ambulance", "+34600000002")
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    await _hang_up(core, _calls(journal, "fire_crew_dispatch")[0].task_id)

    antes = {u: len(_gotos(journal, u)) for u in ("unit_ambulance", "unit_ambulance2")}
    await _feed(core, _fact("poi:poi_pueblo_a:injuries", 2))

    assert "task_rescue_poi_pueblo_a" in core.state().tasks
    amb = [
        c
        for c in _calls(journal, "ambulance_dispatch")
        if c.task_id == "task_rescue_poi_pueblo_a"
    ]
    assert len(amb) == 1
    assert amb[0].facts["injuries"] == "2"
    assert "2 heridos" in amb[0].facts["situation_brief"]
    unidad = amb[0].facts["unit_id"]
    assert len(_gotos(journal, unidad)) == antes[unidad], "sale al rescate sin confirmar"


async def test_un_relevo_de_unidad_hereda_la_retencion(
    journal, fixed_planner, monkeypatch
) -> None:
    """La retención es de la TAREA, no de la unidad.

    Visto en `runs/run_bc8247ef1ed1.jsonl`: la llamada salió pidiendo
    `unit_ambulance2` (seq 519) y un replan seis eventos después puso a
    `unit_ambulance` en el mismo rescate (seq 524), que arrancó con
    `dispatch_confirmed=null` mientras la dotación seguía descolgando. Quien releva,
    espera."""
    monkeypatch.setattr(settings, "phone_ambulance", "+34600000002")
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    await _hang_up(core, _calls(journal, "fire_crew_dispatch")[0].task_id)

    ev = await _feed(core, _fact("poi:poi_pueblo_a:immobile", 2))
    amb = _calls(journal, "ambulance_dispatch")[0]
    llamada = amb.facts["unit_id"]
    assert core._held.get(llamada) == amb.task_id
    relevo = "unit_ambulance2" if llamada == "unit_ambulance" else "unit_ambulance"
    espera_desde = core._held_since[llamada]
    antes = len(_gotos(journal, relevo))  # la salida a evacuar, que no es de este rescate

    # El solver cambia de ambulancia para el mismo rescate.
    plan = Plan(
        id="plan_relevo",
        run_id=RUN,
        created_t=core.state().t_sim,
        policy=Policy(rationale="relevo"),
        assignments=[
            Assignment(
                unit_id=relevo,
                task_id=amb.task_id,
                route=["wp_base", "wp_cruce", "wp_pueblo_a"],
                eta_s=60.0,
                cost=1.0,
            )
        ],
        context=PlanContext(world_seq=core.state().seq),
    )
    await core._emit_actions(plan, ev)

    assert len(_gotos(journal, relevo)) == antes, (
        "el relevo salió sin que nadie confirmara"
    )
    assert core._held.get(relevo) == amb.task_id
    # Y sin regalarle otros 45 s a quien ya estaba sonando.
    assert core._held_since[relevo] == espera_desde
