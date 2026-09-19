"""Las posiciones de cámara del pitch. P2 mueve la cámara durante la demo.

El backbone pide tres, guardadas como macro: **vista cenital del valle, plano del
pueblo A, plano del frente**. Aquí se calculan desde la geometría del escenario en
vez de escribirse a mano, así que si el valle cambia de tamaño los encuadres
siguen encuadrando.

Se usan con un cliente en modo espectador en la segunda pantalla, capturado por
OBS. Nada de `prismarine-viewer`.

    uv run python -m sim.camera --list
    uv run python -m sim.camera valle
"""

import argparse
import asyncio
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from contracts.scenario import Scenario
from contracts.settings import settings
from sim.hazard import parse_cell
from sim.rcon import HIGH, LOW, Rcon, RconClient
from sim.scenario import load
from sim.worldgen import GROUND_Y

FOV_VERTICAL = 70.0
"""El de Minecraft por defecto. Si alguien lo toca en Opciones, los encuadres
cenitales dejan de cuadrar: en el ensayo, que nadie lo toque."""

ASPECT = 16 / 9
"""Proporción de la pantalla. El FOV de Minecraft es vertical, así que a lo ancho
se ve bastante más — y el valle es más ancho que profundo."""

MAX_CENITAL_Y = 175
"""Techo del plano cenital. Medido en el servidor: por encima de ~170 la niebla
de Minecraft lava los colores y los marcadores de POI llegan grises, que es
justo lo que el plano tiene que distinguir. Si el valle no cabe por debajo de
este techo, el valle es demasiado grande — y eso se arregla en el YAML, no
subiendo la cámara."""


@dataclass(frozen=True)
class Shot:
    """Un encuadre. `yaw` 0 es sur y crece hacia el oeste; `pitch` 90 mira al suelo."""

    name: str
    description: str
    x: float
    y: float
    z: float
    yaw: float
    pitch: float

    def tp(self, who: str = "@s") -> str:
        return (
            f"tp {who} {self.x:.0f} {self.y:.0f} {self.z:.0f} "
            f"{self.yaw:.0f} {self.pitch:.0f}"
        )


def height_to_frame(width: float, depth: float) -> float:
    """Altura a la que una cenital abarca ese rectángulo, con margen del 10%.

    Se calcula por los dos lados y manda el que obligue a subir más: encuadrar
    solo por la profundidad dejaba la base y el pueblo fuera de plano.
    """
    half = math.tan(math.radians(FOV_VERTICAL / 2))
    return 1.1 * max(depth / (2 * half), width / (2 * half * ASPECT))


def shots(scenario: Scenario) -> dict[str, Shot]:
    """Los tres del backbone, más el escorzo que se ve mejor que el cenital."""
    # La cenital encuadra **el corredor de carreteras**, no todos los POIs: el
    # hospital y el refugio cuelgan lejos de la Y y estirarían el plano hasta la
    # niebla. Lo que el jurado tiene que leer ahí es la bifurcación.
    rxs = [w.x for w in scenario.waypoints]
    rzs = [w.z for w in scenario.waypoints]
    cx, cz = (min(rxs) + max(rxs)) / 2, (min(rzs) + max(rzs)) / 2

    pueblo = next((p for p in scenario.pois if p.kind == "village"), scenario.pois[0])
    fx, fz = parse_cell(scenario.hazard.origin_cell)
    fx, fz = fx * scenario.hazard.cell_size, fz * scenario.hazard.cell_size

    return {
        s.name: s
        for s in [
            Shot(
                "valle",
                "cenital del valle · explica el mecanismo y la Y",
                cx,
                min(
                    GROUND_Y + height_to_frame(max(rxs) - min(rxs), max(rzs) - min(rzs)),
                    MAX_CENITAL_Y,
                ),
                cz,
                -90,
                90,
            ),
            Shot(
                "escorzo",
                "el valle a 45° desde el oeste · se lee mejor que el cenital",
                min(rxs) - 55,
                GROUND_Y + 48,
                cz + 75,
                -50,
                34,
            ),
            Shot(
                "pueblo",
                f"plano de {pueblo.name} · la escala y los civiles",
                pueblo.x - 26,
                GROUND_Y + 11,
                pueblo.z + 26,
                -45,
                16,
            ),
            Shot(
                "frente",
                "el frente de fuego a ras · las llamas, no la mancha",
                fx - 17,
                GROUND_Y + 7,
                fz + 17,
                -45,
                8,
            ),
        ]
    }


async def move(shot: Shot, rcon: Rcon, who: str) -> None:
    answer = await rcon.send(shot.tp(who), HIGH)
    if "No player was found" in answer or "No entity was found" in answer:
        # El `tp` falla en silencio si el jugador no está: por RCON solo vuelve el
        # texto. Sin esto, en el pitch se pulsa una tecla y no pasa nada.
        print(f"  ! {who} no está conectado: {answer.strip()}", flush=True)


PLAYER_POLL_S = 2.0
"""Cada cuánto se mira si el jugador ya ha entrado."""


async def wait_for_player(rcon: Rcon, player: str) -> None:
    """Espera a que el jugador esté en el servidor, avisando por pantalla.

    La cámara se suele lanzar antes de que el cliente termine de entrar; con un
    error seco habría que relanzarla, y el `tp` a un jugador ausente no falla,
    solo no hace nada. Ctrl-C sale.
    """
    avisado = False
    while True:
        answer = await rcon.send(f"execute if entity {player}", LOW)
        if answer.startswith("Test passed"):
            if avisado:
                print(f"  {player} ha entrado.", flush=True)
            return
        if not avisado:
            print(
                f"  esperando a que {player} entre en localhost:25565 "
                "(cualquier nombre vale: online-mode=false)…",
                flush=True,
            )
            avisado = True
        await asyncio.sleep(PLAYER_POLL_S)


SLOT_POLL_S = 0.15
"""Cada cuánto se pregunta por la ranura. Por debajo se nota instantáneo y son
siete consultas por segundo por el carril lento: no estorba a nada."""


async def follow(
    catalogue: dict[str, Shot], player: str, gamemode: str = "spectator"
) -> None:
    """El macro de verdad: **1-4 dentro del juego** mueven la cámara.

    Un macro que escucha el teclado de la terminal obliga a tenerla enfocada, y
    entonces las teclas no llegan a Minecraft. Aquí es al revés: el juego recibe
    el 1-4 como cambio de ranura del inventario, y el servidor lo lee por RCON.

    En espectador no hay mano ni HUD, que es como tiene que verse la grabación. Si
    en ese modo el cliente no manda el cambio de ranura, se cae a creativo, donde
    **F1** oculta HUD y mano igual de bien.
    """
    rcon = RconClient(settings.rcon_host, settings.rcon_port, settings.rcon_password)
    await rcon.connect()
    await wait_for_player(rcon, player)
    shots_by_slot = dict(enumerate(catalogue.values()))

    print(f"CÁMARA · sigue a {player} · Ctrl-C para salir")
    print("dentro del juego: modo creativo, F1 oculta el HUD, y pulsa:\n")
    for slot, shot in shots_by_slot.items():
        print(f"  [{slot + 1}]  {shot.name:<9} {shot.description}")
    print()

    if gamemode:
        await rcon.send(f"gamemode {gamemode} {player}", HIGH)
    last: int | None = None
    try:
        while True:
            answer = await rcon.send(f"data get entity {player} SelectedItemSlot", LOW)
            found = re.search(r"data:\s*(\d+)|following entity data:\s*(\d+)", answer)
            slot = int(next(g for g in found.groups() if g)) if found else None
            if slot is not None and slot != last and slot in shots_by_slot:
                shot = shots_by_slot[slot]
                await rcon.send(shot.tp(player), HIGH)
                print(f"  → {shot.name}", flush=True)
                last = slot
            elif slot is not None:
                last = slot
            await asyncio.sleep(SLOT_POLL_S)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await rcon.close()


async def live(catalogue: dict[str, Shot], who: str) -> None:
    """El macro del pitch: una tecla, un encuadre.

    Esta terminal enfocada en el portátil, Minecraft en la segunda pantalla — que
    es como lo plantea el backbone. El juego sigue renderizando sin foco, así que
    OBS no se entera de que escribes en otro sitio.

    Va con el jugador en **espectador**: sin mano, sin HUD, y atraviesa bloques.
    La alternativa de leer las teclas desde dentro del juego no existe: lo único
    que el servidor puede leer por RCON es la ranura del inventario, y en
    espectador el cliente no la manda. En creativo sí, pero entonces se ve la mano
    salvo que pulses F1.
    """
    import termios
    import tty

    if not sys.stdin.isatty():
        # Sin terminal no hay teclas que leer: mejor decirlo que morir con un
        # `termios.error` críptico cuando alguien lo lanza con `nohup` o desde un script.
        raise SystemExit(
            "--live necesita una terminal de verdad (lee las teclas de stdin); "
            "para mandar un encuadre suelto: python -m sim.camera valle --who <jugador>"
        )

    keys = dict(zip("1234", catalogue.values(), strict=False))
    rcon = RconClient(settings.rcon_host, settings.rcon_port, settings.rcon_password)
    await rcon.connect()
    if who not in ("@a", "@s"):
        await wait_for_player(rcon, who)
        await rcon.send(f"gamemode spectator {who}", HIGH)

    print("CÁMARA EN DIRECTO · esta terminal enfocada · q para salir\n")
    for key, shot in keys.items():
        print(f"  [{key}]  {shot.name:<9} {shot.description}")
    print()

    stdin = sys.stdin.fileno()
    previous = termios.tcgetattr(stdin)
    loop = asyncio.get_running_loop()
    try:
        tty.setcbreak(stdin)
        while True:
            key = await loop.run_in_executor(None, sys.stdin.read, 1)
            if key in ("q", "\x03", "\x04"):
                break
            shot = keys.get(key)
            if shot is None:
                continue
            await move(shot, rcon, who)
            print(f"  → {shot.name}", flush=True)
    finally:
        termios.tcsetattr(stdin, termios.TCSADRAIN, previous)
        await rcon.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Mueve la cámara del pitch.")
    parser.add_argument("shot", nargs="?", help="valle | escorzo | pueblo | frente")
    parser.add_argument("--scenario", default="scenarios/wildfire_ridge.yaml")
    parser.add_argument("--who", default="@a", help="jugador; @a por defecto")
    parser.add_argument("--list", action="store_true", help="lista los encuadres")
    parser.add_argument(
        "--live",
        action="store_true",
        help="macro por teclado de la terminal (necesita que la terminal tenga foco)",
    )
    parser.add_argument(
        "--follow",
        metavar="JUGADOR",
        help="macro dentro del juego: 1-4 del inventario mueven la cámara",
    )
    parser.add_argument(
        "--gamemode",
        default="spectator",
        help="modo al que se pone al jugador: spectator (sin mano) o creative",
    )
    args = parser.parse_args()

    catalogue = shots(load(Path(args.scenario)))
    if args.follow:
        asyncio.run(follow(catalogue, args.follow, args.gamemode))
        return
    if args.live:
        asyncio.run(live(catalogue, args.who))
        return
    if args.list or not args.shot:
        for shot in catalogue.values():
            print(f"  {shot.name:<9} {shot.description}")
            print(f"            {shot.tp('@s')}")
        return
    if args.shot not in catalogue:
        raise SystemExit(f"no existe {args.shot!r}; hay {sorted(catalogue)}")

    async def run() -> None:
        rcon = RconClient(settings.rcon_host, settings.rcon_port, settings.rcon_password)
        await rcon.connect()
        await move(catalogue[args.shot], rcon, args.who)
        await rcon.close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
