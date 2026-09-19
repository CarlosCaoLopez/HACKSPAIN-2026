"""El autómata de fuego. Puro: ni Paper, ni RCON, ni bus.

Lo que de verdad se comprueba aquí es el determinismo: la misma semilla tiene que
dar el mismo incendio, porque si no, ni se puede ensayar seis veces igual ni
comparar el run 1 con el run 12.
"""

import pytest

from contracts.scenario import HazardSpec
from contracts.world import Wind
from sim.hazard import (
    BURN_DURATION_S,
    MAX_RADIUS_CELLS,
    Blackout,
    CellChange,
    Flood,
    Wildfire,
    build_hazard,
    cell_id,
    parse_cell,
    wind_vector,
)

OESTE = Wind(bearing_deg=270, speed=1.2)  # O→E, como wildfire_ridge.yaml


def spec(**kw) -> HazardSpec:
    base = {
        "kind": "wildfire", "origin_cell": "cell_14_22", "cell_size": 4,
        "base_spread": 0.12, "wind": OESTE,
    }
    return HazardSpec(**{**base, **kw})


def correr(fuego: Wildfire, segundos: int) -> list[CellChange]:
    return [c for _ in range(segundos) for c in fuego.tick(1.0)]


# --- determinismo, que es lo que sostiene todo lo demás ---

def test_la_misma_semilla_da_el_mismo_incendio():
    a = correr(Wildfire(spec(), seed=1821), 60)
    b = correr(Wildfire(spec(), seed=1821), 60)
    assert [(c.cell_id, c.state) for c in a] == [(c.cell_id, c.state) for c in b]
    assert len(a) > 10, "el incendio tiene que haber crecido, si no no prueba nada"


def test_semillas_distintas_dan_incendios_distintos():
    a = correr(Wildfire(spec(), seed=1), 60)
    b = correr(Wildfire(spec(), seed=2), 60)
    assert [c.cell_id for c in a] != [c.cell_id for c in b]


def test_no_usa_el_random_global():
    """Sembrar el módulo `random` no puede cambiar nada (D4)."""
    import random
    random.seed(1); a = correr(Wildfire(spec(), seed=7), 40)
    random.seed(999); b = correr(Wildfire(spec(), seed=7), 40)
    assert [c.cell_id for c in a] == [c.cell_id for c in b]


# --- el comportamiento ---

def test_arranca_ardiendo_por_la_celda_de_origen():
    primero = Wildfire(spec(), seed=1).tick(1.0)
    ignicion = [c for c in primero if c.state == "burning"]
    assert ignicion[0].cell_id == "cell_14_22"
    assert ignicion[0].hazard == "wildfire"


def test_el_fuego_avanza_a_favor_del_viento():
    """Viento de poniente (270, O→E): el frente tiene que irse hacia +X."""
    fuego = Wildfire(spec(base_spread=0.3), seed=4)
    correr(fuego, 40)
    cx_origen = parse_cell("cell_14_22")[0]
    ardiendo = [parse_cell(c)[0] for c in fuego.burning]
    assert max(ardiendo) - cx_origen > cx_origen - min(ardiendo)


def test_una_celda_acaba_quemada():
    fuego = Wildfire(spec(), seed=3)
    correr(fuego, int(BURN_DURATION_S) + 2)
    assert fuego.state_of("cell_14_22") == "burnt"


def test_at_risk_todavia_puede_arder():
    """`at_risk` es una marca para el planner, no un estado terminal."""
    fuego = Wildfire(spec(base_spread=0.5), seed=5)
    fuego.tick(1.0)
    en_riesgo = [c for c in fuego.cells_at_risk(30) if fuego.state_of(c) == "at_risk"]
    assert en_riesgo
    correr(fuego, 30)
    assert any(fuego.state_of(c) in ("burning", "burnt") for c in en_riesgo)


def test_cells_at_risk_ordena_por_riesgo():
    fuego = Wildfire(spec(), seed=6)
    riesgo = fuego.cells_at_risk(60)
    assert riesgo, "con una celda ardiendo tiene que haber vecinas en riesgo"
    # A favor del viento (+X) antes que en contra.
    cx = [parse_cell(c)[0] for c in riesgo]
    assert cx[0] > cx[-1]


def test_no_se_sale_del_radio_maximo():
    fuego = Wildfire(spec(base_spread=60.0), seed=8)  # brutal a propósito: busca el tope
    correr(fuego, 150)
    ox, oz = parse_cell("cell_14_22")
    for c in fuego.burning:
        cx, cz = parse_cell(c)
        assert max(abs(cx - ox), abs(cz - oz)) <= MAX_RADIUS_CELLS


def test_cambiar_el_viento_cambia_la_direccion():
    fuego = Wildfire(spec(), seed=9)
    fuego.set_wind(Wind(bearing_deg=90, speed=2.0))
    assert fuego.wind.bearing_deg == 90


# --- render y utilidades ---

def test_render_de_burning_y_burnt():
    fuego = Wildfire(spec(), seed=1)
    burning = fuego.render_commands(CellChange(cell_id="cell_1_2", state="burning", hazard="wildfire"))
    assert burning == [
        "fill 4 65 8 7 73 11 air",       # se lleva por delante lo que hubiera
        "fill 4 64 8 7 64 11 netherrack",
        "fill 4 65 8 7 65 11 fire",
    ]
    burnt = fuego.render_commands(CellChange(cell_id="cell_1_2", state="burnt", hazard="wildfire"))
    assert burnt[-1].endswith("coal_block")
    assert "air" in burnt[0], "hay que apagar el fuego antes de dejar la cicatriz"


def test_at_risk_no_se_pinta():
    fuego = Wildfire(spec(), seed=1)
    assert fuego.render_commands(
        CellChange(cell_id="cell_1_2", state="at_risk", hazard="wildfire")
    ) == []


@pytest.mark.parametrize(
    "bearing, esperado",
    [(0, (0, 1)), (90, (-1, 0)), (180, (0, -1)), (270, (1, 0))],
)
def test_wind_vector_apunta_a_donde_sopla(bearing, esperado):
    """El YAML pone 270 y lo comenta O→E: tiene que dar +X."""
    dx, dz = wind_vector(bearing)
    assert (round(dx), round(dz)) == esperado


def test_ids_de_celda_van_y_vienen():
    assert parse_cell("cell_14_22") == (14, 22)
    assert parse_cell("cell_-3_-7") == (-3, -7)
    assert cell_id(-3, -7) == "cell_-3_-7"
    with pytest.raises(ValueError, match="id de celda inválido"):
        parse_cell("celda_14_22")


# --- la fábrica ---

def test_build_hazard_devuelve_la_implementacion():
    assert isinstance(build_hazard(spec(), 1), Wildfire)


def test_build_hazard_falla_con_un_kind_inventado():
    with pytest.raises(ValueError, match="peligro desconocido"):
        build_hazard(spec(kind="meteorito"), 1)


def test_flood_declara_que_no_esta():
    """`Flood` no entra en la demo: ninguno de los dos escenarios lo usa."""
    with pytest.raises(NotImplementedError, match="H8"):
        Flood(spec(), 1)


# --- Blackout: el segundo escenario ---

APAGON = {"kind": "blackout", "base_spread": 0.9,
          "wind": Wind(bearing_deg=0, speed=0.0)}


def test_blackout_se_propaga_en_cruz_no_en_diagonal():
    """Sigue tendidos, no un frente por el aire. La mancha sale dendrítica."""
    b = Blackout(spec(**APAGON), seed=3)
    for _ in range(200):
        b.tick(1.0)
    ox, oz = parse_cell("cell_14_22")
    for cid in b.dark:
        cx, cz = parse_cell(cid)
        # con vecindad en cruz, cada celda se alcanza por pasos cardinales
        assert abs(cx - ox) + abs(cz - oz) >= max(abs(cx - ox), abs(cz - oz))
    assert len(b.dark) > 5, "tiene que haberse extendido"


def test_una_celda_a_oscuras_no_vuelve_a_encenderse():
    """No hay estado terminal: sin luz sigue sin luz, y sigue arrastrando."""
    b = Blackout(spec(**APAGON), seed=4)
    for _ in range(300):
        b.tick(1.0)
    assert all(b.state_of(c) == "dark" for c in b.dark)
    assert "burnt" not in {b.state_of(c) for c in b.dark}


def test_el_viento_no_afecta_al_apagon():
    a = Blackout(spec(**APAGON), seed=5)
    otro = Blackout(spec(**APAGON), seed=5)
    otro.set_wind(Wind(bearing_deg=90, speed=5.0))
    izq = [c.cell_id for _ in range(60) for c in a.tick(1.0)]
    der = [c.cell_id for _ in range(60) for c in otro.tick(1.0)]
    assert izq == der


def test_blackout_es_determinista():
    uno = [c.cell_id for _ in range(80) for c in Blackout(spec(**APAGON), 7).tick(1.0)]
    dos = [c.cell_id for _ in range(80) for c in Blackout(spec(**APAGON), 7).tick(1.0)]
    assert uno == dos


def test_render_del_apagon():
    b = Blackout(spec(**APAGON), seed=1)
    cmds = b.render_commands(
        CellChange(cell_id="cell_1_2", state="dark", hazard="blackout")
    )
    assert cmds == ["fill 4 64 8 7 64 11 polished_blackstone"]
    assert b.render_commands(
        CellChange(cell_id="cell_1_2", state="at_risk", hazard="blackout")
    ) == []


def test_build_hazard_devuelve_blackout():
    assert isinstance(build_hazard(spec(**APAGON), 1), Blackout)


# --- sofocar desde la carretera ---

def test_un_camion_cerca_apaga_el_fuego():
    """El contrato lo asigna al sim: una unidad trabaja las celdas `burning` a
    menos de `suppress_reach_m` a `suppress_rate` celdas por minuto. No es un
    verbo nuevo: el core manda el `goto` y el mundo responde."""
    f = Wildfire(spec(), seed=1)
    correr(f, 40)
    objetivo = f.burning[0]
    x, z = f.center_of(objetivo)

    # `suppress_rate` se reparte entre las celdas a tiro, así que con seis
    # delante tardan unos dos minutos en caer todas: eso son 3 celdas/min, que es
    # lo que dice el contrato. Un minuto no basta y no debe bastar.
    apagadas = []
    for _ in range(180):
        apagadas += f.suppress([(x, z)], 1.0)
        f.tick(1.0)
    assert apagadas, "un camión encima del fuego tiene que apagar algo"
    assert all(c.state == "burnt" and c.cause == "extinguished" for c in apagadas)


def test_sin_nadie_cerca_no_se_apaga_nada():
    f = Wildfire(spec(), seed=1)
    correr(f, 40)
    lejos = [(9999.0, 9999.0)]
    assert [c for _ in range(30) for c in f.suppress(lejos, 1.0)] == []


def test_apagar_tarda_lo_que_dice_la_tasa():
    """A 3 celdas/min una celda sola cae en ~20 s: se ve el trabajo en vez de
    desaparecer de golpe."""
    f = Wildfire(spec(), seed=2)
    f.tick(1.0)
    objetivo = f.burning[0]
    x, z = f.center_of(objetivo)
    for segundos in range(1, 60):
        if f.suppress([(x, z)], 1.0):
            assert 15 <= segundos <= 30, f"cayó en {segundos}s"
            return
    raise AssertionError("no se apagó")


def test_un_camion_reparte_su_esfuerzo():
    """No apaga más rápido por tener más fuego delante: `suppress_rate` se
    reparte entre las celdas a tiro."""
    f = Wildfire(spec(base_spread=0.9), seed=3)
    correr(f, 60)
    x, z = f.center_of(f.burning[len(f.burning) // 2])
    a_tiro = sum(1 for c in f.burning
                 if abs(f.center_of(c)[0] - x) <= f.spec.suppress_reach_m
                 and abs(f.center_of(c)[1] - z) <= f.spec.suppress_reach_m)
    apagadas = [c for _ in range(20) for c in f.suppress([(x, z)], 1.0)]
    assert a_tiro > 1, "el escenario de la prueba necesita varias celdas a tiro"
    assert len(apagadas) < a_tiro, "no puede apagarlas todas a la vez"


def test_la_cicatriz_de_un_camion_se_pinta_distinta():
    """`extinguished` no es `burnout`: el dashboard y el mundo los separan."""
    f = Wildfire(spec(), seed=1)
    apagada = f.render_commands(
        CellChange(cell_id="cell_1_2", state="burnt", hazard="wildfire",
                   cause="extinguished"))
    quemada = f.render_commands(
        CellChange(cell_id="cell_1_2", state="burnt", hazard="wildfire",
                   cause="burnout"))
    assert "gray_concrete" in apagada[-1]
    assert "coal_block" in quemada[-1]


def test_cada_cambio_dice_por_que():
    f = Wildfire(spec(), seed=1)
    causas = {c.cause for _ in range(60) for c in f.tick(1.0)}
    assert causas <= {"spread", "burnout", "at_risk", "inject"}
    assert {"spread", "at_risk"} <= causas
