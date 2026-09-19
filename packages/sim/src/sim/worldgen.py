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

import argparse
import asyncio
import math
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from contracts.scenario import Scenario
from contracts.world import POI, Unit
from sim.hazard import MAX_RADIUS_CELLS, parse_cell
from sim.rcon import LOW, PrintRcon, Rcon, RconClient

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
CUT_ROAD_BLOCKS = ("black_concrete", "yellow_concrete")
"""Un tramo cortado se repinta a franjas negras y amarillas, como una valla de
obra. En el clímax el jurado ve al camión dar media vuelta; sin esto no ve **por
qué**, y el motivo solo vive en el dashboard.

No se usa rojo a propósito: en este mapa el rojo ya es el marcador del hospital y
el cuerpo de los camiones. Tres significados para el mismo color, en seis minutos
y con el jurado mirando, es pedir que se confunda."""
ROAD_WIDTH = 3
ROAD_CLEARANCE = 3
"""Bloques a cada lado que la carretera despeja por encima.

Sin esto, cualquier relieve que cruce un trazado lo entierra: la cresta se
construye antes que las carreteras y dejaba la de Pueblo B sepultada, con el
camión circulando por dentro de la roca. Abrir el desmonte es además lo que hace
una carretera de verdad cuando se topa con un cerro."""
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
"""Color por rol. El backbone pide "una cabeza de bloque distinta por rol para que
se distingan a 10 metros"; el color cumple eso, la forma cumple algo más."""

# Cada pieza es (bloque, escala, traslación). Varias piezas comparten posición de
# entidad y se separan por su `transformation`, así que **un solo `/tp` mueve el
# vehículo entero** y `movement.py` no se entera de que ahora son tres entidades.
UNIT_SHAPES: dict[
    str, list[tuple[str, tuple[float, float, float], tuple[float, float, float]]]
] = {
    # Proporciones de camión, no de plancha: un cuerpo de 1 bloque de alto contra
    # 3,4 de largo se lee como una lámina roja tirada en el suelo. Alto ≈ ancho.
    "fire_truck": [
        ("red_concrete", (1.8, 1.6, 3.6), (-0.9, 0.0, -1.8)),  # caja trasera
        ("light_gray_concrete", (1.7, 1.3, 1.3), (-0.85, 1.6, 0.4)),  # cabina
        ("red_concrete", (1.8, 0.35, 1.2), (-0.9, 1.6, -1.8)),  # techo trasero
        ("sea_lantern", (0.6, 0.35, 0.7), (-0.3, 2.9, 0.7)),  # rotativo
    ],
    "ambulance": [
        ("white_concrete", (1.7, 1.7, 3.2), (-0.85, 0.0, -1.6)),
        ("light_gray_concrete", (1.6, 1.1, 1.1), (-0.8, 0.0, 1.6)),
        ("red_concrete", (0.45, 0.45, 1.6), (-0.22, 1.7, -0.8)),
        ("sea_lantern", (0.5, 0.3, 0.5), (-0.25, 1.75, 1.0)),
    ],
    "drone": [
        ("light_blue_concrete", (1.1, 0.8, 1.1), (-0.55, 2.2, -0.55)),
        ("gray_concrete", (2.6, 0.18, 0.25), (-1.3, 2.9, -0.12)),
        ("gray_concrete", (0.25, 0.18, 2.6), (-0.12, 2.9, -1.3)),
    ],
    "crew": [("orange_concrete", (0.8, 1.8, 0.8), (-0.4, 0.0, -0.4))],
}

NO_ROT = "left_rotation:[0f,0f,0f,1f],right_rotation:[0f,0f,0f,1f]"
VIEW_RANGE = 6.0
"""Multiplica la distancia a la que el cliente dibuja la entidad. Con el valor por
defecto un vehículo desaparece a ~60 bloques y el plano cenital sale vacío."""


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


def scar_bounds(scenario: Scenario) -> tuple[int, int, int, int]:
    """El área a limpiar: el valle **y** todo lo que el incendio pueda alcanzar.

    No basta con `bounds`. El hazard se propaga hasta `MAX_RADIUS_CELLS` desde la
    ignición, y ese círculo se sale del rectángulo del escenario: medido en
    `wildfire_ridge`, 52 bloques por el sur. Lo que arde ahí fuera sobrevive al
    `teardown` y reaparece en el run siguiente como una mancha suelta, sin nada
    quemado alrededor — porque el camino que la unía sí se limpió y ella no.
    """
    x1, z1, x2, z2 = bounds(scenario)
    cx, cz = parse_cell(scenario.hazard.origin_cell)
    size = scenario.hazard.cell_size
    r = MAX_RADIUS_CELLS * size + size
    return (
        min(x1, cx * size - r), min(z1, cz * size - r),
        max(x2, cx * size + r), max(z2, cz * size + r),
    )


def scar_commands(scenario: Scenario) -> list[str]:
    """`fill ... replace` por losas, respetando el límite de bloques por comando."""
    x1, z1, x2, z2 = scar_bounds(scenario)
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
        int(min(xs)) - MARGIN,
        int(min(zs)) - MARGIN,
        int(max(xs)) + MARGIN,
        int(max(zs)) + MARGIN,
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
    ocupado = [(w.x, w.z) for w in scenario.waypoints] + [
        (p.x, p.z) for p in scenario.pois
    ]
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


RIDGE_STEP = 3
"""Lado de cada columna de la cresta, en bloques."""


@dataclass(frozen=True)
class RidgeProfile:
    """El relieve de la cresta, columna a columna.

    `tops` va de la esquina `(x, z)` de cada columna de `RIDGE_STEP` bloques a la
    y de su cima; solo las columnas con relieve. `origin` es la esquina de la
    primera columna: las demás se alinean a ella, no a 0, y sin saberlo no se
    puede preguntar por un punto cualquiera.
    """

    origin: tuple[int, int]
    tops: dict[tuple[int, int], int]

    def top_at(self, x: float, z: float) -> int:
        """Y de la cima en ese punto, o `GROUND_Y` si allí el valle es plano."""
        ox, oz = self.origin
        col = (
            ox + (math.floor(x) - ox) // RIDGE_STEP * RIDGE_STEP,
            oz + (math.floor(z) - oz) // RIDGE_STEP * RIDGE_STEP,
        )
        return self.tops.get(col, GROUND_Y)


def ridge_profile(scenario: Scenario) -> RidgeProfile:
    """Calcula el perfil una vez. Es la única fuente del relieve: de aquí salen
    los `fill` de `_ridge` **y** la comprobación de `civilian_commands`, para que
    ningún aldeano aparezca dentro de la roca y muera asfixiado."""
    villages = [p for p in scenario.pois if p.kind == "village"]
    if len(villages) < 2:
        return RidgeProfile((0, 0), {})
    a, b = sorted(villages, key=lambda p: p.z)[:2]
    middle = int((a.z + b.z) / 2)
    x1, _, x2, _ = bounds(scenario)
    west, east = int((x1 + min(a.x, b.x)) / 2), x2 - RIM
    half, rise = 30, RIDGE_Y - GROUND_Y
    rng = random.Random(scenario.seed + 2)

    tops: dict[tuple[int, int], int] = {}
    for x in range(west, east + 1, RIDGE_STEP):
        along = math.sin(math.pi * (x - west) / max(east - west, 1))
        for z in range(middle - half, middle + half + 1, RIDGE_STEP):
            across = math.cos(math.pi * (z - middle) / (2 * half))
            height = int(rise * along * across**1.7 + rng.uniform(-1.2, 1.2))
            if height >= 1:
                tops[(x, z)] = GROUND_Y + height
    return RidgeProfile((west, middle - half), tops)


def _ridge(scenario: Scenario) -> list[str]:
    """La cresta entre los dos pueblos, como perfil por columnas.

    Una caja de `fill` escalonados se lee como zigurat. Aquí cada columna saca su
    altura de un coseno a lo ancho por otro a lo largo, más ruido sembrado, que es
    lo que rompe la simetría y la hace pasar por terreno.
    """
    out: list[str] = []
    for (x, z), top in ridge_profile(scenario).tops.items():
        out.append(
            f"fill {x} {GROUND_Y + 1} {z} "
            + f"{x + RIDGE_STEP - 1} {top - 1} {z + RIDGE_STEP - 1} {RIDGE_BLOCK}"
        )
        out.append(
            f"fill {x} {top} {z} {x + RIDGE_STEP - 1} {top} {z + RIDGE_STEP - 1} "
            + f"{GROUND_BLOCK}"
        )
    return out


def road_commands(scenario: Scenario) -> list[str]:
    """Cada carretera, como una franja de concreto entre sus dos waypoints."""
    positions = {w.id: (w.x, w.z) for w in scenario.waypoints}
    out = []
    for road in scenario.roads:
        out.extend(_strip(positions[road.a], positions[road.b]))
    return out


def _strip(
    a: tuple[float, float], b: tuple[float, float], block: str = ROAD_BLOCK
) -> list[str]:
    """Rasteriza el segmento a bloques. Un `fill` por paso: feo de contar, pero
    son cien comandos y salen en menos de un segundo."""
    (x1, z1), (x2, z2) = a, b
    steps = max(int(max(abs(x2 - x1), abs(z2 - z1))), 1)
    half = ROAD_WIDTH // 2
    wide = half + ROAD_CLEARANCE
    out = []
    for i in range(steps + 1):
        x = round(x1 + (x2 - x1) * i / steps)
        z = round(z1 + (z2 - z1) * i / steps)
        # primero el desmonte, luego el firme: si no, la colina tapa la carretera
        out.append(
            f"fill {x - wide} {GROUND_Y + 1} {z - wide} "
            + f"{x + wide} {RIDGE_Y + 6} {z + wide} air"
        )
        out.append(
            f"fill {x - half} {GROUND_Y} {z - half} "
            + f"{x + half} {GROUND_Y} {z + half} {block}"
        )
    return out


STRIPE_LEN = 3
"""Bloques por franja. Con uno solo, los tramos vecinos se pisan al rasterizar y
desde lejos queda un gris sucio en vez de una valla."""


def _striped(a: tuple[float, float], b: tuple[float, float]) -> list[str]:
    """El mismo trazado que `_strip`, alternando los dos colores de la valla.

    `_strip` emite dos comandos por paso —el desmonte y el firme—, así que el
    paso es `i // 2` y la franja cambia cada `STRIPE_LEN` pasos.
    """
    negro, amarillo = CUT_ROAD_BLOCKS
    out = []
    for i, cmd in enumerate(_strip(a, b, negro)):
        franja = (i // 2) // STRIPE_LEN
        out.append(cmd if franja % 2 == 0 else cmd.replace(negro, amarillo))
    return out


def road_cut_commands(
    a: tuple[float, float], b: tuple[float, float], cut: bool = True
) -> list[str]:
    """Pinta un tramo como cortado, o lo devuelve a carretera normal.

    En el clímax el jurado ve al camión dar media vuelta; sin esto no ve **por
    qué**, porque el motivo solo existe en el dashboard. El firme se repinta de
    rojo, que se lee al instante desde el plano cenital, y en mitad del tramo se
    cruzan dos troncos — que es lo que se aprecia en el plano cercano y coincide
    con la causa que declara el escenario, "árbol caído".
    """
    out = _striped(a, b) if cut else _strip(a, b, ROAD_BLOCK)
    mx, mz = round((a[0] + b[0]) / 2), round((a[1] + b[1]) / 2)
    reach = ROAD_WIDTH // 2 + 1
    log = "oak_log" if cut else "air"
    out += [
        f"fill {mx - reach} {GROUND_Y + 1} {mz} "
        + f"{mx + reach} {GROUND_Y + 1} {mz} {log}",
        f"fill {mx} {GROUND_Y + 1} {mz - reach} "
        + f"{mx} {GROUND_Y + 1} {mz + reach} {log}",
    ]
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
    """El vehículo, como piezas de `block_display`, más su cartel con el nombre.

    Un armor stand con un cubo en la cabeza cumple la letra del backbone —se
    distingue por color a diez metros— pero no se lee como un camión: el jurado
    ve un palo con un bloque y pregunta qué es. Con `block_display` escalado se
    lee como vehículo sin que nadie lo explique, y es la misma entidad que el
    propio backbone ya usa para los marcadores de POI.

    Todas las piezas se invocan en la **misma** posición y se separan por su
    `transformation`, que gira con el yaw de la entidad. Así `movement.py` sigue
    mandando un `/tp` por unidad y ni se entera.
    """
    x, z = int(unit.x), int(unit.z)
    shape = UNIT_SHAPES.get(unit.kind) or [
        (UNIT_HEAD.get(unit.kind, "stone"), (1.0, 1.0, 1.0), (-0.5, 0.0, -0.5))
    ]
    tags = f'Tags:["{VELA_TAG}","{unit.id}"]'
    out = [
        f"summon block_display {x} {GROUND_Y} {z} "
        + f'{{{tags},block_state:{{Name:"minecraft:{block}"}},'
        + f"transformation:{{{NO_ROT},translation:[{tx}f,{ty}f,{tz}f],"
        + f"scale:[{sx}f,{sy}f,{sz}f]}},"
        + f"brightness:{{sky:15,block:15}},view_range:{VIEW_RANGE}f}}"
        for block, (sx, sy, sz), (tx, ty, tz) in shape
    ]
    # El cartel va aparte: un marcador invisible que viaja con el vehículo.
    out.append(
        f"summon armor_stand {x} {GROUND_Y + 2} {z} "
        + f"{{{tags},Marker:1b,Invisible:1b,NoGravity:1b,CustomNameVisible:1b,"
        + f'CustomName:\'{{"text":"{unit.id}"}}\'}}'
    )
    return out


CIVILIAN_RETRIES = 20
"""Tiradas por aldeano para encontrar suelo libre. Con veinte, la probabilidad de
que uno se quede dentro de la roca es despreciable; si pasa, muere uno, no el run."""

MAX_CIVILIANS = 40
"""Tope de seguridad, no una muestra: se invocan **todos** los que declara el
YAML. El backbone limita a seis las *entidades móviles*, y un aldeano con
`NoAI:1b` no se mueve ni piensa; treinta y nueve estáticos no cuestan nada. El
tope existe solo para que un cero de más en el YAML no llene el valle."""


def _clear_ground(x: float, z: float, scenario: Scenario, ridge: RidgeProfile) -> bool:
    """Suelo plano y a la vista: fuera de todo edificio y fuera de la cresta.

    Los edificios se comprueban por su huella con un bloque de holgura, porque
    el aldeano mide 0,6 de ancho y toca la pared aunque su centro caiga fuera.
    """
    if ridge.top_at(x, z) > GROUND_Y:
        return False
    for poi in scenario.pois:
        size = (10 if poi.kind == "village" else 7) + 1
        if abs(x - poi.x) <= size and abs(z - poi.z) <= size:
            return False
    return True


def civilian_commands(scenario: Scenario) -> list[str]:
    """Aldeanos con `NoAI:1b`: son un indicador visual, no simulación.

    Se invocan **todos los que declara el YAML** —24 en Pueblo A, 15 en Pueblo B—
    repartidos con dispersión sembrada, no en rejilla ni en arco perfecto:
    cualquier patrón regular se lee como colocación automática. Se quedan del lado
    por el que entra la carretera, que es por donde llega la ayuda y adonde miran,
    y fuera de la huella del edificio para que no queden dentro de una pared.

    Sin brillo: un aldeano brillando no parece un aldeano.
    """
    pois = {p.id: p for p in scenario.pois}
    rng = random.Random(scenario.seed + 3)
    valle_x = sum(p.x for p in scenario.pois) / len(scenario.pois)
    ridge = ridge_profile(scenario)
    out = []
    for group in scenario.civilians:
        poi = pois[group.poi_id]
        size = 10 if poi.kind == "village" else 7
        # hacia el interior del valle: es de donde vienen las carreteras
        hacia = -1.0 if poi.x > valle_x else 1.0
        cuantos = min(group.count, MAX_CIVILIANS)
        # la zona crece con el grupo: veinticuatro en el hueco de seis se apilan
        ancho = size + 4 + cuantos * 0.7
        for _ in range(cuantos):
            x = poi.x + hacia * rng.uniform(size + 2, size + 4 + cuantos * 0.45)
            z = poi.z + rng.uniform(-ancho, ancho)
            # Se vuelve a tirar si cae dentro de un edificio o de la cresta: un
            # aldeano dentro de un bloque se asfixia en segundos y en Pueblo A
            # aparecían 22 de los 24 del YAML. Sembrado, así que sigue siendo
            # el mismo valle en cada run.
            for _intento in range(CIVILIAN_RETRIES):
                if _clear_ground(x, z, scenario, ridge):
                    break
                x = poi.x + hacia * rng.uniform(size + 2, size + 4 + cuantos * 0.45)
                z = poi.z + rng.uniform(-ancho, ancho)
            # mirando al pueblo, con unos grados de desvío para que no formen
            yaw = math.degrees(math.atan2(-(poi.x - x), poi.z - z))
            yaw += rng.uniform(-35, 35)
            out.append(
                f"summon villager {x:.1f} {GROUND_Y + 1} {z:.1f} "
                + f'{{Tags:["{VELA_TAG}","{group.id}"],NoAI:1b,NoGravity:1b,'
                + f"Rotation:[{yaw:.0f}f,0f]}}"
            )
    return out


def main() -> None:
    """`make world`: construye el mundo por RCON desde el YAML. `--dry-run`
    imprime los comandos en vez de mandarlos, para revisar sin Paper."""
    from sim.scenario import load

    parser = argparse.ArgumentParser(
        description="Levanta el mundo del escenario por RCON."
    )
    parser.add_argument("--scenario", default="scenarios/wildfire_ridge.yaml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="imprime los comandos por stdout y no abre ninguna conexión",
    )
    parser.add_argument(
        "--teardown",
        action="store_true",
        help="solo limpia: /kill @e[tag=vela] y borra la cicatriz del fuego",
    )
    args = parser.parse_args()
    scenario = load(Path(args.scenario))

    async def run() -> None:
        rcon: Rcon
        if args.dry_run:
            rcon = PrintRcon()
        else:
            from contracts.settings import settings

            rcon = RconClient(
                settings.rcon_host, settings.rcon_port, settings.rcon_password
            )
        await rcon.connect()
        try:
            if args.teardown:
                await teardown(rcon, scenario)
            else:
                await build(scenario, rcon)
        finally:
            await rcon.close()

    try:
        asyncio.run(run())
    except BrokenPipeError:
        # `--dry-run | head`: quien lee cerró la tubería, no hay nada que avisar.
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115 — evita el aviso al cerrar


if __name__ == "__main__":
    main()
