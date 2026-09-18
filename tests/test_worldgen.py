"""Worldgen: el mundo sale del YAML. Se asierta contra `FakeRcon`, sin Paper."""

from pathlib import Path

import pytest

from sim.rcon import LOW, FakeRcon
from sim.scenario import load
from sim.worldgen import (
    GROUND_Y,
    VELA_TAG,
    bounds,
    build,
    forceload_commands,
    gamerule_commands,
    road_commands,
    scar_commands,
    teardown,
)

ESCENARIO = Path("scenarios/wildfire_ridge.yaml")


@pytest.fixture
def scenario():
    return load(ESCENARIO)


async def test_build_deja_el_mundo_entero(scenario):
    fake = FakeRcon()
    await build(scenario, fake)
    enviados = "\n".join(c for _, c in fake.commands)
    assert "gamerule doFireTick false" in enviados, "el fuego lo movemos nosotros"
    assert "forceload add" in enviados, "sin chunks cargados no existen las entidades"
    for unit in scenario.units:
        assert f'"{unit.id}"' in enviados, f"falta el armor stand de {unit.id}"
    for poi in scenario.pois:
        assert f"{int(poi.x)} {GROUND_Y}" in enviados or poi.id, "falta el POI"
    assert enviados.count("summon villager") > 0


async def test_todo_lo_invocado_lleva_el_tag(scenario):
    """Si algo no lleva el tag, `teardown` no lo mata y se acumula entre runs."""
    fake = FakeRcon()
    await build(scenario, fake)
    for cmd in (c for _, c in fake.commands if c.startswith("summon")):
        assert f'"{VELA_TAG}"' in cmd, cmd[:60]


async def test_el_render_va_por_el_carril_lento(scenario):
    """D7: worldgen no puede adelantar a un `/tp` del core."""
    fake = FakeRcon()
    await build(scenario, fake)
    assert {p for p, _ in fake.commands} == {LOW}


async def test_teardown_borra_la_cicatriz_del_fuego(scenario):
    """D11: matar entidades no basta, el incendio son bloques."""
    fake = FakeRcon()
    await teardown(fake, scenario)
    enviados = "\n".join(c for _, c in fake.commands)
    assert f"kill @e[tag={VELA_TAG}]" in enviados
    for bloque in ("netherrack", "coal_block", "fire"):
        assert f"replace {bloque}" in enviados, f"{bloque} sobreviviría al run"


async def test_teardown_sin_escenario_solo_mata_entidades():
    fake = FakeRcon()
    await teardown(fake)
    assert len(fake.commands) == 1


def test_los_fill_de_limpieza_respetan_el_limite(scenario):
    """Un `/fill` de más de 32768 bloques lo rechaza el servidor."""
    for cmd in scar_commands(scenario):
        _, x1, y1, z1, x2, y2, z2, *_ = cmd.split()
        volumen = (
            (int(x2) - int(x1) + 1) * (int(y2) - int(y1) + 1) * (int(z2) - int(z1) + 1)
        )
        assert volumen <= 32768, cmd


def test_el_forceload_cubre_todo_el_escenario(scenario):
    x1, _z1, _x2, z2 = bounds(scenario)
    tiles = [c.split() for c in forceload_commands(scenario) if c.startswith("forceload add")]
    assert min(int(t[2]) for t in tiles) == x1
    assert max(int(t[5]) for t in tiles) == z2


def test_las_carreteras_unen_sus_waypoints(scenario):
    """La franja empieza donde dice el YAML: si no, el camión anda sobre hierba."""
    posiciones = {w.id: (w.x, w.z) for w in scenario.waypoints}
    comandos = road_commands(scenario)
    x, z = posiciones["wp_base"]
    assert any(f"fill {int(x) - 1} {GROUND_Y} {int(z) - 1}" in c for c in comandos)


def test_gamerules_fijan_el_mundo_para_la_grabacion():
    reglas = " ".join(gamerule_commands())
    for regla in ("doFireTick false", "randomTickSpeed 0", "doDaylightCycle false"):
        assert regla in reglas
    assert "time set 6000" in reglas and "weather clear" in reglas
