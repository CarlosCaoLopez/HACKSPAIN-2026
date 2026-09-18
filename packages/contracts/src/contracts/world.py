"""El estado del mundo. Inmutable y reemplazado entero en cada tick.

Nadie muta un estado en sitio: `core.belief.apply(state, event) -> WorldState`
devuelve uno nuevo. Las coordenadas son siempre del mundo Minecraft (x, z, con y
implícita); el core nunca piensa en píxeles.

Quién escribe qué:
- P2 emite todo lo que cambia `units`, `cells`, `roads`, `civilians`.
- P1 mantiene `tasks` y `facts`, y es el único que construye `WorldState`.
- P3 nunca toca el estado: solo emite `world.fact.asserted`.
- P4 solo lee, a través del WebSocket.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from contracts.calls import Fact

UnitStatus = Literal["idle", "moving", "working", "unavailable"]
CellState = Literal["intact", "at_risk", "burning", "burnt", "flooded", "dark"]
CivState = Literal["exposed", "warned", "evacuating", "safe", "trapped"]
UnitKind = Literal["fire_truck", "ambulance", "drone", "crew"]
POIKind = Literal["village", "hospital", "shelter", "base", "landmark"]
TaskKind = Literal["evacuate", "extinguish", "rescue", "notify", "recon", "restore"]
TaskSeverity = Literal["low", "medium", "high", "critical"]


class Wind(BaseModel):
    bearing_deg: float  # 0 = norte, horario
    speed: float  # celdas por minuto


class Unit(BaseModel):
    id: str
    kind: UnitKind
    x: float
    z: float
    status: UnitStatus = "idle"
    task_id: str | None = None
    capabilities: list[str] = []  # "extinguish", "transport", "recon"
    capacity: int = 0


class Cell(BaseModel):
    id: str  # "cell_14_22"
    cx: int
    cz: int  # índice de rejilla
    state: CellState = "intact"
    fuel: float = 1.0
    t_changed: float = 0.0


class RoadEdge(BaseModel):
    id: str
    a: str
    b: str  # waypoint ids
    length_m: float
    cut: bool = False
    cut_cause: str | None = None


class POI(BaseModel):
    """`name` y `contact_phone` viven aquí porque el agente telefónico necesita
    decir "Pueblo A" y marcar un número. Si no, P3 mantiene un diccionario
    paralelo y el día de la demo el sistema llama al número equivocado."""

    id: str
    name: str  # "Pueblo A" — lo que dice el agente por teléfono
    kind: POIKind
    x: float
    z: float
    waypoint_id: str
    min_coverage: int = 0  # unidades mínimas que no se pueden retirar
    contact_phone: str | None = None


class CivilianGroup(BaseModel):
    id: str
    poi_id: str
    count: int
    immobile: int = 0
    state: CivState = "exposed"


class Task(BaseModel):
    """`required_capability` y `Unit.capabilities` son la pareja que usa el solver
    para poner coste infinito: una ambulancia no extingue."""

    id: str
    kind: TaskKind
    target_poi: str | None = None
    target_cell: str | None = None
    required_capability: str
    severity: TaskSeverity
    created_t: float
    done: bool = False


class WorldState(BaseModel):
    """Inmutable: `frozen=True`. Para cambiarlo, `model_copy(update=...)`."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    seq: int  # último evento aplicado
    t_sim: float
    wind: Wind
    units: dict[str, Unit] = {}
    cells: dict[str, Cell] = {}
    roads: dict[str, RoadEdge] = {}
    pois: dict[str, POI] = {}
    civilians: dict[str, CivilianGroup] = {}
    tasks: dict[str, Task] = {}
    facts: list[Fact] = []  # lo que han contado las llamadas
