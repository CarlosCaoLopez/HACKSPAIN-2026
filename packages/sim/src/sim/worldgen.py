"""Arranque idempotente del mundo. Unos 4 segundos, y lo vais a ejecutar 200 veces.

La secuencia fija:
1. `/gamerule doFireTick false` y `/gamerule randomTickSpeed 0` — el fuego lo
   movemos nosotros, nunca Minecraft.
2. `/time set 6000`, `/weather clear`, `/gamerule doDaylightCycle false`.
3. Por cada aldeano: `/summon villager` con `NoAI:1b` y `Tags:["vela","civ",...]`.
4. Por cada unidad: `/summon armor_stand` con `ShowArms:1` y cabeza de bloque
   distinta por rol.
5. Marcadores de POI con `/summon block_display`.
"""

from contracts.scenario import Scenario
from sim.rcon import RconClient

VELA_TAG = "vela"
"""Todo lo que invocamos lleva este tag: `/kill @e[tag=vela]` y vuelve a lanzarse."""


async def build(scenario: Scenario, rcon: RconClient) -> None:
    """Deja el mundo listo. Llama a `teardown` primero: es idempotente."""
    raise NotImplementedError


async def teardown(rcon: RconClient) -> None:
    """`/kill @e[tag=vela]`. No toca el mapa construido a mano."""
    raise NotImplementedError


def gamerule_commands() -> list[str]:
    """Los gamerules e iluminación fijos, para que la grabación sea igual en el
    ensayo y en el escenario."""
    raise NotImplementedError
