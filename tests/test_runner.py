"""El tick loop y los cuatro verbos. Contra `FakeRcon` y sin bus: ni Paper, ni core.

Lo que se comprueba aquí es la cadena entera de P2: una orden del core entra por
`execute`, el camión se mueve, el incendio avanza, un inject corta la carretera y
la unidad reencamina sola.
"""

from pathlib import Path

import pytest

from contracts.events import EventType
from sim import runner as runner_mod
from sim.rcon import HIGH, LOW, FakeRcon
from sim.runner import DEFAULT_SPEED_MPS, Sim

ESCENARIO = Path("scenarios/wildfire_ridge.yaml")


@pytest.fixture
def sim():
    runner_mod._FALLBACK.clear()
    return Sim(ESCENARIO, FakeRcon())


def eventos(tipo: EventType) -> list[dict]:
    return [e.payload for e in runner_mod._FALLBACK if e.type == tipo]


# --- los cuatro verbos, y solo cuatro ---


async def test_un_verbo_inventado_no_tumba_el_sim(sim):
    await sim.execute("act_1", "despegar", {})
    assert eventos(EventType.ACTION_FAILED) == [
        {"action_id": "act_1", "error": "unknown_verb"}
    ]


@pytest.mark.parametrize("verbo", ["goto", "set_marker", "announce", "rescue"])
async def test_los_cuatro_verbos_existen(sim, verbo):
    assert hasattr(sim, f"_do_{verbo}")


async def test_goto_pone_la_unidad_en_movimiento(sim):
    await sim.execute(
        "act_go", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_pueblo_a"}
    )
    assert sim.units["unit_truck1"].status == "moving"
    assert "wp_sur_01" in sim._moving["unit_truck1"][0].route, "debe ir por el sur"


async def test_goto_llega_y_confirma(sim):
    await sim.execute(
        "act_go", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_pueblo_a"}
    )
    # Los ticks salen del recorrido y la velocidad, no de un número a ojo: así
    # afinar `DEFAULT_SPEED_MPS` para la demo no rompe este test.
    viaje = sim._moving["unit_truck1"][0]
    for _ in range(int(viaje.total_m / DEFAULT_SPEED_MPS) + 5):
        await sim.tick(1.0)
        if not sim._moving:
            break
    assert eventos(EventType.WORLD_UNIT_ARRIVED)[0]["waypoint_id"] == "wp_pueblo_a"
    assert {"action_id": "act_go", "result": {"waypoint_id": "wp_pueblo_a"}} in eventos(
        EventType.ACTION_COMPLETED
    )
    assert sim.units["unit_truck1"].status == "idle"


async def test_goto_a_una_unidad_desconocida_falla_claro(sim):
    await sim.execute(
        "act_x", "goto", {"unit_id": "unit_ovni", "waypoint_id": "wp_pueblo_a"}
    )
    assert eventos(EventType.ACTION_FAILED)[0]["error"] == "unknown_unit:unit_ovni"


async def test_un_goto_nuevo_cancela_el_anterior(sim):
    """D2: last-write-wins. Sin avisar, el core espera para siempre un
    `action.completed` que no va a llegar."""
    await sim.execute(
        "act_1", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_pueblo_a"}
    )
    await sim.execute(
        "act_2", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_hospital"}
    )
    assert {"action_id": "act_1", "error": "superseded"} in eventos(
        EventType.ACTION_FAILED
    )
    assert sim._moving["unit_truck1"][1] == "act_2"


async def test_announce_y_set_marker_confirman(sim):
    await sim.execute("act_a", "announce", {"text": "Evacuad Pueblo A"})
    await sim.execute(
        "act_m", "set_marker", {"poi_id": "poi_pueblo_a", "state": "danger"}
    )
    hechos = [e["action_id"] for e in eventos(EventType.ACTION_COMPLETED)]
    assert {"act_a", "act_m"} <= set(hechos)
    assert any("red_concrete" in c for _, c in sim.rcon.commands)


async def test_rescue_pone_a_salvo_a_los_civiles(sim):
    await sim.execute(
        "act_r", "rescue", {"civ_ids": ["civ_pueblo_a"], "shelter_id": "poi_refugio"}
    )
    assert sim.civilians["civ_pueblo_a"].state == "safe"
    assert eventos(EventType.WORLD_CIVILIANS_CHANGED)[0]["state"] == "safe"


# --- el tick ---


async def test_cada_tick_publica_world_tick(sim):
    await sim.tick(1.0)
    await sim.tick(1.0)
    assert [e["t_sim"] for e in eventos(EventType.WORLD_TICK)] == [1.0, 2.0]


async def test_el_incendio_avanza_solo(sim):
    for _ in range(60):
        await sim.tick(1.0)
    assert len(eventos(EventType.WORLD_CELL_CHANGED)) > 5
    assert any("netherrack" in c for _, c in sim.rcon.commands)


async def test_la_posicion_sale_a_1_hz_no_a_5(sim):
    await sim.execute(
        "act_go", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_cruce"}
    )
    await sim.tick(1.0)
    assert len(eventos(EventType.WORLD_UNIT_POSITION)) == 1
    tps = [c for p, c in sim.rcon.commands if c.startswith("tp ") and p == HIGH]
    assert len(tps) == 5, "cinco /tp por segundo, un evento por segundo"


async def test_el_render_del_fuego_no_adelanta_al_movimiento(sim):
    """D7: el `/tp` del replan va en `high`, el incendio en `low`."""
    await sim.execute(
        "act_go", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_pueblo_a"}
    )
    for _ in range(30):
        await sim.tick(1.0)
    carriles = {c.split()[0]: p for p, c in sim.rcon.commands}
    assert carriles.get("tp") == HIGH
    assert carriles.get("fill") == LOW


# --- injects ---


async def test_el_inject_de_viento_cambia_el_incendio(sim):
    await sim.inject("wind_shift", {"bearing": 90, "speed": 2.0})
    assert sim.hazard.wind.bearing_deg == 90
    assert eventos(EventType.WORLD_INJECT)[0]["inject_type"] == "wind_shift"


async def test_cortar_la_carretera_reencamina_al_camion(sim):
    """El clímax de la demo, en una prueba: el camión va por el sur, se corta, y
    toma el norte sin que nadie se lo diga."""
    await sim.execute(
        "act_go", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_pueblo_a"}
    )
    assert "wp_sur_01" in sim._moving["unit_truck1"][0].route

    await sim.inject("road_cut", {"edge": "wp_sur_01-wp_sur_02", "cause": "árbol caído"})

    ruta = sim._moving["unit_truck1"][0].route
    assert "wp_nor_01" in ruta, f"debería desviarse al norte, fue por {ruta}"
    assert eventos(EventType.WORLD_ROAD_CHANGED)[0]["cut"] is True


async def test_una_averia_para_la_unidad_y_avisa(sim):
    await sim.execute(
        "act_go", "goto", {"unit_id": "unit_truck2", "waypoint_id": "wp_pueblo_a"}
    )
    await sim.inject("unit_failure", {"unit": "unit_truck2", "reason": "avería"})
    assert sim.units["unit_truck2"].status == "unavailable"
    assert any(
        e["error"].startswith("unit_failure") for e in eventos(EventType.ACTION_FAILED)
    )
    assert "unit_truck2" not in sim._moving


async def test_no_se_manda_una_unidad_averiada(sim):
    await sim.inject("unit_failure", {"unit": "unit_truck2", "reason": "avería"})
    await sim.execute(
        "act_go", "goto", {"unit_id": "unit_truck2", "waypoint_id": "wp_pueblo_a"}
    )
    assert eventos(EventType.ACTION_FAILED)[-1]["error"] == (
        "unit_unavailable:unit_truck2"
    )


async def test_los_injects_del_yaml_saltan_a_su_hora(sim):
    for _ in range(215):
        await sim.tick(1.0)
    tipos = [e["inject_type"] for e in eventos(EventType.WORLD_INJECT)]
    assert tipos == ["wind_shift", "road_cut"], "240 aún no ha llegado"


# --- snapshot ---


async def test_snapshot_sirve_para_depurar(sim):
    await sim.execute(
        "act_go", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_pueblo_a"}
    )
    await sim.tick(1.0)
    snap = sim.snapshot()
    assert snap["t_sim"] == 1.0
    assert "unit_truck1" in snap["moving"]
    assert snap["run_id"].startswith("run_")


# --- la costura con el core: `core.loop` manda la ruta ya resuelta ---


async def test_goto_acepta_la_ruta_que_manda_el_core(sim):
    """`core.loop` emite args={"unit_id", "route"}; `Assignment.route` está
    documentado como "ya resuelta". Sin esto, cada goto del core moría con
    KeyError y en la integración no se movía una sola unidad."""
    await sim.execute(
        "act_go",
        "goto",
        {
            "unit_id": "unit_truck1",
            "route": ["wp_base", "wp_cruce", "wp_nor_01", "wp_nor_02", "wp_pueblo_a"],
        },
    )
    assert sim.units["unit_truck1"].status == "moving"
    assert sim._moving["unit_truck1"][0].route[-1] == "wp_pueblo_a"
    assert "wp_nor_01" in sim._moving["unit_truck1"][0].route, "respeta la del core"


async def test_la_ruta_del_core_se_engancha_donde_esté_la_unidad(sim):
    """El plan puede venir calculado desde otro punto: se antepone el trecho que
    falta en vez de teletransportar la unidad al inicio de la ruta."""
    await sim.execute(
        "a1", "goto", {"unit_id": "unit_truck1", "waypoint_id": "wp_hospital"}
    )
    for _ in range(30):
        await sim.tick(1.0)
    await sim.execute(
        "a2", "goto", {"unit_id": "unit_truck1", "route": ["wp_sur_02", "wp_pueblo_a"]}
    )
    ruta = sim._moving["unit_truck1"][0].route
    assert ruta[-1] == "wp_pueblo_a"
    assert ruta[0] != "wp_sur_02", "tiene que llegar primero hasta la ruta del plan"


async def test_un_waypoint_inventado_en_la_ruta_falla_claro(sim):
    await sim.execute(
        "a1", "goto", {"unit_id": "unit_truck1", "route": ["wp_base", "wp_narnia"]}
    )
    assert eventos(EventType.ACTION_FAILED)[-1]["error"] == "unknown_waypoint:wp_narnia"


async def test_goto_sin_destino_ni_ruta_falla(sim):
    await sim.execute("a1", "goto", {"unit_id": "unit_truck1"})
    assert eventos(EventType.ACTION_FAILED)[-1]["error"] == "goto_sin_destino"


async def test_el_corte_emite_siempre_el_id_canonico(sim):
    """Quien corta puede nombrar la carretera por sus extremos, pero el evento
    lleva el id de siempre: si no, el dashboard ve dos `edge_id` para la misma
    carretera según quién la cortó."""
    await sim.inject("road_cut", {"edge": "wp_sur_01-wp_sur_02", "cause": "árbol"})
    assert eventos(EventType.WORLD_ROAD_CHANGED)[-1]["edge_id"] == (
        "road:wp_sur_01-wp_sur_02"
    )


async def test_cortar_una_carretera_que_no_existe_no_miente(sim):
    """Un id que no casa no puede pasar por un corte efectivo."""
    await sim.inject("road_cut", {"edge": "wp_a-wp_b", "cause": "x"})
    ev = eventos(EventType.WORLD_ROAD_CHANGED)[-1]
    assert ev["cut"] is False and "desconocida" in ev["cause"]


async def test_cortar_una_carretera_se_ve_en_el_mundo(sim):
    """El clímax necesita imagen: el jurado ve al camión girar, y tiene que ver
    también por qué. Sin esto el motivo solo existe en el dashboard."""
    await sim.inject("road_cut", {"edge": "wp_sur_01-wp_sur_02", "cause": "árbol caído"})
    valla = [
        c for _, c in sim.rcon.commands if "black_concrete" in c or "yellow_concrete" in c
    ]
    assert valla, "el tramo cortado tiene que repintarse a franjas"
    assert any("oak_log" in c for _, c in sim.rcon.commands), "y el árbol caído"


async def test_el_repintado_va_por_el_carril_lento(sim):
    """D7: es decorado; no puede adelantar al `/tp` del replan."""
    await sim.inject("road_cut", {"edge": "wp_sur_01-wp_sur_02", "cause": "x"})
    assert all(p == LOW for p, c in sim.rcon.commands if "black_concrete" in c)


# --- lo que el sim deduce por su cuenta, sin que el core se lo pida ---


async def test_un_poi_amenazado_se_pinta_de_rojo(sim):
    """Que un pueblo esté en peligro es geometría, no una decisión: el sim ya sabe
    qué arde y dónde están los POIs. Sin esto el mapa se queda muerto mientras el
    core no pida `set_marker`, y hoy el core no lo pide nunca."""
    cerca = sim._pois["poi_pueblo_a"]
    sim.hazard._state[sim.hazard_cell_at(cerca.x, cerca.z)] = "burning"
    await sim._update_markers()
    assert sim._marker_state["poi_pueblo_a"] == "danger"
    assert any("red_concrete" in c for _, c in sim.rcon.commands)


async def test_el_marcador_no_se_repinta_cada_tick(sim):
    """Cinco `fill` por tick compitiendo con el movimiento, para nada."""
    await sim._update_markers()
    antes = len(sim.rcon.commands)
    await sim._update_markers()
    assert len(sim.rcon.commands) == antes


async def test_pinta_un_corte_que_no_ha_disparado_el(sim):
    """El corte de la demo lo deduce el core de una llamada, no un inject del
    YAML. Por ese camino el sim solo se entera si escucha el evento."""
    await sim.apply_road_change("wp_sur_01-wp_sur_02", True, "por la llamada")
    assert sim.graph.is_cut("wp_sur_01-wp_sur_02")
    assert any("black_concrete" in c for _, c in sim.rcon.commands)


async def test_aplicar_dos_veces_el_mismo_corte_no_hace_nada(sim):
    """Recibe sus propios eventos: repetir no puede costar."""
    await sim.apply_road_change("wp_sur_01-wp_sur_02", True, "x")
    antes = len(sim.rcon.commands)
    await sim.apply_road_change("wp_sur_01-wp_sur_02", True, "x")
    assert len(sim.rcon.commands) == antes


async def test_start_conecta_el_rcon(sim):
    """Quien construye el cliente no lo conecta —`connect` reintenta con backoff y
    un puerto muerto bloquearía el arranque del run—, así que le toca a `Sim`, que
    es quien tiene el ciclo de vida. Sin esto el primer comando del worldgen muere
    con "RconClient sin conectar" y la task del sim se cae dos segundos después de
    arrancar mientras el resto del sistema sigue en pie: pasó en el ensayo del
    sábado y el gateway estuvo ocho minutos corriendo en vacío."""
    assert sim.rcon.connected is False
    await sim.start()
    assert sim.rcon.connected is True, "el worldgen habría muerto al primer comando"
    await sim.stop()


async def test_set_speed_acelera_sin_tocar_el_tiempo_del_dominio(sim):
    """El gateway lo llama con `--speed`. Sin este método degradaba a 1× y los doce
    runs del domingo serían 72 minutos en vez de siete. `t_sim` no se entera: un
    tick sigue siendo un segundo simulado y el journal sale idéntico."""
    sim.set_speed(10.0)
    assert sim.speed == 10.0
    await sim.tick(1.0)
    assert sim.t_sim == 1.0, "acelerar el reloj de pared no cambia el del dominio"


async def test_una_velocidad_no_positiva_falla(sim):
    with pytest.raises(ValueError, match="velocidad no positiva"):
        sim.set_speed(0)


async def test_una_evacuacion_camina_por_la_carretera_que_le_dijeron(sim):
    """Con `route`, el pueblo ANDA: se le ve avanzar por esa carretera y llegar es lo
    que le pone `safe`. Antes cualquier `rescue` era un `/tp` instantáneo al refugio,
    así que el agente le decía al vecino «salgan por la pista sur» y en el mundo
    aparecían de golpe en otro sitio."""
    grupo = "civ_pueblo_a"
    ruta = ["wp_pueblo_a", "wp_sur_02", "wp_sur_01"]
    await sim.execute(
        "act_walk",
        "rescue",
        {"civ_ids": [grupo], "shelter_id": "poi_pueblo_b", "route": ruta},
    )

    # Salen andando, no a salvo: mientras caminan siguen expuestos.
    assert sim.civilians[grupo].state == "evacuating"
    assert grupo in sim._walking
    assert [e["state"] for e in eventos(EventType.WORLD_CIVILIANS_CHANGED)] == [
        "evacuating"
    ]
    assert not eventos(EventType.ACTION_COMPLETED), "no ha llegado nadie todavía"

    # Y avanzan por la ruta, no aparecen en el destino.
    movimiento = sim._walking[grupo][0]
    largo = movimiento.total_m
    await sim.tick(1.0)
    recorrido = movimiento.total_m - (largo - movimiento._travelled)
    assert 0 < movimiento._travelled < largo, "ni quieto ni teletransportado"

    # Hasta que llegan: entonces sí, `safe` y en el destino.
    for _ in range(int(largo / runner_mod.WALK_SPEED_MPS) + 2):
        await sim.tick(1.0)
    assert sim.civilians[grupo].state == "safe"
    assert sim.civilians[grupo].poi_id == "poi_pueblo_b"
    assert grupo not in sim._walking
    completadas = eventos(EventType.ACTION_COMPLETED)
    assert [c["result"]["rescued"] for c in completadas] == [[grupo]]
    assert completadas[0]["action_id"] == "act_walk"
