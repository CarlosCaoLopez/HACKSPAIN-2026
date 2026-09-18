# Contratos e interfaces — Crisis Agent

**Cómo no pisarnos** · 2026-09-18

Todo lo que cruza una frontera entre personas está aquí. Si algo no está en este documento, no existe y no se puede llamar.

---

## Mapa de propiedad

**Cada fichero tiene exactamente un dueño. Si necesitas algo de la carpeta de otro, pídelo por evento, no por import.**

| Ruta | Dueño | Quién más puede editarlo |
| --- | --- | --- |
| `packages/contracts/**` | Nadie | Los cuatro, solo en la ventana de contrato |
| `packages/core/**` | P1 | Nadie |
| `packages/journal/**` | P1 | Nadie |
| `packages/sim/**` | P2 | Nadie |
| `scenarios/*.yaml` | P2 | P1 puede añadir campos previo aviso |
| `infra/**` | P2 | Nadie |
| `packages/voice/**` | P3 | Nadie |
| `apps/gateway/**` | P4 | P3 registra su router, no toca `main.py` |
| `apps/dashboard/**` | P4 | Nadie |
| `scripts/demo.py` | P4 | Nadie |
| `fixtures/**` | Quien lo genera | Solo se añade, nunca se modifica |

### Las tres reglas de import

1. **Todo el mundo importa de `contracts`. Nadie importa de nadie más.** `core` no importa `sim`. `voice` no importa `core`. Si escribes `from sim import ...` dentro de `core`, has roto la arquitectura.
2. **La comunicación es el bus.** `publish(evento)` y `subscribe(tipos)`. No hay llamadas directas entre paquetes.
3. **El gateway es el único que importa de todos.** Es su trabajo: montar los routers y arrancar los bucles. Por eso es de P4 y nadie más lo toca.

### La ventana de contrato

`contracts/` se escribe el viernes de 18:00 a 20:00, los cuatro delante de la misma pantalla. A partir de las 20:00 queda congelado con una excepción: **añadir un campo opcional con valor por defecto siempre está permitido** y solo requiere avisar en el canal. Cualquier otra cosa (renombrar, borrar, cambiar un tipo, hacer obligatorio un campo) necesita que los cuatro digan que sí.

Un campo opcional nuevo no rompe a nadie. Un rename a las tres de la mañana rompe a tres personas a la vez y nadie sabe por qué falla.

---

## El sobre y el catálogo de eventos

**Un solo sobre para todo lo que cruza el bus.**

```python
# contracts/events.py

class Event(BaseModel):
    run_id: str                       # uuid del run
    seq: int                          # monotónico, lo pone el bus
    t_wall: datetime                  # reloj de pared
    t_sim: float                      # segundos simulados desde el inicio
    type: EventType                   # el enum de abajo
    source: str                       # "sim" | "core" | "voice" | "human" | "call:<id>"
    payload: dict                     # validado contra el modelo del tipo
    causes: list[int] = []            # seqs que provocaron este evento
```

`causes` es opcional de escribir pero vale oro: es lo que permite al dashboard dibujar la cadena *llamada → hecho → violación → replan → orden* cuando el jurado pregunta por qué el sistema hizo algo.

### El catálogo

| `type` | Lo emite | Payload | Lo consume |
| --- | --- | --- | --- |
| `world.tick` | sim | `{t_sim, wind: Wind}` | core, dashboard |
| `world.cell.changed` | sim | `{cell_id, state, hazard}` | core, dashboard |
| `world.unit.position` | sim | `{unit_id, x, z, heading, eta_s}` | core, dashboard |
| `world.unit.status` | sim | `{unit_id, status, reason}` | core, dashboard |
| `world.road.changed` | sim | `{edge_id, cut, cause}` | core, dashboard |
| `world.civilians.changed` | sim | `{group_id, count, state, poi_id}` | core, dashboard |
| `world.inject` | sim | `{inject_type, detail}` | core, dashboard |
| `world.fact.asserted` | voice (tool `report_fact` en llamada, o `semantic.extract` al colgar), human | `{key, value, confidence, source, severity}` | core, dashboard |
| `call.requested` | core | `CallRequest` | voice, dashboard |
| `call.started` | voice | `{call_id, task_id, to, direction}` | dashboard |
| `call.transcript.partial` | voice (SSE de la sesión de HappyRobot) | `{call_id, speaker, text}` | dashboard |
| `call.affect` | voice (Humalike `foresee`) | `{call_id, emotions: list[{type, intensity}], risk}` | dashboard |
| `call.ended` | voice | `CallResult` (con `health_score` y hallazgos de `analyze` si llegaron) | core, dashboard |
| `call.signal.requested` | core | `{call_id, key, payload}` · `causes` apunta al hecho que provocó el replan | voice, dashboard |
| `call.signal.sent` | voice | `{call_id, key, signal_id, message?, latency_ms?, refined?}` · `causes` apunta al `call.signal.requested` | dashboard |
| `plan.divergence` | core | `{value, broken: list[str]}` | dashboard |
| `plan.replan.started` | core | `{reason, trigger}` | dashboard |
| `plan.policy.emitted` | core | `Policy` | dashboard |
| `plan.violation` | core | `Violation` | dashboard |
| `plan.emitted` | core | `Plan` | sim, dashboard |
| `action.requested` | core | `{action_id, verb, args}` | sim, voice |
| `action.completed` | sim, voice | `{action_id, result}` | core, dashboard |
| `action.failed` | sim, voice | `{action_id, error}` | core, dashboard |
| `human.override` | dashboard | `{kind, target, value, note}` | core, sim |
| `run.started` / `run.ended` | gateway | `{scenario_id, score?}` | todos |

### Convenciones que ahorran discusiones

- **Los ids son strings con prefijo**: `unit_truck1`, `poi_pueblo_a`, `wp_sur_03`, `cell_14_22`, `task_evac_a`.
- **Las coordenadas siempre son del mundo Minecraft** (x, z, con y implícita). El dashboard hace su propia proyección; el core nunca piensa en píxeles.
- **El tiempo del dominio es `t_sim` en segundos flotantes.** `t_wall` solo sirve para depurar y para medir la latencia real de la llamada.
- **Ningún evento se borra ni se edita.** Si algo cambia, se emite otro evento. El journal es append-only.
- **El payload de cada tipo tiene un modelo Pydantic** en `contracts`, y el bus lo valida al publicar. Un payload que no valida lanza en desarrollo y se registra como `event.malformed` en la demo, nunca tumba el proceso.

---

## contracts/world.py

**El `WorldState` es inmutable y se reemplaza entero en cada tick.** Nadie muta un estado en sitio: `belief.apply(state, event) -> WorldState` devuelve uno nuevo.

```python
UnitStatus = Literal["idle","moving","working","unavailable"]
CellState  = Literal["intact","at_risk","burning","burnt","flooded","dark"]
CivState   = Literal["exposed","warned","evacuating","safe","trapped"]

class Wind(BaseModel):
    bearing_deg: float                 # 0 = norte, horario
    speed: float                       # celdas por minuto

class Unit(BaseModel):
    id: str
    kind: Literal["fire_truck","ambulance","drone","crew"]
    x: float; z: float
    status: UnitStatus = "idle"
    task_id: str | None = None
    capabilities: list[str] = []       # "extinguish", "transport", "recon"
    capacity: int = 0

class Cell(BaseModel):
    id: str                            # "cell_14_22"
    cx: int; cz: int                   # índice de rejilla
    state: CellState = "intact"
    fuel: float = 1.0
    t_changed: float = 0.0

class RoadEdge(BaseModel):
    id: str
    a: str; b: str                     # waypoint ids
    length_m: float
    cut: bool = False
    cut_cause: str | None = None

class POI(BaseModel):
    id: str
    name: str                          # "Pueblo A" — lo que dice el agente por teléfono
    kind: Literal["village","hospital","shelter","base","landmark"]
    x: float; z: float
    waypoint_id: str
    min_coverage: int = 0              # unidades mínimas que no se pueden retirar
    contact_phone: str | None = None

class CivilianGroup(BaseModel):
    id: str
    poi_id: str
    count: int
    immobile: int = 0
    state: CivState = "exposed"

class Task(BaseModel):
    id: str
    kind: Literal["evacuate","extinguish","rescue","notify","recon","restore"]
    target_poi: str | None = None
    target_cell: str | None = None
    required_capability: str
    severity: Literal["low","medium","high","critical"]
    created_t: float
    done: bool = False

class WorldState(BaseModel):
    run_id: str
    seq: int                           # último evento aplicado
    t_sim: float
    wind: Wind
    units: dict[str, Unit]
    cells: dict[str, Cell]
    roads: dict[str, RoadEdge]
    pois: dict[str, POI]
    civilians: dict[str, CivilianGroup]
    tasks: dict[str, Task]
    facts: list[Fact]                  # lo que han contado las llamadas
```

### El detalle que se olvida siempre

`POI.name` y `POI.contact_phone` existen porque el agente telefónico necesita decir *"Pueblo A"* y marcar un número. Si esos campos no viven en el estado, P3 acaba manteniendo un diccionario paralelo y el día de la demo el sistema llama al número equivocado.

`Unit.capabilities` y `Task.required_capability` son la pareja que usa el solver para poner coste infinito: una ambulancia no extingue.

### Quién escribe qué

- **P2 emite** todo lo que cambia `units`, `cells`, `roads`, `civilians`.
- **P1 mantiene** `tasks` y `facts`, y es el único que construye `WorldState`.
- **P3 nunca toca el estado**: solo emite `world.fact.asserted`.
- **P4 solo lee**, a través del WebSocket.

---

## contracts/plan.py

**`Policy` la escribe el modelo, `Plan` lo escribe el solver, `Violation` la escribe un verificador.**

```python
class Policy(BaseModel):
    """Lo único que produce el LLM. Nunca acciones."""
    rationale: str                     # una frase, va al banner
    weights: dict[str, float]          # claves del catálogo de objetivos
    hard_constraints: list[str]        # sintaxis "nombre" o "nombre:arg"
    horizon_s: int = 600
    escalate_to_human: bool = False
    notify: list[NotifyIntent] = []    # a quién hay que llamar y por qué

class NotifyIntent(BaseModel):
    poi_id: str
    audience: Literal["resident","responder","official"]
    message_intent: str                # intención, no guion literal
    urgency: Literal["low","medium","critical"]

class Assignment(BaseModel):
    unit_id: str
    task_id: str
    route: list[str]                   # waypoint ids, ya resuelta
    eta_s: float
    cost: float

class Plan(BaseModel):
    id: str
    run_id: str
    created_t: float
    policy: Policy
    assignments: list[Assignment]
    unassigned_tasks: list[str]        # lo que no se pudo cubrir, se muestra
    context: PlanContext

class PlanContext(BaseModel):
    """Lo que el plan da por cierto. Es lo que vigila el detector."""
    assumptions: list[Assumption]
    world_seq: int                     # estado sobre el que se planificó

class Assumption(BaseModel):
    key: str                           # "road:wp_sur_03-wp_sur_04:open"
    expected: str | float | bool
    weight: float = 1.0                # cuánto pesa si se rompe

class Violation(BaseModel):
    verifier: str                      # "route_feasible"
    severity: Literal["hard","soft"]
    message: str                       # legible, va al dashboard y al planner
    involved: list[str] = []           # ids afectados
```

### El catálogo de pesos y restricciones

Contrato aparte, cerrado el viernes: el prompt del planner lo enumera y el solver lo interpreta. Si P1 añade un peso que el solver no conoce, se ignora en silencio y nadie se entera hasta la demo.

| Peso | Qué encarece o abarata |
| --- | --- |
| `life_safety` | Tareas que tocan civiles expuestos |
| `immobile_first` | Grupos con `immobile > 0` |
| `structure_protection` | Tareas sobre POIs con edificios |
| `containment` | Extinción en celdas a barlovento |
| `response_time` | Penaliza ETA alta |

| Restricción | Argumento | Qué prohíbe |
| --- | --- | --- |
| `no_unit_into_burning_cell` | — | Rutas que crucen celdas `burning` |
| `hospital_min_coverage` | `:n` | Bajar de n unidades en POIs de tipo hospital |
| `no_civilian_route_through` | `:wp_id` | Evacuaciones por ese waypoint |
| `reserve_capability` | `:cap:n` | Deja n unidades con esa capacidad libres |

Una restricción desconocida **no se ignora**: el solver lanza `Violation(verifier="unknown_constraint", severity="soft")` y sale en el dashboard.

### La regla de oro del planner

Si `Policy` tuviera un campo `assignments`, todo el diseño se viene abajo. **No lo añadáis nunca**, por muy tentador que sea a las cuatro de la mañana. El solver dando un resultado raro es información; el LLM asignando es una demo que no podéis defender.

---

## contracts/calls.py

**El core pide una intención de llamada; voice decide plataforma, número y guion.** El core no sabe que existe HappyRobot y no debe saberlo.

```python
class CallRequest(BaseModel):
    task_id: str
    poi_id: str
    to: str                            # E.164, lo resuelve core desde POI.contact_phone
    audience: Literal["resident","responder","official"]
    intent: Literal["evacuation_order","resource_request",
                    "status_check","shelter_confirm"]
    urgency: Literal["low","medium","critical"]
    facts: dict[str, str]              # variables del guion: poi_name, route_name,
                                       # deadline_min, hazard_kind
    expect: list[str] = []             # qué queremos sacar: "confirmation",
                                       # "road_status", "headcount"

class CallResult(BaseModel):
    call_id: str
    task_id: str | None                # viene de metadata.custom
    direction: Literal["outbound","inbound"]
    started_t: float
    ended_t: float
    outcome: Literal["answered","no_answer","busy","failed","hung_up"]
    transcript: str
    facts: CallFacts | None            # None si la extracción falló
    audio_url: str | None = None

class CallFacts(BaseModel):
    """El esquema que consume fenic.semantic.extract.
    Cada descripción del Field es parte del prompt: escribidlas bien."""
    location_hint: str | None = Field(None, description="lugar mencionado, tal cual lo dice la persona")
    resolved_poi_id: str | None = None           # lo rellena semantic.join después
    road_blocked: str | None = Field(None, description="tramo o carretera impracticable")
    people_immobile: int | None = Field(None, description="personas que no pueden moverse solas")
    injuries: int | None = None
    confirmed_order: bool | None = Field(None, description="si acepta la instrucción dada")
    contradicts_known: bool = False
    urgency: Literal["low","medium","critical"] = "medium"
    confidence: float = Field(0.5, ge=0, le=1)

class Fact(BaseModel):
    """Lo que entra al WorldState. Un CallFacts produce de 0 a N de estos."""
    key: str                           # "road:wp_sur_03-wp_sur_04:cut"
    value: str | float | bool
    confidence: float
    source: str                        # "call:hl_8821"
    severity: Literal["low","medium","critical"]
    t_sim: float
```

### La frontera exacta entre P3 y P1

| Responsabilidad | Quién |
| --- | --- |
| Decidir que hay que llamar y a quién | P1 (core) |
| Elegir plataforma, número saliente y guion | P3 (voice) |
| Recibir el tool en llamada, responder el ack (pasado por `foresee`) sin bloquear el replan | P3 |
| Recibir el webhook de fin de llamada y montar `CallResult` | P3 |
| Pedir una signal al agente cuando hay plan nuevo | P1 (core) |
| Redactar la signal, refinarla con `foresee` y publicarla a `session.<id>` | P3 |
| Ejecutar `semantic.extract` y producir `CallFacts` | P3 |
| Traducir `CallFacts` a la lista de `Fact` | **P3**, con el mapa de claves que le da P1 |
| Aplicar los `Fact` al `WorldState` | P1 |

El mapa de claves vive en `contracts/factkeys.py` y es una lista plana de strings con su tipo esperado. P1 lo escribe, P3 lo usa. Sin ese fichero, P3 inventa claves y P1 las ignora en silencio, que es el bug más caro que podéis tener el domingo.

### Timeouts y fallos

- Llamada saliente sin respuesta en 45 s: `outcome="no_answer"`, el core reintenta una vez y después escala a `human.override`.
- El endpoint del tool publica los hechos **antes** de esperar a `foresee`; si `foresee` tarda más de 3 s (medido: 2,5 s), devuelve el ack en borrador. El replan nunca espera a Humalike.
- `semantic.extract` por encima de 4 s: se emite `CallResult` con `facts=None` y la transcripción cruda va al dashboard marcada como *sin extraer*. La demo continúa.
- `analyze` falla o devuelve `402`: `CallResult` sin `health_score`. Nada se bloquea.
- Webhook duplicado (pasa): descartad por `call_id` ya visto. Idempotencia obligatoria.

---

## Interfaces internas

**Estas firmas se escriben el viernes y no cambian.** El cuerpo puede estar vacío hasta el sábado; la firma, no.

### El bus (en `contracts`, lo escribe P1 en la ventana de contrato)

```python
async def publish(ev: Event) -> None
def subscribe(*types: EventType) -> AsyncIterator[Event]
def current_run_id() -> str
```

`publish` escribe al journal antes de repartir. Siempre.

### P2 · sim

```python
class Sim:
    def __init__(self, scenario_path: Path, rcon: RconClient) -> None
    async def start(self) -> None              # worldgen + tick loop
    async def stop(self) -> None
    async def execute(self, action_id: str, verb: str, args: dict) -> None
    async def inject(self, inject_type: str, payload: dict) -> None
    def snapshot(self) -> dict                 # solo para depurar
```

`verb` acepta exactamente `goto`, `set_marker`, `announce`, `rescue`. Cualquier otro emite `action.failed` con `error="unknown_verb"`. P2 no añade verbos sin avisar; P1 no inventa verbos.

### P1 · core

```python
class Core:
    def __init__(self, bus, scenario: Scenario) -> None
    async def run(self) -> None                # consume el bus indefinidamente
    def state(self) -> WorldState              # el dashboard lo pide al arrancar
    def current_plan(self) -> Plan | None

# puros, testeables sin nada montado
def apply(state: WorldState, ev: Event) -> WorldState
def divergence(state: WorldState, ctx: PlanContext) -> tuple[float, list[str]]
async def plan(state: WorldState, reason: str) -> Policy
def solve(state: WorldState, policy: Policy) -> Plan
def verify(state: WorldState, plan: Plan) -> list[Violation]
```

Las cinco funciones de abajo son puras a propósito: P1 puede desarrollarlas contra `fixtures/run_golden.jsonl` sin que exista ni el sim ni la voz.

### P3 · voice

```python
class VoiceGateway:
    async def place_call(self, req: CallRequest) -> str        # devuelve call_id
    async def signal(self, call_id: str, key: str, payload: dict) -> str   # HappyRobot: POST /api/v2/signals a session.<id>
    async def foresee(self, call_id: str, draft: str) -> tuple[str, dict]  # Humalike: (refined_reply, mental_state); el borrador si tarda > 1,5 s
    async def analyze(self, call_id: str) -> dict | None                   # Humalike: health_score y hallazgos, al colgar
    async def extract(self, transcript: str) -> CallFacts | None
    def to_facts(self, cf: CallFacts, t_sim: float, call_id: str) -> list[Fact]

router: APIRouter    # /webhooks/happyrobot/fact (tool, en llamada) · /webhooks/happyrobot/call (fin)
```

`place_call` devuelve en cuanto la plataforma acepta, no cuando la llamada termina. El resultado llega por evento. Nadie espera a una llamada de forma bloqueante.

### P4 · gateway

```python
app: FastAPI
# monta voice.router, arranca Sim y Core, sirve el WS y el dashboard
```

P4 decide el orden de arranque y apaga limpio. Expone `POST /control/*` y es el único que puede publicar eventos `human.override`.

---

## HTTP y WebSocket

**Un WebSocket que solo empuja eventos, y endpoints HTTP para todo lo que no es un evento.** El dashboard no hace polling de nada.

### Endpoints

| Método y ruta | Dueño | Para qué |
| --- | --- | --- |
| `GET /api/state` | P4 | Estado completo al abrir el dashboard |
| `GET /api/plan` | P4 | Plan vigente |
| `GET /api/scenarios` | P4 | Lista de escenarios disponibles |
| `POST /api/run` | P4 | `{scenario_id}` arranca un run, devuelve `run_id` |
| `POST /api/run/stop` | P4 | Para el run actual |
| `POST /control/inject` | P4 | `{inject_type, payload}` dispara un inject a mano |
| `POST /control/override` | P4 | La intervención humana, ver abajo |
| `POST /control/pause` | P4 | Congela el tick, para explicar algo en el pitch |
| `POST /webhooks/happyrobot/fact` | P3 | El tool `report_fact` del agente, **durante** la llamada. Publica los hechos y devuelve el ack |
| `POST /webhooks/happyrobot/call` | P3 | Fin de llamada (nodo Webhook del workflow): `task_id`, `session_id`, estado, transcripción, extract |
| `GET /api/runs` | P4 | Runs pasados con su puntuación, para el run 1 vs run 12 |
| `WS /ws` | P4 | El chorro de eventos |

### El WebSocket

Al conectar, el servidor envía `{"kind":"snapshot", "state": WorldState, "plan": Plan|null, "seq": n}` y a partir de ahí solo `{"kind":"event", "event": Event}` en orden de `seq`. Si el cliente detecta un hueco en `seq`, pide `GET /api/state` y reinicia. Nada de reconciliación fina.

El dashboard mantiene su propio estado derivado aplicando los eventos. **No dupliquéis `belief.apply` a mano en TypeScript**: reimplementad solo lo que necesitéis pintar y para lo demás usad los eventos `plan.*`, que ya vienen completos.

### La intervención humana

Requisito obligatorio del reto, con contrato propio:

```json
POST /control/override
{
  "kind": "force_assignment" | "veto_assignment" | "assert_fact"
         | "force_replan" | "set_priority",
  "target": "unit_truck1" | "task_evac_a" | "road:wp_sur_03-wp_sur_04",
  "value": "<depende de kind>",
  "note": "el jefe de bomberos dice que la sur está transitable"
}
```

Se publica como `human.override` y el core lo trata **con prioridad máxima**: un `assert_fact` humano entra con `confidence=1.0`, un `veto_assignment` pone coste infinito a ese par unidad-tarea durante 5 minutos, y un `force_replan` salta el umbral de divergencia.

En el dashboard son tres botones sobre cada tarjeta de asignación y un campo de texto para asertar un hecho. **Usadlo en la demo al menos una vez**, en directo: es la prueba visible del criterio *Control*.

### Autenticación

Ninguna. Todo corre en localhost salvo los webhooks, que van por un túnel. Poned un token compartido en la cabecera de los webhooks para que un escaneo aleatorio no os dispare una llamada a mitad del pitch.

---

## Trabajar sin los demás

**Nadie debe esperar a nadie en ningún momento del fin de semana.**

| Comando | Qué levanta | Qué simula | Para |
| --- | --- | --- | --- |
| `make dev-core` | core + bus | sim y voice desde `run_golden.jsonl` | P1 |
| `make dev-sim` | sim + bus + RCON | un core tonto que manda `goto` en bucle | P2 |
| `make dev-voice` | gateway + voice + túnel | un core que pide una llamada cada 60 s | P3 |
| `make dev-dash` | gateway + WS en modo replay | todo, reproduciendo un journal | P4 |
| `make demo` | todo de verdad | nada | Integración y ensayo |
| `make replay RUN=<id>` | bus + WS | reproduce ese journal a velocidad real | Depurar y grabar |

### Los tres mocks, y quién los escribe

1. **`fixtures/run_golden.jsonl`** — lo genera P2 el sábado por la mañana en cuanto el sim mueve unidades, aunque el core aún no decida nada. Un run de 6 minutos con todos los tipos de evento del catálogo apareciendo al menos una vez. **Es el artefacto más valioso del proyecto.**
2. **`packages/core/dummy.py`** — lo escribe P1 el viernes en 20 minutos: un core que al recibir `world.fire.detected` manda el camión más cercano y nada más. P2 y P3 lo usan todo el sábado.
3. **`packages/voice/fake.py`** — lo escribe P3 el viernes: acepta `CallRequest`, espera 8 segundos, y publica un `CallResult` con una transcripción de fichero. Es también la base del `--mock-calls` del plan B.

### Contratos verificados, no acordados

`make check` corre tres cosas, todas rápidas:

- `mypy` sobre `contracts` y las firmas públicas de cada paquete.
- Un test que replaya `run_golden.jsonl` completo por `apply` y comprueba que ningún evento revienta la validación.
- Un test que valida que todo peso y toda restricción que aparece en los prompts de `core/prompts/` existe en el catálogo.

Ese tercer test parece una tontería y es el que os salva: es el fallo silencioso más probable de toda la arquitectura.

### Variables de entorno

Un solo `.env` en la raíz, con `.env.example` commiteado. Las claves las carga `contracts/settings.py` con `pydantic-settings`, y **cada paquete lee solo las suyas**:

```bash
RCON_HOST=localhost                 # P2
RCON_PORT=25575
RCON_PASSWORD=
ANTHROPIC_API_KEY=                  # P1 (planner) y P3 (fenic)
HAPPYROBOT_API_KEY=                 # P3
HAPPYROBOT_HOOK_EVACUATION=         # P3, https://platform.happyrobot.ai/hooks/<slug>
HUMANLIKE_API_KEY=                  # P3, token de Humalike (api.humalike.com)
WEBHOOK_SHARED_TOKEN=               # P3 y P4
JUDGE_PHONE=                        # P3, se cambia en el último minuto
VELA_MODE=demo|dev|replay           # P4
```

`JUDGE_PHONE` en una variable y no en el código. Lo vais a cambiar cinco minutos antes de subir al escenario.

---

## Cambios de contrato e integración

**Añadir es libre, cambiar cuesta, borrar está prohibido hasta el domingo.**

| Cambio | Permiso | Cómo |
| --- | --- | --- |
| Campo opcional con valor por defecto | Libre | Commit + mensaje en el canal |
| Tipo de evento nuevo | Libre | Añadirlo al enum y a la tabla del catálogo |
| Peso o restricción nueva | Aviso a P1 | Va al catálogo y al prompt a la vez |
| Renombrar un campo | Los cuatro | Nunca después del sábado a las 18:00 |
| Cambiar un tipo | Los cuatro | Nunca después del sábado a las 18:00 |
| Borrar cualquier cosa | Prohibido | Dejadlo muerto y sin usar hasta el lunes |

### Los tres puntos de integración

No integréis de forma continua: integrad tres veces, con todo el mundo mirando la misma pantalla, y cada una con un criterio de superación claro.

| Momento | Criterio de superación |
| --- | --- |
| **Sáb 13:00** | Un incendio arranca, el core asigna, un camión se mueve en Minecraft. Sin LLM si hace falta. Se graba `run_golden.jsonl` |
| **Sáb 18:00** | Llamada real saliente, replan por inject de viento, banner en el dashboard. Se cronometra por primera vez |
| **Dom 09:00** | Llamada entrante que cuelga y cambia la dirección de las unidades en menos de 3 s. Congelación tras esto |

Si una integración no pasa su criterio, **no se avanza a lo siguiente**: se para todo el mundo y se arregla. Un equipo que sigue construyendo encima de una integración rota llega al domingo con cuatro piezas bonitas y ninguna demo.

### Checklist de la ventana de contrato del viernes

Antes de que nadie escriba una línea de lógica:

- [ ] `contracts/events.py` con el enum completo del catálogo
- [ ] `contracts/world.py` con los ocho modelos
- [ ] `contracts/plan.py` con `Policy`, `Plan`, `Violation`, `PlanContext`
- [ ] `contracts/calls.py` con `CallRequest`, `CallResult`, `CallFacts`, `Fact`
- [ ] `contracts/factkeys.py` con la lista de claves y sus tipos
- [ ] El catálogo de pesos y restricciones cerrado y escrito
- [ ] `contracts/bus.py` con `publish` y `subscribe` funcionando
- [ ] Los cuatro verbos de `sim.execute` acordados
- [ ] `scenarios/wildfire_ridge.yaml` con POIs, unidades y grafo, aunque las coordenadas sean provisionales
- [ ] Un `Event` de prueba viajando del proceso al journal y de vuelta
- [ ] `.env.example` con las diez variables
- [ ] Cada uno sabe decir en una frase qué expone su paquete y qué consume

Si el último punto no se cumple, no habéis terminado la ventana, por muchos ficheros que existan.
