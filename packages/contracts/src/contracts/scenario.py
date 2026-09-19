"""El escenario declarado: `scenarios/*.yaml` parseado.

El mundo no se genera, se declara. El YAML es la única fuente de verdad y tiene
cinco bloques: `pois`, `units`, `roads`, `civilians` e `injects`.

Vive en `contracts` y no en `sim` porque `Core.__init__(bus, scenario)` lo
necesita y `core` no puede importar de `sim` (regla de imports). `sim/scenario.py`
solo parsea el YAML a este modelo.
"""

from pydantic import BaseModel

from contracts.world import POI, CivilianGroup, RoadEdge, Unit, Wind


class Waypoint(BaseModel):
    id: str  # "wp_sur_03"
    x: float
    z: float


class InjectSpec(BaseModel):
    """Una sorpresa en la línea temporal. La importante no está aquí: la dispara
    la llamada entrante."""

    at: float  # t_sim en segundos
    type: str  # "wind_shift" | "road_cut" | "unit_failure"
    payload: dict = {}


class HazardSpec(BaseModel):
    kind: str  # "wildfire" | "flood" | "blackout"
    origin_cell: str
    cell_size: int = 4  # bloques por celda
    base_spread: float = 0.1
    wind: Wind


class Scenario(BaseModel):
    """La semilla del RNG va aquí: el mismo escenario produce el mismo incendio en
    cada ensayo."""

    id: str
    name: str
    seed: int = 0
    origin: tuple[float, float] = (0.0, 0.0)  # esquina del mundo Minecraft
    hazard: HazardSpec
    pois: list[POI] = []
    units: list[Unit] = []
    waypoints: list[Waypoint] = []
    roads: list[RoadEdge] = []
    civilians: list[CivilianGroup] = []
    injects: list[InjectSpec] = []

    # Cómo nombra la gente los sitios por teléfono → id del escenario. Los lee
    # `voice.pois` para resolver "el molino viejo" o "la pista del sur" sin red.
    # Opcionales con default: un escenario sin ellos sigue valiendo.
    poi_aliases: dict[str, str] = {}  # "el molino" → "poi_molino"
    road_aliases: dict[str, str] = {}  # "pista del sur" → "road:wp_sur_01-wp_sur_02"
