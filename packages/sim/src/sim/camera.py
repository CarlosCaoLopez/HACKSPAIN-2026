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
from dataclasses import dataclass
from pathlib import Path

from contracts.scenario import Scenario
from contracts.settings import settings
from sim.hazard import parse_cell
from sim.rcon import HIGH, Rcon, RconClient
from sim.scenario import load
from sim.worldgen import GROUND_Y

FOV_VERTICAL = 70.0
"""El de Minecraft por defecto. Si alguien lo toca en Opciones, los encuadres
cenitales dejan de cuadrar: en el ensayo, que nadie lo toque."""

MAX_CENITAL_Y = 150
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


def height_to_frame(depth: float) -> float:
    """Altura a la que una vista cenital abarca `depth` bloques de norte a sur."""
    return depth / (2 * math.tan(math.radians(FOV_VERTICAL / 2)))


def shots(scenario: Scenario) -> dict[str, Shot]:
    """Los tres del backbone, más el escorzo que se ve mejor que el cenital."""
    xs = [p.x for p in scenario.pois]
    zs = [p.z for p in scenario.pois]
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    depth = max(zs) - min(zs)

    pueblo = next(
        (p for p in scenario.pois if p.kind == "village"), scenario.pois[0]
    )
    fx, fz = parse_cell(scenario.hazard.origin_cell)
    fx, fz = fx * scenario.hazard.cell_size, fz * scenario.hazard.cell_size

    return {
        s.name: s
        for s in [
            Shot(
                "valle", "cenital del valle · explica el mecanismo y la Y",
                cx, min(GROUND_Y + height_to_frame(depth), MAX_CENITAL_Y), cz, -90, 90,
            ),
            Shot(
                "escorzo", "el valle a 45° desde el oeste · se lee mejor que el cenital",
                min(xs) - 120, GROUND_Y + 60, cz + 140, -50, 38,
            ),
            Shot(
                "pueblo", f"plano de {pueblo.name} · la escala y los civiles",
                pueblo.x - 55, GROUND_Y + 22, pueblo.z + 55, -45, 22,
            ),
            Shot(
                "frente", "el frente de fuego a ras · las llamas, no la mancha",
                fx - 45, GROUND_Y + 14, fz + 45, -45, 12,
            ),
        ]
    }


async def move(shot: Shot, rcon: Rcon, who: str) -> None:
    await rcon.send(shot.tp(who), HIGH)


def main() -> None:
    parser = argparse.ArgumentParser(description="Mueve la cámara del pitch.")
    parser.add_argument("shot", nargs="?", help="valle | escorzo | pueblo | frente")
    parser.add_argument("--scenario", default="scenarios/wildfire_ridge.yaml")
    parser.add_argument("--who", default="@a", help="jugador; @a por defecto")
    parser.add_argument("--list", action="store_true", help="lista los encuadres")
    args = parser.parse_args()

    catalogue = shots(load(Path(args.scenario)))
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
