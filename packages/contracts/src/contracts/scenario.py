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
    # Sofocar (sim) y alcanzar (core) el fuego desde la carretera: un camión trabaja las
    # celdas `burning` a menos de `suppress_reach_m` de su posición a `suppress_rate`
    # celdas por minuto (repartidas entre las que tenga a tiro). Un frente más lejos de
    # cualquier waypoint que el alcance no se puede atacar y el core lo baja de prioridad.
    suppress_reach_m: float = 24.0
    suppress_rate: float = 3.0


class GeoAnchor(BaseModel):
    """Proyección lat/lon → (x, z) del mundo: el punto `(lat, lon)` cae en `(x, z)`,
    el eje x apunta al este y z al sur, `scale` metros de mundo por metro real
    (1.0 = escala natural; 0.05 comprime un pueblo real en 200 bloques). Un pin a
    menos de `snap_m` metros de mundo de un POI se ancla a él. Opcional: sin anclaje
    el pin de Telegram no se proyecta y solo cuenta el texto."""

    lat: float
    lon: float
    x: float = 0.0
    z: float = 0.0
    scale: float = 1.0
    snap_m: float = 60.0


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
    geo: GeoAnchor | None = None  # Telegram: dónde cae un pin GPS en este mundo
