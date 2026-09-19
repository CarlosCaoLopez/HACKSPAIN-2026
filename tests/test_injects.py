"""El planificador de injects. Puro: decide qué vence, no lo ejecuta."""

from contracts.scenario import InjectSpec
from sim.injects import ROAD_CUT, UNIT_FAILURE, WIND_SHIFT, InjectScheduler

GUION = [
    InjectSpec(at=150, type=WIND_SHIFT, payload={"bearing": 300, "speed": 1.8}),
    InjectSpec(at=210, type=ROAD_CUT, payload={"edge": "wp_sur_01-wp_sur_02"}),
    InjectSpec(at=240, type=UNIT_FAILURE, payload={"unit": "unit_truck2"}),
]


def test_solo_dispara_lo_vencido():
    s = InjectScheduler(GUION)
    assert s.due(100) == []
    assert [i.type for i in s.due(150)] == [WIND_SHIFT]
    assert [i.type for i in s.due(215)] == [ROAD_CUT]


def test_cada_inject_se_dispara_una_sola_vez():
    """Si el viento gira dos veces, el escenario deja de ser el del guion."""
    s = InjectScheduler(GUION)
    s.due(300)
    assert s.due(300) == []
    assert len(s.fired) == 3


def test_un_salto_de_tiempo_no_se_come_ninguno():
    """Si un tick se retrasa, los tres tienen que salir juntos, no perderse."""
    s = InjectScheduler(GUION)
    assert len(s.due(1000)) == 3
    assert s.pending == []


def test_el_orden_es_estable_con_el_mismo_at():
    """Dos injects a la misma hora salen siempre igual o el run no se reproduce."""
    a = InjectSpec(at=10, type=WIND_SHIFT)
    b = InjectSpec(at=10, type=ROAD_CUT)
    uno = [i.type for i in InjectScheduler([a, b]).due(10)]
    otro = [i.type for i in InjectScheduler([b, a]).due(10)]
    assert uno == otro == [ROAD_CUT, WIND_SHIFT]


def test_arm_mete_uno_a_mano():
    """El botón de emergencia del pitch, y el inject que dispara la llamada."""
    s = InjectScheduler([])
    s.arm(InjectSpec(at=0, type=ROAD_CUT, payload={"edge": "wp_sur_01-wp_sur_02"}))
    assert [i.type for i in s.due(0)] == [ROAD_CUT]


def test_arm_respeta_el_orden_temporal():
    s = InjectScheduler(GUION)
    s.arm(InjectSpec(at=1, type=ROAD_CUT))
    assert s.pending[0].at == 1
