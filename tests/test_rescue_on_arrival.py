"""Llegar a un pueblo cierra su evacuación; mover a la gente es otra cosa.

El core solo pedía `goto`, así que de los cuatro verbos del sim, `rescue` no se
ejecutaba nunca. La demo cantaba "Pueblo B evacuado" en el minuto 5 con los
aldeanos donde estaban.
"""

from pathlib import Path

import pytest

from contracts.events import Event, EventType
from contracts.world import CivilianGroup
from core.loop import Core
from sim.scenario import load

ESCENARIO = Path("scenarios/wildfire_ridge.yaml")


class BusFalso:
    def __init__(self) -> None:
        self.publicados: list[Event] = []

    def current_run_id(self) -> str:
        return "run_test"

    async def publish(self, ev: Event) -> None:
        self.publicados.append(ev)

    def subscribe(self, *types):  # no se usa: los eventos se inyectan a mano
        raise NotImplementedError


@pytest.fixture
def core():
    bus = BusFalso()
    c = Core(bus, load(ESCENARIO))
    return c, bus


def acciones(bus, verbo):
    return [
        e.payload
        for e in bus.publicados
        if e.type == EventType.ACTION_REQUESTED
        and (e.payload.get("verb") if isinstance(e.payload, dict) else e.payload.verb)
        == verbo
    ]


async def test_llegar_a_un_pueblo_pide_rescate(core, monkeypatch):
    c, bus = core
    grupo = CivilianGroup(id="civ_pueblo_a", poi_id="poi_pueblo_a", count=24, immobile=3)
    c._state = c._state.model_copy(update={"civilians": {grupo.id: grupo}})

    tarea = type(
        "T", (), {"done": True, "kind": "evacuate", "target_poi": "poi_pueblo_a"}
    )()
    llegada = Event(
        run_id="run_test",
        seq=1,
        t_wall=__import__("datetime").datetime.now(__import__("datetime").UTC),
        t_sim=100.0,
        type=EventType.WORLD_UNIT_ARRIVED,
        source="sim",
        payload={"unit_id": "unit_ambulance", "waypoint_id": "wp_pueblo_a"},
    )
    await c._emit_rescue([tarea], llegada)

    pedidas = acciones(bus, "rescue")
    assert len(pedidas) == 1, "una llegada con civiles expuestos pide un rescate"
    args = pedidas[0]["args"] if isinstance(pedidas[0], dict) else pedidas[0].args
    assert args["civ_ids"] == ["civ_pueblo_a"]
    assert args["shelter_id"] == "poi_refugio", "al refugio del escenario"


async def test_no_se_rescata_a_quien_ya_esta_a_salvo(core):
    c, bus = core
    grupo = CivilianGroup(
        id="civ_pueblo_a", poi_id="poi_pueblo_a", count=24, immobile=3, state="safe"
    )
    c._state = c._state.model_copy(update={"civilians": {grupo.id: grupo}})
    tarea = type(
        "T", (), {"done": True, "kind": "evacuate", "target_poi": "poi_pueblo_a"}
    )()
    llegada = Event(
        run_id="run_test",
        seq=1,
        t_wall=__import__("datetime").datetime.now(__import__("datetime").UTC),
        t_sim=100.0,
        type=EventType.WORLD_UNIT_ARRIVED,
        source="sim",
        payload={},
    )
    await c._emit_rescue([tarea], llegada)
    assert acciones(bus, "rescue") == []


async def test_una_tarea_de_extincion_no_rescata_a_nadie(core):
    c, bus = core
    grupo = CivilianGroup(id="civ_pueblo_a", poi_id="poi_pueblo_a", count=24, immobile=3)
    c._state = c._state.model_copy(update={"civilians": {grupo.id: grupo}})
    tarea = type(
        "T", (), {"done": True, "kind": "extinguish", "target_poi": "poi_pueblo_a"}
    )()
    llegada = Event(
        run_id="run_test",
        seq=1,
        t_wall=__import__("datetime").datetime.now(__import__("datetime").UTC),
        t_sim=100.0,
        type=EventType.WORLD_UNIT_ARRIVED,
        source="sim",
        payload={},
    )
    await c._emit_rescue([tarea], llegada)
    assert acciones(bus, "rescue") == []


async def test_solo_dispara_con_una_llegada(core):
    """Un tick cualquiera no mueve a nadie."""
    c, bus = core
    grupo = CivilianGroup(id="civ_pueblo_a", poi_id="poi_pueblo_a", count=24, immobile=3)
    c._state = c._state.model_copy(update={"civilians": {grupo.id: grupo}})
    tarea = type(
        "T", (), {"done": True, "kind": "evacuate", "target_poi": "poi_pueblo_a"}
    )()
    tick = Event(
        run_id="run_test",
        seq=1,
        t_wall=__import__("datetime").datetime.now(__import__("datetime").UTC),
        t_sim=100.0,
        type=EventType.WORLD_TICK,
        source="sim",
        payload={"t_sim": 100.0, "wind": {"bearing_deg": 270.0, "speed": 1.2}},
    )
    await c._emit_rescue([tarea], tick)
    assert acciones(bus, "rescue") == []
