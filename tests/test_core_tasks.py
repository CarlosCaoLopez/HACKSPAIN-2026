"""Ciclo de vida de las tareas, llamadas salientes y señales en vivo del core.

Bus en memoria (`contracts.bus` con writer a una lista, como `test_voice_fact.py`),
planner stubbeado (ninguna llamada de red) y un escenario mínimo con la Y de
`wildfire_ridge`: base → cruce → desvío norte o sur → Pueblo A.
"""

import logging

import pytest

from contracts import bus
from contracts.calls import Fact
from contracts.events import (
    ActionRequested,
    CallRequest,
    Event,
    EventType,
    SignalRequested,
    TaskChanged,
)
from contracts.plan import UNKNOWN_CONSTRAINT, Policy
from contracts.scenario import HazardSpec, Scenario, Waypoint
from contracts.settings import settings
from contracts.world import POI, Cell, CivilianGroup, RoadEdge, Unit, Wind, WorldState
from core import belief, calls, loop, planner, solver, tasks

RUN = "run_test"
JUDGE = "+34600000000"

# --- escenario ---------------------------------------------------------------


def _scenario(contact_phone: str | None = None) -> Scenario:
    return Scenario(
        id="sc_test",
        name="test",
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
            Waypoint(id="wp_nor_01", x=60, z=-40),
            Waypoint(id="wp_sur_01", x=60, z=40),
            Waypoint(id="wp_pueblo_a", x=100, z=0),
            Waypoint(id="wp_refugio", x=-40, z=0),
        ],
        roads=[
            RoadEdge(id="road:wp_base-wp_cruce", a="wp_base", b="wp_cruce", length_m=30),
            RoadEdge(
                id="road:wp_cruce-wp_nor_01", a="wp_cruce", b="wp_nor_01", length_m=50
            ),
            RoadEdge(
                id="road:wp_nor_01-wp_pueblo_a",
                a="wp_nor_01",
                b="wp_pueblo_a",
                length_m=56,
            ),
            RoadEdge(
                id="road:wp_cruce-wp_sur_01", a="wp_cruce", b="wp_sur_01", length_m=50
            ),
            RoadEdge(
                id="road:wp_sur_01-wp_pueblo_a",
                a="wp_sur_01",
                b="wp_pueblo_a",
                length_m=56,
            ),
            RoadEdge(
                id="road:wp_base-wp_refugio", a="wp_base", b="wp_refugio", length_m=40
            ),
        ],
        pois=[
            POI(
                id="poi_pueblo_a",
                name="Pueblo A",
                kind="village",
                x=100,
                z=0,
                waypoint_id="wp_pueblo_a",
                contact_phone=contact_phone,
            ),
            # El refugio: sin él una evacuación no tiene por dónde salir ni dónde
            # acabar, y el verbo `rescue` no se emite (`loop._emit_rescue`).
            POI(
                id="poi_refugio",
                name="Refugio",
                kind="shelter",
                x=-40,
                z=0,
                waypoint_id="wp_refugio",
            ),
        ],
        units=[
            Unit(
                id="unit_truck2", kind="fire_truck", x=0, z=0, capabilities=["extinguish"]
            ),
            Unit(
                id="unit_ambulance",
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
    # Los números salen del test, no del `.env` de quien lo corre: el de al lado tiene
    # otros y el test no puede depender de eso. `PHONE_OVERRIDE` manda todas las
    # llamadas a pueblos a un mismo número, que es lo que quieren los tests que no van
    # de a quién se llama.
    monkeypatch.setattr(settings, "phone_override", JUDGE)
    monkeypatch.setattr(settings, "phone_pueblo_a", "")
    monkeypatch.setattr(settings, "phone_pueblo_b", "")
    monkeypatch.setattr(settings, "phone_fire_crew", "")
    monkeypatch.setattr(settings, "phone_ambulance", "")
    yield events
    bus.reset()


@pytest.fixture
def fixed_planner(monkeypatch) -> dict[str, int]:
    """Planner sin red: una `Policy` fija y un contador de llamadas al modelo."""
    calls_n = {"n": 0}

    async def _plan(state, reason, rules=""):
        calls_n["n"] += 1
        return Policy(rationale="test", weights={"life_safety": 0.8})

    async def _critique(state, violations):
        calls_n["n"] += 1
        return Policy(rationale="test")

    monkeypatch.setattr(planner, "plan", _plan)
    monkeypatch.setattr(planner, "replan_with_critique", _critique)
    return calls_n


def _ev(
    type_: EventType, payload: dict, source: str = "sim", t_sim: float = 0.0
) -> Event:
    return bus.make_event(type_, payload, source=source, t_sim=t_sim)


async def _ignite(core: loop.Core) -> Event:
    ev = _ev(EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_5_0", "hazard": "wildfire"})
    await bus.publish(ev)  # sella `seq`, como haría el sim de verdad
    await core.on_event(ev)
    return ev


def _of(journal: list[Event], type_: EventType) -> list[Event]:
    return [e for e in journal if e.type == type_]


def _tasks_in(journal: list[Event]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in _of(journal, EventType.TASK_CHANGED):
        t = TaskChanged.model_validate(e.payload).task
        out[t.id] = t.model_dump()
    return out


# --- tareas ------------------------------------------------------------------


async def test_ignition_creates_extinguish_and_evacuate(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)

    st = core.state()
    ext = st.tasks["task_front_5_0"]
    assert ext.kind == "extinguish" and ext.target_cell == "cell_5_0"
    assert ext.required_capability == "extinguish" and not ext.done
    evac = st.tasks["task_evac_poi_pueblo_a"]
    assert evac.kind == "evacuate" and evac.target_poi == "poi_pueblo_a"
    assert evac.required_capability == "transport"
    # Pueblo A está a sotavento (viento del oeste, pueblo al este): crítica.
    assert evac.severity == "critical"

    # Las tareas están en el journal, antes del plan, y el plan inicial salió.
    seen = _tasks_in(journal)
    assert set(seen) == {"task_front_5_0", "task_evac_poi_pueblo_a"}
    types = [e.type for e in journal]
    assert types.index(EventType.TASK_CHANGED) < types.index(EventType.PLAN_EMITTED)
    assert fixed_planner["n"] == 1
    # La evacuación lleva ambulancia y sale sin que nadie la pida: el camión va al
    # frente y la ambulancia a por el pueblo.
    assigned = {a.task_id: a.unit_id for a in core.current_plan().assignments}
    assert assigned == {
        "task_front_5_0": "unit_truck2",
        "task_evac_poi_pueblo_a": "unit_ambulance",
    }
    assert core.current_plan().unassigned_tasks == []
    gotos = [
        ActionRequested.model_validate(e.payload)
        for e in _of(journal, EventType.ACTION_REQUESTED)
    ]
    assert {g.args["unit_id"] for g in gotos} == {"unit_truck2", "unit_ambulance"}


async def test_cell_changed_dedupes_and_burnt_closes(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    n_before = len(_of(journal, EventType.TASK_CHANGED))

    # La misma celda vuelve a llegar como `burning`: no nace otra tarea. Una vecina
    # que prende tampoco: es el mismo frente, y `at_risk` no es fuego.
    for cell, state, t in (
        ("cell_5_0", "burning", 10.0),
        ("cell_6_0", "burning", 12.0),
        ("cell_7_0", "at_risk", 14.0),
    ):
        ev = _ev(
            EventType.WORLD_CELL_CHANGED,
            {"cell_id": cell, "state": state, "hazard": "wildfire"},
            t_sim=t,
        )
        await bus.publish(ev)
        await core.on_event(ev)
    assert len(_of(journal, EventType.TASK_CHANGED)) == n_before
    assert [t.id for t in core.state().tasks.values() if t.kind == "extinguish"] == [
        "task_front_5_0"
    ]

    # Una celda suelta lejos del frente es otro frente. Se quema: se cierra.
    ev = _ev(
        EventType.WORLD_CELL_CHANGED,
        {"cell_id": "cell_5_10", "state": "burning", "hazard": "wildfire"},
        t_sim=20.0,
    )
    await bus.publish(ev)
    await core.on_event(ev)
    assert core.state().tasks["task_front_5_10"].done is False
    ev = _ev(
        EventType.WORLD_CELL_CHANGED,
        {"cell_id": "cell_5_10", "state": "burnt", "hazard": "wildfire"},
        t_sim=30.0,
    )
    await bus.publish(ev)
    await core.on_event(ev)
    assert core.state().tasks["task_front_5_10"].done is True
    assert _tasks_in(journal)["task_front_5_10"]["done"] is True
    assert core.state().tasks["task_front_5_0"].done is False
    # Nada de esto llamó al modelo: solo el plan inicial.
    assert fixed_planner["n"] == 1


async def test_immobile_fact_creates_rescue(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:immobile",
            "value": 2,
            "confidence": 0.9,
            "source": "call:sess_1",
            "severity": "medium",
            "kind": "inferred",
        },
        source="call:sess_1",
        t_sim=40.0,
    )
    await bus.publish(fact)
    await core.on_event(fact)
    rescue = core.state().tasks["task_rescue_poi_pueblo_a"]
    assert rescue.kind == "rescue" and rescue.severity == "critical"
    assert (
        rescue.required_capability == "transport" and rescue.target_poi == "poi_pueblo_a"
    )
    assert "task_rescue_poi_pueblo_a" in _tasks_in(journal)


async def test_assumed_default_immobile_does_not_create_rescue(
    journal, fixed_planner
) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:immobile",
            "value": 2,
            "confidence": 0.3,
            "source": "call:sess_1",
            "severity": "low",
            "kind": "assumed_default",
        },
        source="call:sess_1",
    )
    await bus.publish(fact)
    await core.on_event(fact)
    assert "task_rescue_poi_pueblo_a" not in core.state().tasks


async def test_la_llegada_cierra_la_evacuacion_y_saca_a_la_gente(
    journal, fixed_planner
) -> None:
    """Lo que cierra una evacuación es que llegue la ambulancia, no que el pueblo
    acepte la orden por teléfono. Y al cerrarse sale el verbo `rescue`, que es lo
    único que mueve a los vecinos; sin él se quedaban plantados en el pueblo y
    `civilians_safe` no subía nunca."""
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    assert core.state().units["unit_ambulance"].task_id == "task_evac_poi_pueblo_a"

    # Que el alcalde acepte la orden no cierra nada: la gente sigue en el pueblo
    # hasta que llega quien se la lleva.
    ev = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:confirmed",
            "value": True,
            "confidence": 0.9,
            "source": "call:sess_ok",
            "severity": "medium",
            "kind": "observed",
        },
        source="call:sess_ok",
        t_sim=60.0,
    )
    await bus.publish(ev)
    await core.on_event(ev)
    assert core.state().tasks["task_evac_poi_pueblo_a"].done is False

    ev = _ev(
        EventType.WORLD_UNIT_ARRIVED,
        {"unit_id": "unit_ambulance", "waypoint_id": "wp_pueblo_a"},
        t_sim=90.0,
    )
    await bus.publish(ev)
    await core.on_event(ev)
    assert core.state().tasks["task_evac_poi_pueblo_a"].done is True
    assert core.state().tasks["task_front_5_0"].done is False
    assert _tasks_in(journal)["task_evac_poi_pueblo_a"]["done"] is True
    # Se replanificó solo con el solver (sin modelo) y la ambulancia queda libre.
    assert fixed_planner["n"] == 1
    assert "task_evac_poi_pueblo_a" not in {
        a.task_id for a in core.current_plan().assignments
    }

    # Y la gente sale: sin otro pueblo al que llevarla, al refugio.
    rescates = [
        ActionRequested.model_validate(e.payload)
        for e in _of(journal, EventType.ACTION_REQUESTED)
        if ActionRequested.model_validate(e.payload).verb == "rescue"
    ]
    assert len(rescates) == 1
    assert rescates[0].args["civ_ids"] == ["civ_a"]
    assert rescates[0].args["shelter_id"] == "poi_refugio"


# --- llamadas ----------------------------------------------------------------


async def test_evacuate_assignment_publishes_one_call(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)

    reqs = [
        CallRequest.model_validate(e.payload)
        for e in _of(journal, EventType.CALL_REQUESTED)
    ]
    assert len(reqs) == 1
    req = reqs[0]
    assert req.task_id == "task_evac_poi_pueblo_a" and req.poi_id == "poi_pueblo_a"
    assert req.to == JUDGE
    # A quien se llama es al alcalde, no a un vecino cualquiera: es él quien da el
    # recuento y decide si acepta la orden.
    assert req.intent == "evacuation_order" and req.audience == "official"
    assert req.urgency == "critical"
    assert req.facts["role"] == "evacuation"
    assert req.facts["poi_name"] == "Pueblo A"
    assert req.facts["route_name"] in ("pista norte", "pista sur")
    assert req.facts["hazard_kind"] == "incendio forestal"
    assert int(req.facts["deadline_min"]) >= 6
    assert "evacuar Pueblo A" in req.facts["situation_brief"]
    assert "reportar_situacion" in req.facts["checklist"]
    # `injuries` también: el checklist lo pregunta desde hace tiempo y el contrato de
    # la llamada no lo decía.
    assert req.expect == ["confirmation", "headcount", "immobile", "injuries"]
    # `causes` apunta al evento que provocó el plan.
    fire = _of(journal, EventType.WORLD_FIRE_DETECTED)[0]
    assert _of(journal, EventType.CALL_REQUESTED)[0].causes == [fire.seq]

    # Otro replan (hecho crítico) no vuelve a llamar por la misma tarea.
    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:headcount",
            "value": 12,
            "confidence": 1.0,
            "source": "human",
            "severity": "critical",
        },
        source="human",
    )
    await bus.publish(fact)
    await core.on_event(fact)
    assert fixed_planner["n"] == 2
    assert len(_of(journal, EventType.CALL_REQUESTED)) == 1


async def test_the_call_carries_the_live_state_not_a_fixed_script(
    journal, fixed_planner
) -> None:
    """Lo que el operador puede decir por teléfono sale del `WorldState` de ese
    segundo: qué medios hay y qué hacen, a qué distancia está el frente, qué
    carreteras están cortadas y qué unidad va. Si eso fuera un guion fijo, el
    operador prometería un camión que está averiado."""
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    req = CallRequest.model_validate(_of(journal, EventType.CALL_REQUESTED)[0].payload)

    # Medios: los dos del escenario, con lo que están haciendo ahora.
    assert "camión" in req.facts["resources"]
    assert "ambulancia" in req.facts["resources"]
    # Frente: distancia real a Pueblo A, no una frase hecha.
    assert "metros" in req.facts["fire_status"]
    assert "viento" in req.facts["fire_status"]
    # Carreteras: ninguna cortada todavía.
    assert req.facts["roads_status"] == "no hay ninguna carretera cortada"
    # Quién va y cuándo: la ambulancia que el solver le ha asignado.
    assert "ambulancia" in req.facts["unit_eta"] and "minuto" in req.facts["unit_eta"]
    # Y el parte que se lee en voz alta los lleva dentro.
    brief = req.facts["situation_brief"]
    assert req.facts["fire_status"] in brief and req.facts["resources"] in brief
    assert "no inventes" in req.facts["advice_rules"]

    # Cortamos una carretera: la siguiente llamada ya lo dice con su nombre y causa.
    cut = _ev(
        EventType.WORLD_ROAD_CHANGED,
        {
            "edge_id": "road:wp_cruce-wp_sur_01",
            "cut": True,
            "cause": "un árbol caído",
        },
        t_sim=30.0,
    )
    await bus.publish(cut)
    await core.on_event(cut)
    linea = calls.roads_line(core.state(), {"pista del sur": "road:wp_cruce-wp_sur_01"})
    assert "pista del sur" in linea and "árbol caído" in linea


def _scenario_two_villages() -> Scenario:
    """`_scenario()` más un Pueblo B lejos del fuego, con su propio contacto: lo que
    hace falta para probar el aviso al vecino, sin ruta ni unidad para él (no se
    evacúa, solo se le avisa). Cada pueblo con su número: la llamada al alcalde de
    uno no es la del otro."""
    sc = _scenario(contact_phone="+34000000001")
    return sc.model_copy(
        update={
            "waypoints": [*sc.waypoints, Waypoint(id="wp_pueblo_b", x=100, z=100)],
            "pois": [
                *sc.pois,
                POI(
                    id="poi_pueblo_b",
                    name="Pueblo B",
                    kind="village",
                    x=100,
                    z=100,
                    waypoint_id="wp_pueblo_b",
                    contact_phone="+34000000002",
                ),
            ],
            "civilians": [
                *sc.civilians,
                CivilianGroup(id="civ_b", poi_id="poi_pueblo_b", count=8),
            ],
        }
    )


async def test_evacuating_one_village_alerts_the_other(
    journal, fixed_planner, monkeypatch
) -> None:
    """Pueblo A se evacúa; Pueblo B, que no arde, recibe una llamada aparte: puede
    llegarle gente. No es la orden de evacuación, así que va a su propio número."""
    monkeypatch.setattr(settings, "phone_override", "")  # sin palanca: manda el POI
    core = loop.Core(bus, _scenario_two_villages())
    await _ignite(core)

    reqs = [
        CallRequest.model_validate(e.payload)
        for e in _of(journal, EventType.CALL_REQUESTED)
    ]
    evac = [r for r in reqs if r.intent == "evacuation_order"]
    alerts = [r for r in reqs if r.intent == "neighbor_alert"]
    assert len(evac) == 1 and evac[0].poi_id == "poi_pueblo_a"

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.poi_id == "poi_pueblo_b" and alert.to == "+34000000002"
    assert alert.audience == "official" and alert.urgency == "medium"
    assert alert.facts["role"] == "neighbor_alert"
    assert alert.facts["source_poi_name"] == "Pueblo A"
    assert "Pueblo A" in alert.facts["situation_brief"]
    assert "reportar_situacion" in alert.facts["checklist"]
    assert alert.expect == ["capacity_available"]

    # Otro replan no repite el aviso: una vez por par de pueblos y run.
    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:headcount",
            "value": 20,
            "confidence": 1.0,
            "source": "human",
            "severity": "critical",
        },
        source="human",
    )
    await bus.publish(fact)
    await core.on_event(fact)
    reqs_after = [
        e
        for e in _of(journal, EventType.CALL_REQUESTED)
        if e.payload["intent"] == "neighbor_alert"
    ]
    assert len(reqs_after) == 1


async def test_the_safe_village_has_its_own_number(
    journal, fixed_planner, monkeypatch
) -> None:
    """Las dos llamadas salen a la vez: con un solo número se pisaban en el mismo
    móvil. Y el número es del PUEBLO, no del papel: quien atiende Pueblo B es el mismo
    cuando se le avisa y cuando, girado el viento, se le ordena salir."""
    monkeypatch.setattr(settings, "phone_override", "")
    monkeypatch.setattr(settings, "phone_pueblo_a", JUDGE)
    monkeypatch.setattr(settings, "phone_pueblo_b", "+34638383503")
    core = loop.Core(bus, _scenario_two_villages())
    await _ignite(core)

    reqs = [
        CallRequest.model_validate(e.payload)
        for e in _of(journal, EventType.CALL_REQUESTED)
    ]
    evac = next(r for r in reqs if r.intent == "evacuation_order")
    alert = next(r for r in reqs if r.intent == "neighbor_alert")
    assert evac.to == JUDGE  # el que arde, al suyo
    assert alert.to == "+34638383503"  # el vecino, al suyo


async def test_both_villages_burning_does_not_alert_each_other(
    journal, fixed_planner, monkeypatch
) -> None:
    """Si el vecino también arde, ya se le manda su propia orden de evacuación: no
    hace falta avisarle además de que "puede llegarle gente"."""
    monkeypatch.setattr(settings, "phone_override", JUDGE)
    core = loop.Core(bus, _scenario_two_villages())
    # Una sola ignición entre los dos pueblos (celda de 4 m centrada en 102, 50): a
    # 50 m de cada uno, los dos entran en `critical` a la vez.
    fire = _ev(
        EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_25_12", "hazard": "wildfire"}
    )
    await bus.publish(fire)
    await core.on_event(fire)

    # Pueblo B tiene el fuego encima (severidad `critical`): lo suyo es su propia
    # orden de evacuación cuando haya unidad, no un aviso de "puede llegarle gente".
    assert core.state().tasks["task_evac_poi_pueblo_b"].severity == "critical"
    reqs = [e.payload["intent"] for e in _of(journal, EventType.CALL_REQUESTED)]
    assert "neighbor_alert" not in reqs
    assert "evacuation_order" in reqs


async def test_no_phone_no_call(journal, fixed_planner, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "phone_override", "")
    core = loop.Core(bus, _scenario(contact_phone=None))
    with caplog.at_level(logging.WARNING, logger="core.loop"):
        await _ignite(core)
    assert not _of(journal, EventType.CALL_REQUESTED)
    assert any("PHONE_PUEBLO_A" in r.message for r in caplog.records)


async def test_contact_phone_used_when_no_override(
    journal, fixed_planner, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "phone_override", "")
    core = loop.Core(bus, _scenario(contact_phone="+34000000001"))
    await _ignite(core)
    req = CallRequest.model_validate(_of(journal, EventType.CALL_REQUESTED)[0].payload)
    assert req.to == "+34000000001"


# --- señal a la llamada en curso ----------------------------------------------


async def test_call_fact_replan_signals_unit_dispatched(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)

    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:immobile",
            "value": 3,
            "confidence": 0.9,
            "source": "call:sess_7",
            "severity": "critical",
            "kind": "observed",
        },
        source="call:sess_7",
        t_sim=50.0,
    )
    await bus.publish(fact)
    await core.on_event(fact)
    assert fixed_planner["n"] == 2  # hecho crítico: replan con modelo

    sigs = _of(journal, EventType.CALL_SIGNAL_REQUESTED)
    assert len(sigs) == 1
    sig = SignalRequested.model_validate(sigs[0].payload)
    assert sig.call_id == "sess_7" and sig.key == "unit_dispatched"
    assert sig.payload["unit"] == "ambulancia"
    assert sig.payload["route"] in ("pista norte", "pista sur")
    assert isinstance(sig.payload["eta_s"], int) and sig.payload["eta_s"] > 0
    assert sigs[0].causes == [fact.seq]
    # La señal sale después del plan que la justifica.
    types = [e.type for e in journal]
    assert types.index(EventType.CALL_SIGNAL_REQUESTED) > len(types) - 1 - types[
        ::-1
    ].index(EventType.PLAN_EMITTED)


async def test_sim_fact_does_not_signal(journal, fixed_planner) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:immobile",
            "value": 3,
            "confidence": 1.0,
            "source": "human",
            "severity": "critical",
        },
        source="human",
    )
    await bus.publish(fact)
    await core.on_event(fact)
    assert not _of(journal, EventType.CALL_SIGNAL_REQUESTED)


# --- solver y planner: nada en silencio ---------------------------------------


def test_sync_is_pure_and_idempotent() -> None:
    """Mismo estado, mismas tareas; y con las tareas ya plegadas, nada que publicar."""
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    st = belief.initial_state(RUN, sc)
    assert tasks.sync(st, graph) == []  # sin fuego, sin tareas
    ignited = belief.apply(
        st,
        _ev(EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_5_0", "hazard": "wildfire"}),
    )
    first = tasks.sync(ignited, graph)
    assert [t.id for t in first] == ["task_front_5_0", "task_evac_poi_pueblo_a"]
    assert tasks.sync(ignited, graph) == first
    folded = ignited.model_copy(update={"tasks": {t.id: t for t in first}})
    assert tasks.sync(folded, graph) == []


def test_solver_returns_unknown_constraint_violation() -> None:
    sc = _scenario()
    st = belief.initial_state(RUN, sc)
    policy = Policy(
        rationale="x", hard_constraints=["no_unit_into_burning_cell", "foo:bar"]
    )
    plan, viols = solver.solve_with_violations(
        st, policy, solver.RoadGraph.from_scenario(sc)
    )
    assert plan.policy is policy
    assert [v.verifier for v in viols] == [UNKNOWN_CONSTRAINT]
    assert viols[0].severity == "soft" and "foo:bar" in viols[0].message
    # La firma de contrato sigue devolviendo solo el plan.
    assert solver.solve(st, policy).policy is policy


async def test_loop_publishes_unknown_constraint(journal, monkeypatch) -> None:
    async def _plan(state, reason, rules=""):
        return Policy(rationale="x", hard_constraints=["foo:bar"])

    async def _critique(state, violations):
        assert any(v.verifier == UNKNOWN_CONSTRAINT for v in violations)
        return Policy(rationale="corregida")

    monkeypatch.setattr(planner, "plan", _plan)
    monkeypatch.setattr(planner, "replan_with_critique", _critique)
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    viols = _of(journal, EventType.PLAN_VIOLATION)
    assert viols and viols[0].payload["verifier"] == UNKNOWN_CONSTRAINT
    assert core.current_plan().policy.rationale == "corregida"


async def test_planner_failure_is_logged(monkeypatch, caplog) -> None:
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("sin red")

    monkeypatch.setattr(planner, "AsyncOpenAI", Boom)
    with caplog.at_level(logging.ERROR, logger="core.planner"):
        policy = await planner.plan(_scenario_state(), "test")
    assert policy == planner.neutral_policy()
    assert any("neutra" in r.message for r in caplog.records)


def _scenario_state() -> WorldState:
    return belief.initial_state(RUN, _scenario())


# --- nombres humanos -----------------------------------------------------------


def test_route_and_unit_names() -> None:
    assert (
        calls.route_name(["wp_base", "wp_cruce", "wp_nor_01", "wp_pueblo_a"])
        == "pista norte"
    )
    assert (
        calls.route_name(["wp_base", "wp_cruce", "wp_sur_01", "wp_pueblo_a"])
        == "pista sur"
    )
    roads = {r.id: r for r in _scenario().roads}
    assert calls.route_name(["wp_base", "wp_cruce"], roads) == "wp_base-wp_cruce"
    assert calls.route_name(["wp_x", "wp_y"]) == "wp_x-wp_y"
    assert (
        calls.unit_name(Unit(id="unit_truck2", kind="fire_truck", x=0, z=0)) == "camión 2"
    )
    assert (
        calls.unit_name(Unit(id="unit_ambulance", kind="ambulance", x=0, z=0))
        == "ambulancia"
    )


# --- lo que se le cuenta al vecino -----------------------------------------------


def test_route_name_is_the_track_travelled_not_the_last_waypoint() -> None:
    """Al molino (`wp_sur_02`) se llega por la pista norte y Pueblo A cuando la sur
    está cortada; y una unidad parada en la sur que sale por la norte va "por la
    norte"."""
    assert (
        calls.route_name(
            ["wp_cruce", "wp_nor_01", "wp_nor_02", "wp_pueblo_a", "wp_sur_02"]
        )
        == "pista norte"
    )
    assert (
        calls.route_name(
            ["wp_sur_01", "wp_cruce", "wp_nor_01", "wp_nor_02", "wp_pueblo_a"]
        )
        == "pista norte"
    )
    assert calls.route_name(["wp_cruce", "wp_sur_01", "wp_sur_02"]) == "pista sur"
    assert calls.route_name(["wp_sur_01"]) == "pista sur"  # ya está en la pista


async def test_three_facts_of_one_call_signal_once(journal, fixed_planner) -> None:
    """Un `report_fact` publica varios hechos seguidos (arista cortada, causa, inmóviles).
    La misma unidad por la misma pista se dice una vez, no una por hecho."""
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    keys = [
        ("road:wp_cruce-wp_sur_01:cut", True),
        ("road:wp_cruce-wp_sur_01:cause", "árbol caído"),
        ("poi:poi_pueblo_a:immobile", 3),
    ]
    for key, value in keys:
        fact = _ev(
            EventType.WORLD_FACT_ASSERTED,
            {
                "key": key,
                "value": value,
                "confidence": 0.9,
                "source": "call:sess_9",
                "severity": "critical",
                "kind": "observed",
            },
            source="call:sess_9",
            t_sim=50.0,
        )
        await bus.publish(fact)
        await core.on_event(fact)
    sigs = [
        SignalRequested.model_validate(e.payload)
        for e in _of(journal, EventType.CALL_SIGNAL_REQUESTED)
    ]
    assert len(sigs) == 1, [s.payload for s in sigs]
    assert sigs[0].payload["unit"] == "ambulancia"
    assert sigs[0].payload["route"] == "pista norte"  # la sur está cortada
    assert sigs[0].payload["eta_s"] > 0


def test_severity_weighs_in_the_cost() -> None:
    """A igual distancia, una tarea `critical` cuesta la mitad que una `high` y una
    cuarta parte que una `medium`: dejar sin cubrir lo grave deja de ser gratis."""
    sc = _scenario()
    sc = sc.model_copy(
        update={
            "pois": sc.pois
            + [
                POI(
                    id="poi_molino",
                    name="Molino",
                    kind="landmark",
                    x=60,
                    z=40,
                    waypoint_id="wp_sur_01",
                )
            ],
        }
    )
    graph = solver.RoadGraph.from_scenario(sc)
    st = belief.initial_state(RUN, sc)
    st = belief.apply(
        st,
        _ev(EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_5_0", "hazard": "wildfire"}),
    )
    st = belief.apply_fact(
        st,
        Fact(
            key="poi:poi_molino:immobile",
            value=3,
            confidence=0.9,
            source="call:sess_1",
            severity="critical",
            kind="observed",
            t_sim=50.0,
        ),
    )
    st = st.model_copy(update={"tasks": {t.id: t for t in tasks.sync(st, graph)}})
    rescue = st.tasks["task_rescue_poi_molino"]
    assert rescue.severity == "critical"
    policy = planner.neutral_policy()
    evac = rescue.model_copy(update={"kind": "evacuate"})
    crit = solver._weighted_cost(100.0, st, evac, policy)
    high = solver._weighted_cost(
        100.0, st, evac.model_copy(update={"severity": "high"}), policy
    )
    med = solver._weighted_cost(
        100.0, st, evac.model_copy(update={"severity": "medium"}), policy
    )
    assert crit == 25.0 and high == 50.0 and med == 100.0
    # Y un rescate vale la mitad que una evacuación de la misma gravedad: los que no
    # pueden moverse van antes que los que sí.
    assert solver._weighted_cost(100.0, st, rescue, policy) == 12.5


async def test_signal_waits_until_the_pois_task_is_assigned(
    journal, fixed_planner
) -> None:
    """El vecino del molino no oye "ya va la ambulancia" mientras la ambulancia va a otro
    sitio: la señal sale con el plan que por fin asigna su rescate, y apunta al hecho."""
    sc = _scenario()
    sc = sc.model_copy(
        update={
            "pois": sc.pois
            + [
                POI(
                    id="poi_molino",
                    name="Molino",
                    kind="landmark",
                    x=60,
                    z=40,
                    waypoint_id="wp_sur_01",
                )
            ],
        }
    )
    core = loop.Core(bus, sc)
    await _ignite(core)
    # Lo que ocupa a la única ambulancia es OTRO rescate, que le gana a la
    # evacuación en la que salió. El hecho no viene de una llamada
    # (`source="human"`), así que no genera señal por sí mismo.
    ocupada = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:immobile",
            "value": 2,
            "confidence": 0.9,
            "source": "human",
            "severity": "critical",
            "kind": "observed",
        },
        source="human",
        t_sim=30.0,
    )
    await bus.publish(ocupada)
    await core.on_event(ocupada)
    assert core.state().units["unit_ambulance"].task_id == "task_rescue_poi_pueblo_a"

    # Y está a las puertas de Pueblo A.
    arrive = _ev(
        EventType.WORLD_UNIT_POSITION,
        {"unit_id": "unit_ambulance", "x": 96, "z": 0, "heading": 90.0},
        t_sim=40.0,
    )
    await bus.publish(arrive)
    await core.on_event(arrive)
    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_molino:immobile",
            "value": 3,
            "confidence": 0.9,
            "source": "call:sess_5",
            "severity": "critical",
            "kind": "observed",
        },
        source="call:sess_5",
        t_sim=50.0,
    )
    await bus.publish(fact)
    await core.on_event(fact)
    assert not _of(journal, EventType.CALL_SIGNAL_REQUESTED), "aún no va nadie al molino"
    assert "task_rescue_poi_molino" in core._plan.unassigned_tasks

    # La ambulancia llega a Pueblo A: su rescate cierra y el del molino se asigna.
    moved = _ev(
        EventType.WORLD_UNIT_POSITION,
        {"unit_id": "unit_ambulance", "x": 100, "z": 0, "heading": 90.0},
        t_sim=60.0,
    )
    await bus.publish(moved)
    await core.on_event(moved)
    arrived = _ev(
        EventType.WORLD_UNIT_ARRIVED,
        {"unit_id": "unit_ambulance", "waypoint_id": "wp_pueblo_a"},
        t_sim=60.0,
    )
    await bus.publish(arrived)
    await core.on_event(arrived)
    sigs = _of(journal, EventType.CALL_SIGNAL_REQUESTED)
    assert len(sigs) == 1
    sig = SignalRequested.model_validate(sigs[0].payload)
    assert sig.call_id == "sess_5"
    assert sig.payload["unit"] == "ambulancia" and sig.payload["route"] == "pista sur"
    assert sigs[0].causes == [fact.seq]


def test_coverage_only_violated_when_a_plan_withdraws_it() -> None:
    """Un pueblo con `min_coverage=1` al que nunca llegó nadie no es una violación del
    plan; sí lo es mandar a otro sitio a la unidad que lo cubre."""
    from core import verifiers

    sc = _scenario()
    sc = sc.model_copy(
        update={
            "pois": [
                sc.pois[0].model_copy(update={"min_coverage": 1}),
                POI(
                    id="poi_molino",
                    name="Molino",
                    kind="landmark",
                    x=60,
                    z=40,
                    waypoint_id="wp_sur_01",
                ),
            ],
            "units": sc.units
            + [
                Unit(
                    id="unit_truck9",
                    kind="fire_truck",
                    x=100,
                    z=0,
                    capabilities=["extinguish"],
                )
            ],
        }
    )
    graph = solver.RoadGraph.from_scenario(sc)
    st = belief.initial_state(RUN, sc)
    st = belief.apply(
        st,
        _ev(EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_5_0", "hazard": "wildfire"}),
    )
    st = belief.apply_fact(
        st,
        Fact(
            key="poi:poi_molino:immobile",
            value=2,
            confidence=0.9,
            source="call:sess_2",
            severity="critical",
            kind="observed",
            t_sim=50.0,
        ),
    )
    st = st.model_copy(update={"tasks": {t.id: t for t in tasks.sync(st, graph)}})
    # Nadie en Pueblo A salvo el camión 9: un plan que lo deja allí no viola nada.
    plan = solver.solve(st, planner.neutral_policy(), graph)
    stays = plan.model_copy(
        update={
            "assignments": [a for a in plan.assignments if a.unit_id != "unit_truck9"]
        }
    )
    assert verifiers.coverage_maintained(st, stays, graph) is None
    # Sin ninguna unidad en el pueblo tampoco: no hay cobertura que retirar.
    gone = st.model_copy(
        update={"units": {k: v for k, v in st.units.items() if k != "unit_truck9"}}
    )
    assert verifiers.coverage_maintained(gone, stays, graph) is None
    # Retirar al camión 9 hacia otro POI (el molino) sí.
    withdraws = stays.model_copy(
        update={
            "assignments": [a for a in stays.assignments if a.unit_id != "unit_ambulance"]
            + [
                plan.assignments[0].model_copy(
                    update={
                        "unit_id": "unit_truck9",
                        "task_id": "task_rescue_poi_molino",
                        "route": ["wp_pueblo_a", "wp_sur_01"],
                    }
                )
            ]
        }
    )
    v = verifiers.coverage_maintained(st, withdraws, graph)
    assert v is not None and v.verifier == "coverage_maintained"


def test_a_unit_reached_by_the_fire_can_still_be_assigned_out() -> None:
    """`no_unit_into_burning_cell` no cuenta el waypoint de salida (la unidad ya está
    ahí) ni el destino de una tarea `extinguish` (arde por definición). Antes, con el
    frente encima del cruce, todo par era infactible y el plan salía vacío."""
    sc = _scenario()
    graph = solver.RoadGraph.from_scenario(sc)
    st = belief.initial_state(RUN, sc)
    st = belief.apply(
        st,
        _ev(EventType.WORLD_FIRE_DETECTED, {"cell_id": "cell_5_0", "hazard": "wildfire"}),
    )
    # El frente llega al cruce, donde está la ambulancia.
    cx, cz = graph.coords["wp_cruce"]
    cruce_cell = graph.cell_of(cx, cz)
    burning = Cell(
        id=cruce_cell,
        cx=int((cx - graph.origin[0]) // graph.cell_size),
        cz=int((cz - graph.origin[1]) // graph.cell_size),
        state="burning",
    )
    st = st.model_copy(update={"cells": {**st.cells, cruce_cell: burning}})
    amb = st.units["unit_ambulance"].model_copy(update={"x": 30, "z": 0})
    st = st.model_copy(update={"units": {**st.units, "unit_ambulance": amb}})
    # Alguien ha pedido la ambulancia: es lo único que la hace salir. Sin esto no hay
    # tarea que pueda servir y el test no probaría nada.
    st = belief.apply_fact(
        st,
        Fact(
            key="poi:poi_pueblo_a:immobile",
            value=2,
            confidence=0.9,
            source="call:sess_1",
            severity="critical",
            kind="observed",
            t_sim=10.0,
        ),
    )
    st = st.model_copy(update={"tasks": {t.id: t for t in tasks.sync(st, graph)}})
    policy = Policy(rationale="x", hard_constraints=["no_unit_into_burning_cell"])
    plan = solver.solve(st, policy, graph)
    by_unit = {a.unit_id: a.task_id for a in plan.assignments}
    assert by_unit.get("unit_ambulance") == "task_rescue_poi_pueblo_a", by_unit
    assert "task_rescue_poi_pueblo_a" not in plan.unassigned_tasks


# --- unidad libre vuelve a base, órdenes que no se repiten -----------------------


async def test_free_ambulance_returns_to_its_base_once(journal, fixed_planner) -> None:
    """La ambulancia hace un rescate en Pueblo A y se queda libre allí: vuelve a su
    waypoint de origen (`wp_base`) con un `goto`, y solo uno. Así el siguiente rescate
    la hace salir de la base y se la ve llegar, en vez de «ya va» con ETA 0."""
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    pedida = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:immobile",
            "value": 2,
            "confidence": 0.9,
            "source": "human",
            "severity": "critical",
            "kind": "observed",
        },
        source="human",
        t_sim=30.0,
    )
    await bus.publish(pedida)
    await core.on_event(pedida)
    for ev in (
        _ev(
            EventType.WORLD_UNIT_POSITION,
            {"unit_id": "unit_ambulance", "x": 100, "z": 0, "heading": 90.0},
            t_sim=60.0,
        ),
        _ev(
            EventType.WORLD_UNIT_ARRIVED,
            {"unit_id": "unit_ambulance", "waypoint_id": "wp_pueblo_a"},
            t_sim=60.0,
        ),
    ):
        await bus.publish(ev)
        await core.on_event(ev)
    assert core.state().tasks["task_rescue_poi_pueblo_a"].done is True
    gotos = [
        ActionRequested.model_validate(e.payload).args
        for e in _of(journal, EventType.ACTION_REQUESTED)
    ]
    # `.get`: al cerrarse el rescate sale también un `rescue`, que lleva `civ_ids` y
    # `shelter_id` en vez de `unit_id`.
    home = [g["route"] for g in gotos if g.get("unit_id") == "unit_ambulance"]
    assert home[-1][0] == "wp_pueblo_a" and home[-1][-1] == "wp_base"
    assert core._last_actions["unit_ambulance"] == (loop.RETURN_TASK, tuple(home[-1]))

    # Otro tick, aún parada en Pueblo A: no se le repite la orden.
    tick = _ev(
        EventType.WORLD_TICK,
        {"t_sim": 61.0, "wind": {"bearing_deg": 270, "speed": 1.0}},
        t_sim=61.0,
    )
    await bus.publish(tick)
    await core.on_event(tick)
    n_after = len(
        [
            e
            for e in _of(journal, EventType.ACTION_REQUESTED)
            if ActionRequested.model_validate(e.payload).args.get("unit_id")
            == "unit_ambulance"
        ]
    )
    assert n_after == len(home)


async def test_no_goto_for_a_unit_already_parked_at_its_waypoint(
    journal, fixed_planner
) -> None:
    """El camión ya está en el waypoint desde el que se ataca la celda: ruta de un
    solo waypoint, ninguna orden (cada `goto` repetido lo ponía `moving` un segundo
    y le cortaba el sofocado). Y sigue `idle` en el estado: el plan no lo mueve."""
    sc = _scenario()
    truck = next(u for u in sc.units if u.id == "unit_truck2")
    sc = sc.model_copy(
        update={
            "units": [
                truck.model_copy(update={"x": 30.0, "z": 0.0}),  # en wp_cruce
                *(u for u in sc.units if u.id != "unit_truck2"),
            ]
        }
    )
    core = loop.Core(bus, sc)
    await _ignite(core)
    plan = core.current_plan()
    a = next(a for a in plan.assignments if a.unit_id == "unit_truck2")
    assert a.route == ["wp_cruce"]
    gotos = [
        ActionRequested.model_validate(e.payload).args["unit_id"]
        for e in _of(journal, EventType.ACTION_REQUESTED)
    ]
    assert "unit_truck2" not in gotos
    assert core.state().units["unit_truck2"].status == "idle"
    assert core._last_actions["unit_truck2"] == ("task_front_5_0", ("wp_cruce",))


async def test_units_out_of_the_plan_keep_their_last_order(
    journal, fixed_planner
) -> None:
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    # Al encenderse se mueven los dos: el camión al frente y la ambulancia a evacuar.
    assert set(core._last_actions) == {"unit_truck2", "unit_ambulance"}
    pedida = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:immobile",
            "value": 2,
            "confidence": 0.9,
            "source": "human",
            "severity": "critical",
            "kind": "observed",
        },
        source="human",
        t_sim=30.0,
    )
    await bus.publish(pedida)
    await core.on_event(pedida)
    assert set(core._last_actions) == {"unit_truck2", "unit_ambulance"}
    # El rescate cierra: la ambulancia sale del plan pero su última orden (ahora
    # la vuelta a base) sigue registrada, y la del camión no se toca.
    truck_order = core._last_actions["unit_truck2"]
    ev = _ev(
        EventType.WORLD_UNIT_ARRIVED,
        {"unit_id": "unit_ambulance", "waypoint_id": "wp_pueblo_a"},
        t_sim=60.0,
    )
    await bus.publish(ev)
    await core.on_event(ev)
    assert core._last_actions["unit_truck2"] == truck_order
    assert "unit_ambulance" in core._last_actions


async def test_two_trucks_one_front_is_a_valid_plan(journal, fixed_planner) -> None:
    sc = _scenario()
    sc = sc.model_copy(
        update={
            "units": sc.units
            + [
                Unit(
                    id="unit_truck1",
                    kind="fire_truck",
                    x=0,
                    z=0,
                    capabilities=["extinguish"],
                )
            ]
        }
    )
    core = loop.Core(bus, sc)
    await _ignite(core)
    plan = core.current_plan()
    trucks = sorted(a.unit_id for a in plan.assignments if a.task_id == "task_front_5_0")
    assert trucks == ["unit_truck1", "unit_truck2"]
    assert not _of(journal, EventType.PLAN_VIOLATION)
    assert "task_front_5_0" not in plan.unassigned_tasks


# --- nombres de calle y esquema del planner ----------------------------------------


def test_route_name_uses_the_scenario_aliases() -> None:
    roads = {
        "road:wp_sur_02-wp_pueblo_b": RoadEdge(
            id="road:wp_sur_02-wp_pueblo_b", a="wp_sur_02", b="wp_pueblo_b", length_m=84
        )
    }
    aliases = {
        "pista de pueblo b": "road:wp_sur_02-wp_pueblo_b",
        "camino de pueblo b": "road:wp_sur_02-wp_pueblo_b",  # el primero manda
    }
    route = ["wp_sur_02", "wp_pueblo_b"]
    assert calls.route_name(route, roads) == "wp_sur_02-wp_pueblo_b"
    assert calls.route_name(route, roads, aliases) == "pista de pueblo b"
    assert calls.route_name(route, None, aliases) == "pista de pueblo b"
    # Los desvíos siguen siendo "pista sur"/"pista norte" con o sin alias.
    assert calls.route_name(["wp_cruce", "wp_sur_01", "wp_sur_02"], roads, aliases) == (
        "pista sur"
    )


def test_policy_tool_schema_has_the_contract_enums_inline() -> None:
    import json

    schema = planner._POLICY_TOOL["function"]["parameters"]
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in schema
    urgency = schema["properties"]["notify"]["items"]["properties"]["urgency"]
    assert urgency["enum"] == ["low", "medium", "critical"]
    audience = schema["properties"]["notify"]["items"]["properties"]["audience"]
    assert audience["enum"] == ["resident", "responder", "official"]


def test_parse_policy_repairs_an_out_of_contract_urgency(caplog) -> None:
    raw = {
        "rationale": "Pueblo A primero",
        "weights": {"life_safety": 0.9},
        "notify": [
            {
                "poi_id": "poi_pueblo_a",
                "audience": "resident",
                "message_intent": "evacuar",
                "urgency": "high",
            }
        ],
    }
    with caplog.at_level(logging.WARNING, logger="core.planner"):
        policy = planner.parse_policy(raw)
    assert policy.weights == {"life_safety": 0.9}  # no se pierde por un aviso
    assert policy.notify[0].urgency == "medium"
    assert "fuera del contrato" in caplog.text


# --- llamadas a los medios ------------------------------------------------------


async def test_fire_detected_calls_the_crew_once(
    journal, fixed_planner, monkeypatch
) -> None:
    """Al retén se le llama en cuanto hay fuego: breve, con dónde arde y si pueden
    salir. Una vez por run, aunque el plan se rehaga veinte veces."""
    monkeypatch.setattr(settings, "phone_fire_crew", "+34900000001")
    sc = _scenario()
    sc = sc.model_copy(
        update={
            "pois": [
                *sc.pois,
                POI(
                    id="poi_base",
                    name="Parque de bomberos",
                    kind="base",
                    x=0,
                    z=0,
                    waypoint_id="wp_base",
                ),
            ]
        }
    )
    core = loop.Core(bus, sc)
    await _ignite(core)

    crew = [
        CallRequest.model_validate(e.payload)
        for e in _of(journal, EventType.CALL_REQUESTED)
        if e.payload["intent"] == "fire_crew_dispatch"
    ]
    assert len(crew) == 1
    r = crew[0]
    assert r.to == "+34900000001" and r.audience == "responder"
    assert r.facts["role"] == "fire_crew" and r.facts["callee"] == "el retén de bomberos"
    assert r.facts["unit_id"] == "unit_truck2"  # el camión que puede salir
    assert "incendio forestal" in r.facts["situation_brief"]
    # Dónde arde, en referencia humana y con su preposición: nadie conduce a una celda.
    assert "a " in r.facts["situation_brief"] and "Pueblo A" in r.facts["situation_brief"]
    assert "pueden salir" in r.facts["checklist"]

    # Un replan más no vuelve a llamar.
    tick = _ev(
        EventType.WORLD_CELL_CHANGED,
        {"cell_id": "cell_6_0", "state": "burning", "hazard": "wildfire"},
        t_sim=20.0,
    )
    await bus.publish(tick)
    await core.on_event(tick)
    assert (
        len(
            [
                e
                for e in _of(journal, EventType.CALL_REQUESTED)
                if e.payload["intent"] == "fire_crew_dispatch"
            ]
        )
        == 1
    )


async def test_no_crew_phone_no_crew_call(journal, fixed_planner, monkeypatch) -> None:
    monkeypatch.setattr(settings, "phone_fire_crew", "")
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    assert not [
        e
        for e in _of(journal, EventType.CALL_REQUESTED)
        if e.payload["intent"] == "fire_crew_dispatch"
    ]


async def test_ambulance_is_called_only_when_asked_for_and_free(
    journal, fixed_planner, monkeypatch
) -> None:
    """A la ambulancia se la llama dos veces con motivo distinto: al arder el pueblo,
    para pedirle las que van a evacuarlo; y cuando alguien reporta un inmóvil, para el
    rescate. Una ambulancia que ya va a ese pueblo cuenta como libre para su rescate:
    el sitio es el mismo y ella es la que antes llega."""
    monkeypatch.setattr(settings, "phone_ambulance", "+34900000002")
    sc = _scenario()
    sc = sc.model_copy(
        update={
            "units": [
                *sc.units,
                Unit(
                    id="unit_ambulance2",
                    kind="ambulance",
                    x=0,
                    z=0,
                    capabilities=["transport"],
                ),
            ]
        }
    )
    core = loop.Core(bus, sc)
    await _ignite(core)
    evac = [
        CallRequest.model_validate(e.payload)
        for e in _of(journal, EventType.CALL_REQUESTED)
        if e.payload["intent"] == "ambulance_dispatch"
    ]
    assert len(evac) == 1 and evac[0].task_id == "task_evac_poi_pueblo_a"
    assert "2 ambulancias" in evac[0].facts["requested_units"]

    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:immobile",
            "value": 2,
            "confidence": 0.9,
            "source": "call:sess_amb",
            "severity": "critical",
            "kind": "observed",
        },
        source="call:sess_amb",
        t_sim=40.0,
    )
    await bus.publish(fact)
    await core.on_event(fact)

    amb = [
        CallRequest.model_validate(e.payload)
        for e in _of(journal, EventType.CALL_REQUESTED)
        if e.payload["intent"] == "ambulance_dispatch"
        and e.payload["task_id"] == "task_rescue_poi_pueblo_a"
    ]
    assert len(amb) == 1
    r = amb[0]
    assert r.to == "+34900000002"
    assert r.facts["role"] == "ambulance"
    # Cuál de las dos da igual —el desempate del solver es asunto suyo, y `358eeed`
    # lo cambió—; lo que importa es que sea una que va a ese pueblo.
    assert r.facts["unit_id"] in ("unit_ambulance", "unit_ambulance2")
    assert r.facts["immobile"] == "2"
    assert "no pueden moverse" in r.facts["situation_brief"]


async def test_all_ambulances_busy_calls_to_ask_when(
    journal, fixed_planner, monkeypatch
) -> None:
    """Si no queda ninguna libre, la llamada no se cancela: se telefonea a la
    dotación ocupada para preguntarle CUÁNDO, y esa respuesta vuelve a quien sigue
    esperando al teléfono (`waiting_call_id`). Con un caso crítico, además, no se le
    pregunta si quiere: se le dice que va en cuanto termine."""
    monkeypatch.setattr(settings, "phone_ambulance", "+34900000002")
    sc = _scenario()
    sc = sc.model_copy(
        update={
            "pois": sc.pois
            + [
                POI(
                    id="poi_molino",
                    name="Molino",
                    kind="landmark",
                    x=60,
                    z=40,
                    waypoint_id="wp_sur_01",
                )
            ],
        }
    )
    core = loop.Core(bus, sc)
    await _ignite(core)
    # Lo que ocupa a la única ambulancia es otro rescate, que le gana a la
    # evacuación en la que salió.
    otro = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_molino:immobile",
            "value": 1,
            "confidence": 0.9,
            "source": "human",
            "severity": "critical",
            "kind": "observed",
        },
        source="human",
        t_sim=30.0,
    )
    await bus.publish(otro)
    await core.on_event(otro)
    assert core.state().units["unit_ambulance"].task_id == "task_rescue_poi_molino"

    fact = _ev(
        EventType.WORLD_FACT_ASSERTED,
        {
            "key": "poi:poi_pueblo_a:immobile",
            "value": 2,
            "confidence": 0.9,
            "source": "call:sess_amb",
            "severity": "critical",
            "kind": "observed",
            "call_id": "sess_amb",
        },
        source="call:sess_amb",
        t_sim=40.0,
    )
    await bus.publish(fact)
    await core.on_event(fact)

    amb = [
        CallRequest.model_validate(e.payload)
        for e in _of(journal, EventType.CALL_REQUESTED)
        if e.payload["intent"] == "ambulance_dispatch"
    ]
    # Tres: la de la evacuación, la del molino (que se llevó a la ambulancia) y la
    # de Pueblo A, para la que ya no queda ninguna.
    assert [r.facts["role"] for r in amb] == [
        "ambulance",
        "ambulance",
        "ambulance_queued",
    ]
    r = amb[-1]
    assert r.facts["role"] == "ambulance_queued"
    assert r.facts["must_go_next"] == "sí"  # el rescate es crítico
    assert r.facts["waiting_call_id"] == "sess_amb"  # a quién hay que contestarle
    assert "todas las ambulancias ocupadas" in r.facts["situation_brief"]
    assert "van directos allí" in r.facts["situation_brief"]
    assert "available_after_min" in r.expect


async def test_el_guion_le_prohibe_colgar_antes_de_tiempo(journal, fixed_planner) -> None:
    """El prompt de la plataforma se reserva colgar «salvo que la persona cuelgue o no
    conteste», y en el ensayo de las 20:28 el modelo usó esa salida con 4,7 s de
    silencio: se despidió de Pueblo A justo antes de que el vecino empezara a hablar.
    En otra llamada improvisó «llame al 112» y cortó justo después de que le dijeran
    que había tres personas que no podían andar.

    La contra vive aquí y no en la plataforma, que es lo que deja probarla."""
    core = loop.Core(bus, _scenario())
    await _ignite(core)
    reglas = CallRequest.model_validate(
        _of(journal, EventType.CALL_REQUESTED)[0].payload
    ).facts["advice_rules"]

    assert "No cuelgues porque tarden en contestar" in reglas
    assert "repite la pregunta al menos dos veces" in reglas
    assert "no son motivo para terminar la llamada" in reglas
    # El 112 es él: mandar a alguien a llamar al número desde el que le llaman es el
    # bucle que cerró la tercera llamada del ensayo.
    assert "el 112 eres tú" in reglas
