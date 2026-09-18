"""El autómata del peligro. Tres implementaciones, una interfaz.

`Wildfire` es una rejilla de celdas de 4x4 bloques. Cada tick, una celda `burning`
intenta propagarse a cada vecina con una tasa, en celdas por minuto, de
`(base_spread + viento * max(0, cos(ángulo))) * fuel / distancia`.

Esto **se aparta a propósito** de la fórmula del backbone, `base * (1 + cos) * fuel`:
esa expresión no usa `Wind.speed` en ningún sitio, aunque `contracts.world.Wind` la
documente en celdas por minuto, y medida da un autómata biestable — por debajo de
`base_spread` 0,6 el fuego se apaga solo y por encima de 1,0 arrasa el mapa en
noventa segundos, sin ventana utilizable entre medias. Con el viento entrando como
sumando, las dos magnitudes del YAML significan lo que dicen que significan y el
frente avanza a favor del viento sin reventar.

La semilla del RNG va en el YAML: el mismo escenario produce el mismo incendio en
cada ensayo. Con `doFireTick false` ese fuego no se propaga solo ni quema nada.

Módulo **puro** (D5) salvo `render_commands`, que solo devuelve cadenas. El azar
vive en una instancia propia (D4), nunca en el módulo `random`: en cuanto otro
paquete tocara el global, el mismo escenario dejaría de reproducirse — y no
petaría, simplemente dejaría de ser el mismo incendio.
"""

import math
import random
import re
from typing import Protocol

from pydantic import BaseModel

from contracts.scenario import HazardSpec
from contracts.world import CellState, Wind

CELL_ID = re.compile(r"^cell_(-?\d+)_(-?\d+)$")

BURN_DURATION_S = 45.0
"""Lo que una celda arde antes de quedar `burnt`. Deja frente móvil y cicatriz."""

MAX_RADIUS_CELLS = 40
"""Tope de propagación alrededor del origen. Sin él, seis minutos de demo bastan
para que el fuego salga del valle y se coma el mapa entero."""

SECONDS_PER_MINUTE = 60.0
"""`base_spread` y `Wind.speed` van en **celdas por minuto**, que es como
`contracts.world.Wind` documenta la velocidad. Leer `base_spread` como
probabilidad por segundo multiplica el ritmo por sesenta: el mapa entero arde en
90 segundos."""

NEIGHBOURS = [(dx, dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if (dx, dz) != (0, 0)]
"""Ocho vecinas. Con cuatro el frente avanza en rombo y desde arriba canta."""

IGNITABLE: frozenset[CellState] = frozenset({"intact", "at_risk"})
"""Lo que todavía puede arder. `at_risk` incluido: es una marca para el planner."""


class CellChange(BaseModel):
    """Lo que devuelve un tick del autómata. El runner lo traduce a
    `world.cell.changed` y a comandos de render."""

    cell_id: str
    state: CellState
    hazard: str


class Hazard(Protocol):
    """Interfaz común. `Wildfire | Flood | Blackout` la cumplen."""

    def tick(self, dt: float) -> list[CellChange]: ...

    def cells_at_risk(self, horizon_s: float) -> list[str]: ...

    def set_wind(self, wind: Wind) -> None: ...

    def render_commands(self, change: CellChange) -> list[str]: ...


def parse_cell(cell_id: str) -> tuple[int, int]:
    """`cell_14_22` → (14, 22). Falla fuerte: un id mal escrito en el YAML no
    puede acabar en una celda silenciosamente equivocada."""
    match = CELL_ID.match(cell_id)
    if match is None:
        raise ValueError(f"id de celda inválido: {cell_id!r}, se espera cell_<cx>_<cz>")
    return int(match.group(1)), int(match.group(2))


def cell_id(cx: int, cz: int) -> str:
    return f"cell_{cx}_{cz}"


def _probability(rate: float, seconds: float) -> float:
    """Probabilidad de que un proceso de Poisson con esa tasa ocurra en `seconds`.

    Monótona en la tasa y nunca llega a 1, así que ordenar por ella sigue
    distinguiendo a favor de viento y en contra por muy largo que sea el horizonte.
    """
    return 1.0 - math.exp(-rate * seconds)


def wind_vector(bearing_deg: float) -> tuple[float, float]:
    """Hacia dónde **sopla** un viento con ese rumbo, en (dx, dz) de Minecraft.

    `bearing_deg` es de dónde viene (convención meteorológica: el YAML pone 270
    y lo comenta como O→E). Norte es -Z y este +X, así que el rumbo al que va es
    el opuesto.
    """
    towards = math.radians(bearing_deg + 180.0)
    return math.sin(towards), -math.cos(towards)


class Wildfire:
    """`fill netherrack` + `fill fire` encima, más humo. `burnt` es `coal_block`,
    que deja una cicatriz negra vista desde arriba."""

    def __init__(self, spec: HazardSpec, seed: int) -> None:
        self.spec = spec
        self.wind = spec.wind
        self.ground_y = 64
        """Altura del render. Mientras el mapa sea plano vale una constante; con
        el valle real esto pide un heightmap por celda."""

        self._rng = random.Random(seed)
        self._origin = parse_cell(spec.origin_cell)
        self._state: dict[str, CellState] = {}
        self._burning_for: dict[str, float] = {}
        self._pending: list[CellChange] = []

        # La ignición del minuto 00:00 sale en el primer `tick`, no se pierde.
        self._pending.append(self._ignite(spec.origin_cell))
        self._pending.extend(self._mark_at_risk())

    # --- la interfaz ---

    def tick(self, dt: float) -> list[CellChange]:
        changes, self._pending = self._pending, []

        for cid in sorted(self._burning_for):
            self._burning_for[cid] += dt
            if self._burning_for[cid] >= BURN_DURATION_S:
                self._state[cid] = "burnt"
                del self._burning_for[cid]
                changes.append(self._change(cid, "burnt"))

        # Orden estable: un dict recorrido al azar rompe el determinismo aunque
        # el RNG esté bien sembrado.
        for cid in sorted(self._burning_for):
            for neighbour, rate in self._spread_from(cid):
                if self._rng.random() < _probability(rate, dt):
                    changes.append(self._ignite(neighbour))

        changes.extend(self._mark_at_risk())
        return changes

    def cells_at_risk(self, horizon_s: float) -> list[str]:
        """Las que probablemente ardan dentro de ese horizonte, de más a menos.

        Es lo que el core necesita para priorizar: evacuar antes de que llegue el
        frente, no cuando ya ha llegado.
        """
        risk: dict[str, float] = {}
        for cid in self._burning_for:
            for neighbour, rate in self._spread_from(cid):
                # Probabilidad de que al menos una vecina ardiendo la encienda.
                probability = _probability(rate, horizon_s)
                survives = (1 - probability) * (1 - risk.get(neighbour, 0.0))
                risk[neighbour] = 1 - survives
        return sorted(risk, key=lambda c: (-risk[c], c))

    def set_wind(self, wind: Wind) -> None:
        """Cambiar el viento es cambiar un vector en memoria."""
        self.wind = wind

    def render_commands(self, change: CellChange) -> list[str]:
        x1, z1, x2, z2 = self.bounds(change.cell_id)
        y = self.ground_y
        if change.state == "burning":
            return [
                f"fill {x1} {y} {z1} {x2} {y} {z2} netherrack",
                f"fill {x1} {y + 1} {z1} {x2} {y + 1} {z2} fire",
            ]
        if change.state == "burnt":
            return [
                f"fill {x1} {y + 1} {z1} {x2} {y + 1} {z2} air",
                f"fill {x1} {y} {z1} {x2} {y} {z2} coal_block",
            ]
        return []  # `intact` y `at_risk` son estado del modelo, no se pintan

    # --- consultas ---

    def bounds(self, cid: str) -> tuple[int, int, int, int]:
        """(x1, z1, x2, z2) en bloques del mundo. Para `/fill`, inclusive."""
        cx, cz = parse_cell(cid)
        size = self.spec.cell_size
        return cx * size, cz * size, cx * size + size - 1, cz * size + size - 1

    def state_of(self, cid: str) -> CellState:
        return self._state.get(cid, "intact")

    @property
    def burning(self) -> list[str]:
        return sorted(self._burning_for)

    # --- interno ---

    def _spread_from(self, cid: str) -> list[tuple[str, float]]:
        """Vecinas candidatas de una celda ardiendo, con su **tasa** por segundo.

        Devuelve tasa y no probabilidad a propósito: `_probability` la convierte
        al intervalo que haga falta. Con probabilidades directas, un horizonte
        largo las satura todas a 1 y `cells_at_risk` pierde el orden justo
        cuando más falta hace.
        """
        cx, cz = parse_cell(cid)
        wind_x, wind_z = wind_vector(self.wind.bearing_deg)

        out = []
        for dx, dz in NEIGHBOURS:
            neighbour = cell_id(cx + dx, cz + dz)
            # `at_risk` sigue siendo combustible: es una marca para el planner,
            # no un estado del que una celda ya no pueda arder.
            if self.state_of(neighbour) not in IGNITABLE or self._too_far(
                cx + dx, cz + dz
            ):
                continue
            distance = math.hypot(dx, dz)
            cosine = (dx * wind_x + dz * wind_z) / distance
            # Celdas por minuto: base isótropa más el empuje del viento, que solo
            # suma a favor. La diagonal está más lejos y avanza proporcionalmente
            # más despacio.
            rate = self.spec.base_spread + self.wind.speed * max(0.0, cosine)
            out.append(
                (neighbour, rate * self._fuel / distance / SECONDS_PER_MINUTE)
            )
        return out

    @property
    def _fuel(self) -> float:
        """Uniforme mientras no haya terreno. Con el mapa real, por celda."""
        return 1.0

    def _too_far(self, cx: int, cz: int) -> bool:
        return math.dist((cx, cz), self._origin) > MAX_RADIUS_CELLS

    def _ignite(self, cid: str) -> CellChange:
        self._state[cid] = "burning"
        self._burning_for[cid] = 0.0
        return self._change(cid, "burning")

    def _mark_at_risk(self) -> list[CellChange]:
        changes: list[CellChange] = []
        for cid in self.cells_at_risk(BURN_DURATION_S):
            if self.state_of(cid) == "intact":
                self._state[cid] = "at_risk"
                changes.append(self._change(cid, "at_risk"))
        return changes

    def _change(self, cid: str, state: CellState) -> CellChange:
        return CellChange(cell_id=cid, state=state, hazard=self.spec.kind)


class Flood:
    """Pendiente: no está en la demo. Ver H8."""

    def __init__(self, spec: HazardSpec, seed: int) -> None:
        raise NotImplementedError("Flood no entra en la demo; ver H8")

    def tick(self, dt: float) -> list[CellChange]:
        raise NotImplementedError

    def cells_at_risk(self, horizon_s: float) -> list[str]:
        raise NotImplementedError

    def set_wind(self, wind: Wind) -> None:
        raise NotImplementedError

    def render_commands(self, change: CellChange) -> list[str]:
        raise NotImplementedError


class Blackout:
    """Pendiente: es el segundo escenario, `blackout_grid`. Ver H8."""

    def __init__(self, spec: HazardSpec, seed: int) -> None:
        raise NotImplementedError("Blackout llega con el segundo escenario; ver H8")

    def tick(self, dt: float) -> list[CellChange]:
        raise NotImplementedError

    def cells_at_risk(self, horizon_s: float) -> list[str]:
        raise NotImplementedError

    def set_wind(self, wind: Wind) -> None:
        raise NotImplementedError

    def render_commands(self, change: CellChange) -> list[str]:
        raise NotImplementedError


HAZARDS = {"wildfire": Wildfire, "flood": Flood, "blackout": Blackout}


def build_hazard(spec: HazardSpec, seed: int) -> Hazard:
    """`spec.kind` → la implementación. Falla fuerte si no existe."""
    if spec.kind not in HAZARDS:
        raise ValueError(
            f"peligro desconocido: {spec.kind!r}. Hay {sorted(HAZARDS)}"
        )
    return HAZARDS[spec.kind](spec, seed)
