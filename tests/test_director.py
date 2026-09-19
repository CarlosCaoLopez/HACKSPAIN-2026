"""La cámara automática: a dónde mira el director según lo que lee del journal.

Sin RCON ni Paper: se instancia el `Director`, se le dan eventos sintéticos por
`aplicar()` y se comprueba el nombre del plano que devuelve `elegir()`. Cubre los
nueve hitos del track de la demo y la regresión (sin eventos clave, manda el
fuego / la unidad / el valle, como antes)."""

from pathlib import Path

import pytest

from sim.director import Director

ESCENARIO = Path("scenarios/wildfire_ridge.yaml")


@pytest.fixture
def director() -> Director:
    return Director(ESCENARIO, "p")


def _dar(d: Director, tipo: str, payload: dict) -> None:
    d.aplicar({"type": tipo, "payload": payload})


def _celda_ardiendo(d: Director, cell_id: str) -> None:
    _dar(d, "world.cell.changed", {"cell_id": cell_id, "state": "burning"})


# --- focos puntuales (hitos 5, 6, 8, 9) ---


def test_llamada_saliente_encuadra_el_pueblo(director):
    """Hito 5: la llamada de evacuación lleva la cámara al POI de esa tarea."""
    _dar(director, "call.requested", {"task_id": "task_evac_a", "poi_id": "poi_pueblo_a"})
    _dar(director, "call.started", {"task_id": "task_evac_a", "direction": "outbound"})
    nombre, _ = director.elegir(urgente=True)
    assert nombre == "llamada a poi_pueblo_a"


def test_replan_de_demo_encuadra_el_valle(director):
    """Hito 6: el replan por divergencia (viento) o restricción dura abre al valle."""
    _dar(director, "plan.replan.started",
         {"reason": "divergencia 0.45 > 0.25", "trigger": "divergence"})
    nombre, _ = director.elegir(urgente=True)
    assert nombre == "el valle · replan"


def test_replan_rutinario_no_agarra_la_camara(director):
    """El retasking (`tasks_changed`) salta decenas de veces: se narra pero NO fija
    foco, para no dejar la cámara pegada al valle. Con fuego activo, manda el frente."""
    for cid in ("cell_13_11", "cell_14_11", "cell_13_12", "cell_14_12"):
        _celda_ardiendo(director, cid)
    nota = director.aplicar(
        {"type": "plan.replan.started",
         "payload": {"reason": "tareas: task_front_13_11", "trigger": "tasks_changed"}}
    )
    assert nota is not None and not nota[0].isupper()  # minúscula → no urgente
    nombre, _ = director.elegir(urgente=False)
    assert nombre == "el frente de fuego"


def test_llamada_entrante_sin_ubicacion_abre_al_valle(director):
    """Hito 8: el vecino llama pero no sabe dónde está; no hay a qué apuntar."""
    _dar(director, "call.started", {"call_id": "c1", "direction": "inbound"})
    nombre, _ = director.elegir(urgente=True)
    assert nombre == "el valle · llamada entrante"


def test_ubicacion_del_vecino_encuadra_el_pin(director):
    """Hito 9: el pin de Telegram lleva la cámara al punto exacto."""
    _dar(director, "citizen.location", {"x": 187.0, "z": 94.0, "poi_name": "Pueblo B"})
    nombre, _ = director.elegir(urgente=True)
    assert nombre.startswith("vecino")


def test_foco_manda_sobre_el_fuego(director):
    """El foco puntual pisa las heurísticas mientras dura, aunque arda medio valle."""
    for cid in ("cell_13_11", "cell_14_11", "cell_13_12", "cell_14_12", "cell_15_11"):
        _celda_ardiendo(director, cid)
    _dar(director, "plan.replan.started", {"reason": "x", "trigger": "hard_violation"})
    nombre, _ = director.elegir(urgente=True)
    assert nombre == "el valle · replan"


# --- heurísticas de fondo (hitos 4, 7) y regresión ---


def test_sigue_a_la_unidad_recien_ordenada(director):
    """Hito 7: tras un goto, la cámara sigue a esa unidad, no a `min(unit_id)`."""
    _dar(director, "world.unit.status", {"unit_id": "unit_truck1", "status": "moving"})
    _dar(director, "world.unit.position", {"unit_id": "unit_truck1", "x": 0.0, "z": 0.0})
    _dar(director, "world.unit.status", {"unit_id": "unit_truck2", "status": "moving"})
    _dar(director, "world.unit.position", {"unit_id": "unit_truck2", "x": 50.0, "z": 0.0})
    _dar(
        director,
        "action.requested",
        {"verb": "goto", "args": {"unit_id": "unit_truck2", "route": ["wp_pueblo_b"]}},
    )
    nombre, _ = director.elegir(urgente=False)
    assert nombre == "siguiendo a unit_truck2"


def test_sin_eventos_clave_manda_el_valle(director):
    """Regresión: nada se mueve ni arde → plano general del valle."""
    nombre, _ = director.elegir(urgente=False)
    assert nombre == "el valle"


def test_fuego_significativo_sin_foco_va_al_frente(director):
    """Regresión: >3 celdas ardiendo y sin foco → el frente de fuego."""
    for cid in ("cell_13_11", "cell_14_11", "cell_13_12", "cell_14_12"):
        _celda_ardiendo(director, cid)
    nombre, _ = director.elegir(urgente=False)
    assert nombre == "el frente de fuego"


# --- despacho por teléfono: la unidad que espera, y el corte al colgar ---
#
# El core retiene a una unidad desde que pide su llamada hasta que la dotación
# cuelga (`core.loop.DISPATCH_HOLD_S`). Durante esos ~30 s lo que hay que enseñar
# no es a dónde va, sino que NO se mueve. Secuencia real medida en
# `runs/run_726b7bab939a.jsonl`: call.requested(t=1) → call.started → …30 s… →
# call.ended → goto [CONFIRMADO].


def _pedir_reten(d: Director, unidad: str = "unit_truck1") -> None:
    _dar(
        d,
        "call.requested",
        {
            "task_id": "task_front_13_11",
            "poi_id": "poi_base",
            "facts": {"role": "fire_crew", "unit_id": unidad},
        },
    )
    _dar(
        d,
        "call.started",
        {"task_id": "task_front_13_11", "direction": "outbound"},
    )


def test_la_llamada_de_despacho_encuadra_la_unidad_retenida(director):
    """El retén: la cámara va al camión parado, no al POI ni al fuego."""
    for cell in ("cell_13_11", "cell_14_11", "cell_13_12", "cell_14_12"):
        _celda_ardiendo(director, cell)
    _pedir_reten(director)

    nombre, _ = director.elegir(urgente=False)
    assert nombre == "unit_truck1 espera al teléfono"


def test_la_ambulancia_se_encuadra_donde_esta_no_donde_va(director):
    """La llamada de la ambulancia lleva el POI del RESCATE (Pueblo A), pero la
    unidad sigue parada en el hospital: se encuadra el hospital."""
    _dar(
        director,
        "call.requested",
        {
            "task_id": "task_rescue_poi_pueblo_a",
            "poi_id": "poi_pueblo_a",
            "facts": {"role": "ambulance", "unit_id": "unit_ambulance"},
        },
    )
    _dar(
        director,
        "call.started",
        {"task_id": "task_rescue_poi_pueblo_a", "direction": "outbound"},
    )
    _, (x, _y, z, _yaw, _pitch) = director.elegir(urgente=False)

    # `unit_ambulance` arranca en (22, -77), encima del hospital; Pueblo A está en
    # (187, 11). El plano tiene que caer cerca de la ambulancia, no del pueblo.
    assert abs(x - 22) < 40 and abs(z + 77) < 40, (x, z)


def test_el_plano_de_la_llamada_no_caduca_a_los_doce_segundos(monkeypatch):
    """La avería original: `FOCO_S`=12 s y la llamada dura 30, así que la cámara se
    iba al fuego con los camiones todavía parados. Ahora el foco espera a colgar."""
    import sim.director as mod

    reloj = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: reloj["t"])
    d = Director(ESCENARIO, "p")
    for cell in ("cell_13_11", "cell_14_11", "cell_13_12", "cell_14_12"):
        _celda_ardiendo(d, cell)
    _pedir_reten(d)

    reloj["t"] += mod.FOCO_S + 5.0  # pasados los doce segundos de antes
    nombre, _ = d.elegir(urgente=False)
    assert nombre == "unit_truck1 espera al teléfono"


def test_al_colgar_la_camara_sigue_a_la_unidad_que_arranca(director):
    """El pago de los treinta segundos de espera: cuelgan y los camiones salen."""
    _pedir_reten(director)
    _dar(director, "call.ended", {"task_id": "task_front_13_11", "outcome": "answered"})
    _dar(director, "world.unit.status", {"unit_id": "unit_truck1", "status": "moving"})
    _dar(
        director, "world.unit.position", {"unit_id": "unit_truck1", "x": -40.0, "z": 0.0}
    )
    _dar(
        director,
        "action.requested",
        {"verb": "goto", "args": {"unit_id": "unit_truck1", "route": ["wp_cruce"]}},
    )

    nombre, _ = director.elegir(urgente=True)
    assert nombre == "siguiendo a unit_truck1"


def test_colgar_una_llamada_de_despacho_es_urgente(director):
    """`_quizas_mover` trata como urgente lo que empieza en mayúscula: sin eso, el
    corte se lo come el `HOLD_S` de nueve segundos y no se ve arrancar a nadie."""
    _pedir_reten(director)
    nota = director.aplicar(
        {"type": "call.ended", "payload": {"task_id": "task_front_13_11"}}
    )
    assert nota and nota[0].isupper(), nota


def test_el_valle_del_director_es_el_mismo_que_el_de_las_teclas():
    """Un encuadre, una implementación. Antes este salía a y=204 y el de `sim.camera`
    a y=175, por encima del techo de niebla que camera.py documenta como medido."""
    from sim.camera import shots
    from sim.scenario import load

    d = Director(ESCENARIO, "p")
    v = shots(load(ESCENARIO))["valle"]
    assert d._valle() == (v.x, v.y, v.z, v.yaw, v.pitch)
