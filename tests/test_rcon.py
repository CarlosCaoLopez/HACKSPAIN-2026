"""RCON: la prioridad de D7 y el doble de D1. Ninguno necesita Paper levantado."""

import asyncio

import pytest

from sim.rcon import HIGH, LOW, FakeRcon, RconClient, RconError


async def test_fake_rcon_apunta_los_comandos():
    fake = FakeRcon()
    await fake.connect()
    await fake.send("tp @e[tag=unit_truck1] 10 64 20 90 0")
    await fake.send("fill 0 64 0 3 64 3 netherrack", LOW)
    assert fake.connected
    assert fake.sent("tp") == ["tp @e[tag=unit_truck1] 10 64 20 90 0"]
    assert fake.commands[1][0] == LOW


async def test_send_sin_conectar_falla_claro():
    with pytest.raises(RconError, match="sin conectar"):
        await RconClient("localhost", 25575, "x").send("list")


async def test_el_carril_high_adelanta_al_render():
    """D7: el `/tp` del replan no espera detrás de la ráfaga de `/fill` del fuego."""
    client = RconClient("localhost", 25575, "x")
    client._queues = {HIGH: asyncio.Queue(), LOW: asyncio.Queue()}
    loop = asyncio.get_running_loop()

    for i in range(5):  # el frente de fuego, encolado primero
        client._queues[LOW].put_nowait((f"fill {i}", loop.create_future(), 60.0))
    client._queues[HIGH].put_nowait(("tp truck1", loop.create_future(), 5.0))

    orden = [(await client._next())[0] for _ in range(6)]
    assert orden[0] == "tp truck1", "el movimiento tiene que salir primero"
    assert orden[1:] == [f"fill {i}" for i in range(5)], "el render conserva su orden"


async def test_next_espera_sin_quemar_cpu_y_no_pierde_comandos():
    client = RconClient("localhost", 25575, "x")
    client._queues = {HIGH: asyncio.Queue(), LOW: asyncio.Queue()}
    loop = asyncio.get_running_loop()

    pendiente = asyncio.ensure_future(client._next())
    await asyncio.sleep(0)
    client._queues[LOW].put_nowait(("fill tardío", loop.create_future(), 60.0))
    assert (await asyncio.wait_for(pendiente, 1))[0] == "fill tardío"
    assert client._queues[LOW].empty()
