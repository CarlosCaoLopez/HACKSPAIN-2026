"""Arranque idempotente del mundo. Unos segundos, y lo vais a ejecutar 200 veces.

**El mapa no se construye a mano: se construye desde el YAML.** Es la decisión que
convierte el mundo en código versionable en texto, quita `infra/server/world/` de
git (D10) y hace que la cicatriz del incendio se borre regenerando (D11).

La secuencia fija:
1. `forceload` del área: sin jugadores no hay chunks cargados y los selectores no
   encuentran nada (D12).
2. `doFireTick false` y `randomTickSpeed 0` — el fuego lo movemos nosotros, nunca
   Minecraft.
3. `time set 6000`, `weather clear`, `doDaylightCycle false`.
4. El suelo del valle, la cresta que separa los dos pueblos y las carreteras.
5. Por cada POI, su edificio y su marcador de color.
6. Por cada unidad, un armor stand con `Tags:["vela", <unit_id>]`.
7. Por cada grupo de civiles, aldeanos con `NoAI:1b`.
"""

import math
import random

from contracts.scenario import Scenario
from contracts.world import POI, Unit
from sim.rcon import LOW, Rcon

VELA_TAG = "vela"
"""Todo lo que invocamos lleva este tag: `kill @e[tag=vela]` y vuelve a lanzarse."""

GROUND_Y = 64
"""El valle es plano. Un terreno con relieve obligaría a un heightmap por celda y
a que `movement` interpolara también en y; para lo que la demo necesita, no paga."""

FOUNDATION_Y = 40
"""Hasta dónde baja el cimiento. Sin él la meseta queda al aire allí donde el
terreno original era más bajo —o era mar— y el valle se ve flotando."""

RIDGE_Y = 73
"""La cresta que separa los dos pueblos. Alta para que se lea desde arriba y para
que justifique que la carretera la rodee en vez de cruzarla, pero no tanto como
para taparlos: con el valle a 301 bloques, un muro de 14 se comía media vista."""

MARGIN = 58
"""Bloques alrededor de lo más extremo del escenario. La franja exterior se
convierte en las laderas que cierran el valle."""

RIM = 30
"""Franja de borde sin vegetación. Ya no lleva cerros: el mundo base es superplano
a la misma altura que el valle, así que no hay costura que tapar. Un anillo de
`fill` concéntricos se leía como bancales de cultivo y dejaba un corte recto donde
terminaba — peor el remedio que la enfermedad.

La regla que decide qué puede tener relieve sigue en pie: **solo lo que el
simulador nunca pinta**. Carreteras, plataformas de POI y celdas de incendio se
renderizan a una altura fija, así que un cerro ahí dejaría el fuego enterrado."""

TREES = ["oak", "oak", "birch", "spruce", "oak_bees_0002"]
"""Features de vanilla: árboles de verdad, no cajas de `fill`."""

ROAD_BLOCK = "gray_concrete"
ROAD_WIDTH = 3
GROUND_BLOCK = "grass_block"
RIDGE_BLOCK = "stone"

POI_STYLE: dict[str, tuple[str, str]] = {
    # kind -> (bloque del edificio, concreto del marcador)
    "village": ("white_terracotta", "yellow_concrete"),
    "hospital": ("white_concrete", "red_concrete"),
    "shelter": ("oak_planks", "lime_concrete"),
    "base": ("bricks", "blue_concrete"),
    "landmark": ("stone_bricks", "light_gray_concrete"),
}
UNIT_HEAD = {
    "fire_truck": "red_concrete",
    "ambulance": "white_concrete",
    "drone": "light_blue_concrete",
    "crew": "orange_concrete",
}


async def build(scenario: Scenario, rcon: Rcon) -> None:
    """Deja el mundo listo. Llama a `teardown` primero: es idempotente."""
    await teardown(rcon, scenario)
    await rcon.send_many(forceload_commands(scenario), LOW)
    await rcon.send_many(gamerule_commands(), LOW)
    await rcon.send_many(terrain_commands(scenario), LOW)
    await rcon.send_many(road_commands(scenario), LOW)
    await rcon.send_many(scenery_commands(scenario), LOW)
    for poi in scenario.pois:
        await rcon.send_many(poi_commands(poi), LOW)
    for unit in scenario.units:
        await rcon.send_many(unit_commands(unit), LOW)
    await rcon.send_many(civilian_commands(scenario), LOW)


async def teardown(rcon: Rcon, scenario: Scenario | None = None) -> None:
    """`kill @e[tag=vela]` **y borrar la cicatriz del incendio**.

    D11: matar entidades no basta. El fuego se pinta con bloques —`netherrack`,
    `fire`, `coal_block`— y sobrevive a cualquier `kill`. Sin esto, el run 2
    empieza sobre las cenizas del run 1 y los doce runs del domingo dejan de ser
    comparables.
    """
    await rcon.send(f"kill @e[tag={VELA_TAG}]", LOW)
    if scenario is not None:
        await rcon.send_many(scar_commands(scenario), LOW)


MAX_FILL_BLOCKS = 32768
"""Límite de `/fill` por comando en vanilla. Por encima lo rechaza, y desde RCON
el error se pierde: el mundo sale a medias sin que nada falle."""

SCAR_BLOCKS = {"netherrack": GROUND_BLOCK, "coal_block": GROUND_BLOCK, "fire": "air"}
"""Lo que pinta el hazard y con qué se deshace."""

SCAR_Y = (GROUND_Y - 2, RIDGE_Y + 4)
"""Franja vertical que puede haber tocado el fuego, con holgura."""


def scar_commands(scenario: Scenario) -> list[str]:
    """`fill ... replace` por losas, respetando el límite de bloques por comando."""
    x1, z1, x2, z2 = bounds(scenario)
    out = []
    for block, replacement in SCAR_BLOCKS.items():
        out += tiled_fill(
            x1, SCAR_Y[0], z1, x2, SCAR_Y[1], z2, replacement, f" replace {block}"
        )
    return out


def gamerule_commands() -> list[str]:
    """Los gamerules e iluminación fijos, para que la grabación sea igual en el
    ensayo y en el escenario."""
    return [
        "gamerule doFireTick false",
        "gamerule randomTickSpeed 0",
        "gamerule doDaylightCycle false",
        "gamerule doWeatherCycle false",
        "gamerule doMobSpawning false",
        "gamerule mobGriefing false",
        "gamerule announceAdvancements false",
        "time set 6000",
        "weather clear",
    ]


def bounds(scenario: Scenario) -> tuple[int, int, int, int]:
    """(x1, z1, x2, z2) del valle, con margen alrededor de todo el escenario."""
    xs = [w.x for w in scenario.waypoints] + [p.x for p in scenario.pois]
    zs = [w.z for w in scenario.waypoints] + [p.z for p in scenario.pois]
    return (
        int(min(xs)) - MARGIN, int(min(zs)) - MARGIN,
        int(max(xs)) + MARGIN, int(max(zs)) + MARGIN,
    )


def forceload_commands(scenario: Scenario) -> list[str]:
    """D12. En trozos, porque el límite es de 256 chunks por comando."""
    x1, z1, x2, z2 = bounds(scenario)
    step = 16 * 15  # 15x15 chunks por comando, holgado bajo el límite
    out = ["forceload remove all"]
    for x in range(x1, x2 + 1, step):
        for z in range(z1, z2 + 1, step):
            out.append(
                f"forceload add {x} {z} {min(x + step - 1, x2)} {min(z + step - 1, z2)}"
            )
    return out


def tiled_fill(
    x1: int, y1: int, z1: int, x2: int, y2: int, z2: int, block: str, extra: str = ""
) -> list[str]:
    """`fill` troceado para no pasar de `MAX_FILL_BLOCKS`.

    Vanilla rechaza en silencio —desde RCON, con un error que nadie lee— cualquier
    `fill` de más de 32768 bloques. El valle entero son 72541 solo en la capa de
    hierba, así que sin trocear **el terreno no se construye** y lo que queda es
    el mundo natural con las carreteras pintadas encima.
    """
    height = max(y2 - y1 + 1, 1)
    side = max(int((MAX_FILL_BLOCKS / height) ** 0.5), 1)
    out = []
    for x in range(x1, x2 + 1, side):
        for z in range(z1, z2 + 1, side):
            out.append(
                f"fill {x} {y1} {z} "
                + f"{min(x + side - 1, x2)} {y2} {min(z + side - 1, z2)} {block}{extra}"
            )
    return out


def terrain_commands(scenario: Scenario) -> list[str]:
    """El valle plano, el aire por encima, y la cresta entre los dos pueblos."""
    x1, z1, x2, z2 = bounds(scenario)
    out = tiled_fill(x1, FOUNDATION_Y, z1, x2, GROUND_Y - 1, z2, "stone")
    out += tiled_fill(x1, GROUND_Y, z1, x2, GROUND_Y, z2, GROUND_BLOCK)
    out += tiled_fill(x1, GROUND_Y + 1, z1, x2, RIDGE_Y + 8, z2, "air")
    out.extend(_ridge(scenario))
    return out


def scenery_commands(scenario: Scenario) -> list[str]:
    """Árboles y flores del propio Minecraft, lejos de carreteras y POIs.

    `place feature` genera vegetación vanilla: variada y creíble, imposible de
    imitar con `fill`. Va sembrado por `scenario.seed`, así que el valle es el
    mismo en los doce runs del domingo.
    """
    x1, z1, x2, z2 = bounds(scenario)
    rng = random.Random(scenario.seed + 1)
    ocupado = [(w.x, w.z) for w in scenario.waypoints] + [(p.x, p.z) for p in scenario.pois]
    roads = {(w.id): (w.x, w.z) for w in scenario.waypoints}

    out = []
    for _ in range(420):
        x = rng.randint(x1 + RIM, x2 - RIM)
        z = rng.randint(z1 + RIM, z2 - RIM)
        if any(abs(x - px) < 26 and abs(z - pz) < 26 for px, pz in ocupado):
            continue
        if _near_road(x, z, scenario, roads):
            continue
        feature = rng.choice(TREES) if rng.random() < 0.72 else "flower_default"
        out.append(f"place feature minecraft:{feature} {x} {GROUND_Y + 1} {z}")
    return out


def _near_road(x: int, z: int, scenario: Scenario, roads: dict) -> bool:
    """Distancia punto-segmento contra cada carretera. Un árbol en mitad del
    asfalto tapa justo lo que el jurado tiene que ver moverse."""
    for road in scenario.roads:
        (ax, az), (bx, bz) = roads[road.a], roads[road.b]
        dx, dz = bx - ax, bz - az
        length2 = dx * dx + dz * dz or 1
        t = max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / length2))
        if math.dist((x, z), (ax + dx * t, az + dz * t)) < 14:
            return True
    return False


def _ridge(scenario: Scenario) -> list[str]:
    """La cresta entre los dos pueblos, como perfil por columnas.

    Una caja de `fill` escalonados se lee como zigurat. Aquí cada columna saca su
    altura de un coseno a lo ancho por otro a lo largo, más ruido sembrado, que es
    lo que rompe la simetría y la hace pasar por terreno.
    """
    villages = [p for p in scenario.pois if p.kind == "village"]
    if len(villages) < 2:
        return []
    a, b = sorted(villages, key=lambda p: p.z)[:2]
    middle = int((a.z + b.z) / 2)
    x1, _, x2, _ = bounds(scenario)
    west, east = int((x1 + min(a.x, b.x)) / 2), x2 - RIM
    half, rise = 30, RIDGE_Y - GROUND_Y
    rng = random.Random(scenario.seed + 2)
    step = 3

    out: list[str] = []
    for x in range(west, east + 1, step):
        along = math.sin(math.pi * (x - west) / max(east - west, 1))
        for z in range(middle - half, middle + half + 1, step):
            across = math.cos(math.pi * (z - middle) / (2 * half))
            height = int(rise * along * across ** 1.7 + rng.uniform(-1.2, 1.2))
            if height < 1:
                continue
            top = GROUND_Y + height
            out.append(
                f"fill {x} {GROUND_Y + 1} {z} "
                + f"{x + step - 1} {top - 1} {z + step - 1} {RIDGE_BLOCK}"
            )
            out.append(
                f"fill {x} {top} {z} {x + step - 1} {top} {z + step - 1} {GROUND_BLOCK}"
            )
    return out


def road_commands(scenario: Scenario) -> list[str]:
    """Cada carretera, como una franja de concreto entre sus dos waypoints."""
    positions = {w.id: (w.x, w.z) for w in scenario.waypoints}
    out = []
    for road in scenario.roads:
        out.extend(_strip(positions[road.a], positions[road.b]))
    return out


def _strip(a: tuple[float, float], b: tuple[float, float]) -> list[str]:
    """Rasteriza el segmento a bloques. Un `fill` por paso: feo de contar, pero
    son cien comandos y salen en menos de un segundo."""
    (x1, z1), (x2, z2) = a, b
    steps = max(int(max(abs(x2 - x1), abs(z2 - z1))), 1)
    half = ROAD_WIDTH // 2
    out = []
    for i in range(steps + 1):
        x = round(x1 + (x2 - x1) * i / steps)
        z = round(z1 + (z2 - z1) * i / steps)
        out.append(
            f"fill {x - half} {GROUND_Y} {z - half} "
            + f"{x + half} {GROUND_Y} {z + half} {ROAD_BLOCK}"
        )
    return out


def poi_commands(poi: POI) -> list[str]:
    """Un edificio sencillo y un marcador de color plano, que es lo que se lee
    desde la vista cenital del pitch."""
    building, marker = POI_STYLE.get(poi.kind, POI_STYLE["landmark"])
    x, z = int(poi.x), int(poi.z)
    size, height = (10, 6) if poi.kind == "village" else (7, 5)
    return [
        # plataforma de color: el marcador que cambia con `set_marker`
        f"fill {x - size - 2} {GROUND_Y} {z - size - 2} "
        + f"{x + size + 2} {GROUND_Y} {z + size + 2} {marker}",
        # el edificio, hueco
        f"fill {x - size} {GROUND_Y + 1} {z - size} "
        + f"{x + size} {GROUND_Y + height} {z + size} {building}",
        f"fill {x - size + 1} {GROUND_Y + 1} {z - size + 1} "
        + f"{x + size - 1} {GROUND_Y + height - 1} {z + size - 1} air",
        # un faro de luz para localizarlo de noche y en la grabación
        f"setblock {x} {GROUND_Y + height + 1} {z} sea_lantern",
        # cartel flotante: se lee a distancia y a través de bloques. Es lo que
        # hace el mapa navegable, y en la grabación identifica cada sitio sin voz.
        f"summon armor_stand {x} {GROUND_Y + height + 3} {z} "
        + f'{{Tags:["{VELA_TAG}","label"],Marker:1b,Invisible:1b,NoGravity:1b,'
        + f'CustomNameVisible:1b,CustomName:\'{{"text":"{poi.name}"}}\'}}',
    ]


def unit_commands(unit: Unit) -> list[str]:
    """Armor stand con cabeza de bloque distinta por rol, y el tag que usa `/tp`."""
    head = UNIT_HEAD.get(unit.kind, "stone")
    x, z = int(unit.x), int(unit.z)
    nbt = (
        f'{{Tags:["{VELA_TAG}","{unit.id}"],ShowArms:1b,NoGravity:1b,'
        f'CustomNameVisible:1b,CustomName:\'{{"text":"{unit.id}"}}\','
        + f'ArmorItems:[{{}},{{}},{{}},{{id:"minecraft:{head}",count:1}}]}}'
    )
    return [f"summon armor_stand {x} {GROUND_Y + 1} {z} {nbt}"]


def civilian_commands(scenario: Scenario) -> list[str]:
    """Aldeanos con `NoAI:1b`: son un indicador visual, no simulación."""
    pois = {p.id: p for p in scenario.pois}
    out = []
    for group in scenario.civilians:
        poi = pois[group.poi_id]
        for n in range(min(group.count, 8)):  # ocho bastan para leerlo; 24 arrastran
            x = int(poi.x) + (n % 4) * 3 - 5
            z = int(poi.z) + (n // 4) * 3 + 14
            out.append(
                f"summon villager {x} {GROUND_Y + 1} {z} "
                f'{{Tags:["{VELA_TAG}","{group.id}"],NoAI:1b,NoGravity:1b}}'
            )
    return out
