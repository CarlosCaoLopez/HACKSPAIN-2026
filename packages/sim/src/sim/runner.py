"""El tick loop y la clase `Sim`. P2.

Cada tick avanza 1 segundo simulado: propaga fuego, interpola posiciones, dispara
injects vencidos, y publica `world.*`.

Es el único módulo de `sim` que habla con el mundo y con el bus. `graph`,
`movement`, `hazard` e `injects` son puros y no saben que existe ninguna de las
dos cosas (D5): eso es lo que los hace testeables sin Paper y sin core.
"""

import asyncio
import contextlib
import logging
import math
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contracts.events import Event, EventType
from contracts.world import Wind
from sim.graph import RoadGraph
from sim.hazard import build_hazard, cell_id
from sim.injects import ROAD_CUT, UNIT_FAILURE, WIND_SHIFT, InjectScheduler
from sim.movement import TICK_HZ, Movement, tp_command
from sim.rcon import HIGH, LOW, Rcon
from sim.scenario import load
from sim.worldgen import (
    GROUND_Y,
    POI_STYLE,
    build,
    road_cut_commands,
    teardown,
)

log = logging.getLogger("vela.sim")

TICK_S = 1.0
"""Un segundo simulado por tick. La interpolación va a 5 Hz por dentro."""

VERBS = ("goto", "set_marker", "announce", "rescue")
"""Exactamente cuatro. Cualquier otro es `action.failed` con `unknown_verb`."""

DEFAULT_SPEED_MPS = 4.0
"""Velocidad de una unidad por carretera. A 14 m/s la ruta corta se hacía en 12 s:
demasiado rápido para verlo, y sobre todo demasiado rápido para que el corte de
carretera del minuto 3:30 pille a alguien en ruta — el clímax se quedaba sin nadie
a quien reencaminar. A 4 m/s son ~40 s, que se ven y se pueden interrumpir."""

DANGER_RADIUS_M = 45.0
"""A qué distancia del frente un POI se pinta en rojo.

Que un pueblo esté amenazado **no es una decisión, es geometría**: el sim ya sabe
qué celdas arden y dónde está cada POI. Hacer que eso viajara como orden desde el
core era acoplamiento gratis, y mientras el core no lo pidiera el mapa se quedaba
muerto salvo por el fuego y los camiones."""

MARKER_BLOCKS = {
    "ok": "lime_concrete", "warned": "yellow_concrete",
    "evacuating": "orange_concrete", "danger": "red_concrete",
    "safe": "light_blue_concrete", "done": "white_concrete",
}


async def _publish(ev: Event) -> None:
    """Publica al bus. La reserva es un espejo para depurar, no un desvío.

    Antes esto se saltaba el bus cuando `current_run_id()` venía vacío y lo metía
    todo en `_FALLBACK`. La intención era no ensuciar un bus que nadie había
    arrancado; el efecto fue que **el sistema entero corría sin publicar un solo
    evento** y reportando `up`: journal a cero, el core sin recibir nada y el mundo
    avanzando para nadie. Costó una mañana encontrarlo porque no había error en
    ningún sitio.

    Ahora se publica siempre y se avisa una vez si no hay run arrancado, que es una
    anomalía y no un estado normal. `_FALLBACK` sigue recibiendo copia mientras no
    haya run, porque es de donde leen los tests.
    """
    from contracts import bus

    try:
        if not bus.current_run_id():
            global _WARNED_NO_RUN
            if not _WARNED_NO_RUN:
                _WARNED_NO_RUN = True
                log.warning(
                    "publicando sin run arrancado: ¿falta bus.configure()? "
                    "los eventos van al bus igualmente"
                )
            _FALLBACK.append(ev)
        await bus.publish(ev)
    except NotImplementedError:
        _FALLBACK.append(ev)


_WARNED_NO_RUN = False
"""Un aviso, no uno por evento: son mil por run."""


_FALLBACK: list[Event] = []
"""Los eventos que no ha podido tragar el bus. `snapshot()` los expone."""


async def _subscribe(*types: EventType):
    """Suscripción al bus, o `None` si P1 todavía no lo ha escrito.

    Mismo rodeo que `_publish`: la firma está cerrada, así que `sim` escribe
    contra ella y el día que exista el bus esto empieza a funcionar solo.
    """
    from contracts import bus

    try:
        return bus.subscribe(*types)
    except NotImplementedError:
        return None


def _run_id() -> str:
    from contracts import bus

    try:
        return bus.current_run_id() or f"run_{uuid.uuid4().hex[:8]}"
    except NotImplementedError:
        return f"run_{uuid.uuid4().hex[:8]}"


class Sim:
    def __init__(self, scenario_path: Path, rcon: Rcon) -> None:
        self.scenario = load(scenario_path)
        self.rcon = rcon
        self.graph = RoadGraph.from_scenario(self.scenario)
        self.hazard = build_hazard(self.scenario.hazard, self.scenario.seed)
        self.injects = InjectScheduler(self.scenario.injects)

        self.t_sim = 0.0
        self.speed = 1.0
        """Multiplicador del reloj (D3). Los 12 runs del bonus no caben a 1x."""

        self.run_id = _run_id()
        self.units = {u.id: u.model_copy() for u in self.scenario.units}
        self.civilians = {c.id: c.model_copy() for c in self.scenario.civilians}
        self._pois = {p.id: p for p in self.scenario.pois}
        self._moving: dict[str, tuple[Movement, str]] = {}
        self._marker_state: dict[str, str] = {}
        """Color actual de cada POI, para repintar solo cuando cambia."""
        self._loop_task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self._running = False

    # --- ciclo de vida ---

    async def start(self) -> None:
        """Worldgen + tick loop. Publica `world.*` hasta que alguien pare.

        Conecta el RCON él mismo. Quien construye el cliente no lo conecta a
        propósito —`connect` reintenta con backoff y un puerto muerto serían varios
        segundos de bloqueo al arrancar el run—, así que le toca a quien tiene el
        ciclo de vida. Sin esto el primer comando del worldgen muere con
        "RconClient sin conectar" y la task del sim se cae entera dos segundos
        después de arrancar, con el resto del sistema corriendo en vacío.
        """
        await self.rcon.connect()
        await build(self.scenario, self.rcon)
        self._running = True
        await self._emit(
            EventType.WORLD_FIRE_DETECTED,
            {"cell_id": self.scenario.hazard.origin_cell,
             "hazard": self.scenario.hazard.kind},
        )
        self._loop_task = asyncio.create_task(self._loop(), name="sim-tick")
        self._watch_task = asyncio.create_task(self._watch_roads(), name="sim-roads")

    def set_speed(self, speed: float) -> None:
        """Multiplicador del reloj de pared (D3). Lo llama el gateway con `--speed`.

        `t_sim` no cambia: un tick sigue siendo un segundo simulado y el journal
        sale idéntico. Lo único que se acorta es la espera entre ticks, así que a
        10× los seis minutos de demo se corren en 36 s — que es lo que hace
        viables los doce runs del domingo para el bonus de aprendizaje. A 1× no
        pasa nada: es como se ensaya.
        """
        if speed <= 0:
            raise ValueError(f"velocidad no positiva: {speed}")
        self.speed = speed

    async def stop(self) -> None:
        self._running = False
        if self._watch_task is not None:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task
            self._watch_task = None
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None
        await teardown(self.rcon, self.scenario)

    async def _watch_roads(self) -> None:
        """Escucha `world.road.changed` y pinta, venga el corte de donde venga.

        El corte que importa en la demo no lo dispara un inject del YAML: lo
        deduce el core de la llamada del vecino. Por ese camino el sim nunca se
        enteraba, así que la carretera no se pintaba justo en el momento para el
        que existe el repintado. Escuchar el evento —que ya está en el catálogo y
        que el propio sim emite— lo arregla sin que nadie tenga que acordarse de
        avisar.
        """
        stream = await _subscribe(EventType.WORLD_ROAD_CHANGED)
        if stream is None:
            return
        async for ev in stream:
            edge_id = ev.payload.get("edge_id")
            cut = bool(ev.payload.get("cut"))
            if edge_id is None or self.graph.resolve_edge(edge_id) is None:
                continue
            await self.apply_road_change(edge_id, cut, ev.payload.get("cause", ""))

    async def apply_road_change(self, reference: str, cut: bool, cause: str) -> None:
        """Aplica y pinta un cambio de carretera. Idempotente a propósito: el sim
        recibe también sus propios eventos, y repetir no puede costar nada."""
        edge_id = self.graph.resolve_edge(reference)
        if edge_id is None or self.graph.is_cut(edge_id) == cut:
            return
        if cut:
            self.graph.cut(edge_id, cause)
        else:
            self.graph.restore(edge_id)
        await self._render_road(edge_id, cut=cut)

    async def _loop(self) -> None:
        while self._running:
            await self.tick(TICK_S)
            await asyncio.sleep(TICK_S / self.speed)

    # --- el tick ---

    async def tick(self, dt: float = TICK_S) -> None:
        """Un segundo simulado. Público a propósito: el test lo llama a mano y no
        depende del reloj de pared."""
        self.t_sim += dt
        await self._emit(
            EventType.WORLD_TICK, {"t_sim": self.t_sim, "wind": self.hazard.wind}
        )
        await self._advance_hazard(dt)
        await self._advance_units(dt)
        await self._update_markers()
        for spec in self.injects.due(self.t_sim):
            await self.inject(spec.type, spec.payload)

    async def _advance_hazard(self, dt: float) -> None:
        for change in self.hazard.tick(dt):
            await self._emit(
                EventType.WORLD_CELL_CHANGED,
                {"cell_id": change.cell_id, "state": change.state,
                 "hazard": change.hazard},
            )
            # Render en el carril lento (D7): el frente puede ir un tick tarde,
            # el `/tp` de un replan no.
            for cmd in self.hazard.render_commands(change):
                await self.rcon.send(cmd, LOW)

    async def _advance_units(self, dt: float) -> None:
        """Interpola a 5 Hz y publica posición a 1 Hz, no a 5."""
        steps = max(int(TICK_HZ * dt), 1)
        for unit_id, (movement, _) in list(self._moving.items()):
            for _ in range(steps):
                if movement.done:
                    break
                x, z, yaw = movement.step(dt / steps)
                await self.rcon.send(
                    tp_command(unit_id, x, z, GROUND_Y + 1, yaw), HIGH
                )
            x, z, yaw = movement.position()
            unit = self.units[unit_id]
            self.units[unit_id] = unit.model_copy(update={"x": x, "z": z})
            await self._emit(
                EventType.WORLD_UNIT_POSITION,
                {"unit_id": unit_id, "x": x, "z": z, "heading": yaw,
                 "eta_s": movement.eta_s},
            )
            if movement.done:
                await self._arrive(unit_id)

    async def _update_markers(self) -> None:
        """Pinta cada POI según lo cerca que tenga el peligro.

        Rojo si arde algo a menos de `DANGER_RADIUS_M`, naranja si el frente ya
        pasó por ahí y dejó cicatriz, y su color propio si está limpio. Solo se
        manda el `fill` cuando el estado cambia: sin eso serían cinco comandos por
        tick compitiendo con el movimiento.
        """
        for poi_id, poi in self._pois.items():
            estado = self._threat(poi.x, poi.z)
            if self._marker_state.get(poi_id) != estado:
                await self._paint_marker(poi_id, estado)

    def hazard_cell_at(self, x: float, z: float) -> str:
        """Id de la celda del autómata que cubre esas coordenadas."""
        size = self.scenario.hazard.cell_size
        return cell_id(int(x // size), int(z // size))

    def _threat(self, x: float, z: float) -> str:
        cerca_ardiendo = cerca_quemado = False
        for cid, cell_state in self.hazard._state.items():
            if cell_state not in ("burning", "burnt"):
                continue
            x1, z1, x2, z2 = self.hazard.bounds(cid)
            if math.dist((x, z), ((x1 + x2) / 2, (z1 + z2) / 2)) > DANGER_RADIUS_M:
                continue
            if cell_state == "burning":
                cerca_ardiendo = True
                break
            cerca_quemado = True
        if cerca_ardiendo:
            return "danger"
        return "evacuating" if cerca_quemado else "base"

    async def _arrive(self, unit_id: str) -> None:
        movement, action_id = self._moving.pop(unit_id)
        await self._emit(
            EventType.WORLD_UNIT_ARRIVED,
            {"unit_id": unit_id, "waypoint_id": movement.route[-1]},
        )
        await self._status(unit_id, "idle", "llegada")
        await self._emit(
            EventType.ACTION_COMPLETED,
            {"action_id": action_id, "result": {"waypoint_id": movement.route[-1]}},
        )

    # --- los cuatro verbos ---

    async def execute(self, action_id: str, verb: str, args: dict) -> None:
        """Ejecuta una acción del core y confirma con `action.completed`.

        `verb` acepta exactamente `goto`, `set_marker`, `announce`, `rescue`.
        Cualquier otro emite `action.failed` con `error="unknown_verb"`.
        """
        if verb not in VERBS:
            await self._failed(action_id, "unknown_verb")
            return
        try:
            await getattr(self, f"_do_{verb}")(action_id, args)
        except Exception as exc:  # noqa: BLE001 — una acción mala no tumba el sim
            await self._failed(action_id, f"{type(exc).__name__}: {exc}")

    async def _do_goto(self, action_id: str, args: dict) -> None:
        """Acepta `route` (la que manda el core) o `waypoint_id` (un destino suelto).

        El core resuelve el camino él mismo: `Assignment.route` está documentado
        como "waypoint ids, ya resuelta" y `core.loop` emite `args={"unit_id",
        "route"}`. El sim acepta también un destino suelto porque es lo cómodo
        para un core tonto, para `POST /control` y para los tests.
        """
        unit_id = args["unit_id"]
        if unit_id not in self.units:
            await self._failed(action_id, f"unknown_unit:{unit_id}")
            return
        if self.units[unit_id].status == "unavailable":
            await self._failed(action_id, f"unit_unavailable:{unit_id}")
            return

        target = args.get("waypoint_id")
        given = args.get("route")
        if not given and target is None:
            await self._failed(action_id, "goto_sin_destino")
            return

        if given:
            desconocidos = [w for w in given if w not in self.graph.waypoint_ids]
            if desconocidos:
                await self._failed(action_id, f"unknown_waypoint:{desconocidos[0]}")
                return
            # La unidad puede no estar en el arranque de la ruta que manda el core
            # —viene de otra orden, o el plan la calculó desde su tarea—. Se le
            # antepone el trecho que falta en vez de teletransportarla al inicio.
            route = self._join(unit_id, list(given))
            target = given[-1]
        else:
            route = self._route_from(unit_id, target)

        if route is None:
            await self._failed(action_id, f"no_route:{target}")
            return

        # D2, por defecto: last-write-wins. La orden anterior de esa unidad se
        # cancela y se le avisa al core, que si no se queda esperando su
        # `action.completed` para siempre.
        if unit_id in self._moving:
            _, previous = self._moving.pop(unit_id)
            await self._failed(previous, "superseded")

        self._moving[unit_id] = (
            Movement(unit_id, route, DEFAULT_SPEED_MPS, self.graph), action_id
        )
        await self._status(unit_id, "moving", f"hacia {target}")

    async def _do_set_marker(self, action_id: str, args: dict) -> None:
        poi_id, state = args["poi_id"], args["state"]
        await self._paint_marker(poi_id, state)
        await self._completed(action_id, {"poi_id": poi_id, "state": state})

    async def _paint_marker(self, poi_id: str, state: str) -> None:
        """Repinta la plataforma de un POI. Lo usan el verbo del core y el
        marcado automático por cercanía del fuego."""
        poi = self._pois[poi_id]
        block = MARKER_BLOCKS.get(state, POI_STYLE[poi.kind][1])
        size = 12
        await self.rcon.send(
            f"fill {int(poi.x) - size} {GROUND_Y} {int(poi.z) - size} "
            f"{int(poi.x) + size} {GROUND_Y} {int(poi.z) + size} {block}",
            LOW,
        )
        self._marker_state[poi_id] = state

    async def _do_announce(self, action_id: str, args: dict) -> None:
        text = str(args["text"]).replace('"', "'")
        await self.rcon.send(f'title @a title {{"text":"{text}"}}', HIGH)
        await self.rcon.send("playsound minecraft:block.bell.use master @a", LOW)
        await self._completed(action_id, {"text": text})

    async def _do_rescue(self, action_id: str, args: dict) -> None:
        shelter = self._pois[args["shelter_id"]]
        rescatados = []
        for group_id in args.get("civ_ids", []):
            group = self.civilians.get(group_id)
            if group is None:
                continue
            self.civilians[group_id] = group.model_copy(update={"state": "safe"})
            await self.rcon.send(
                f"tp @e[tag={group_id}] {int(shelter.x)} {GROUND_Y + 1} "
                f"{int(shelter.z) + 16}",
                HIGH,
            )
            await self.rcon.send(
                f"effect give @e[tag={group_id}] glowing 60 0 true", LOW
            )
            await self._emit(
                EventType.WORLD_CIVILIANS_CHANGED,
                {"group_id": group_id, "count": group.count, "state": "safe",
                 "poi_id": shelter.id},
            )
            rescatados.append(group_id)
        await self._completed(action_id, {"rescued": rescatados})

    # --- injects ---

    async def inject(self, inject_type: str, payload: dict) -> None:
        """Dispara un inject, venga del YAML o de `POST /control/inject`."""
        await self._emit(
            EventType.WORLD_INJECT, {"inject_type": inject_type, "detail": payload}
        )
        if inject_type == WIND_SHIFT:
            self.hazard.set_wind(
                Wind(bearing_deg=payload["bearing"], speed=payload["speed"])
            )
        elif inject_type == ROAD_CUT:
            await self._cut(payload["edge"], payload.get("cause", "inject"))
        elif inject_type == UNIT_FAILURE:
            await self._fail_unit(payload["unit"], payload.get("reason", "avería"))

    async def _cut(self, reference: str, cause: str) -> None:
        # Canonizar antes de emitir: si el que corta nombró la carretera por sus
        # extremos, el evento tiene que llevar el id de siempre. Si no, el
        # dashboard ve dos `edge_id` distintos para la misma carretera según
        # quién la cortó, y el journal deja de poder casarlos.
        edge_id = self.graph.resolve_edge(reference)
        if edge_id is None:
            await self._emit(
                EventType.WORLD_ROAD_CHANGED,
                {"edge_id": reference, "cut": False, "cause": f"desconocida: {cause}"},
            )
            return
        self.graph.cut(edge_id, cause)
        await self._render_road(edge_id, cut=True)
        await self._emit(
            EventType.WORLD_ROAD_CHANGED,
            {"edge_id": edge_id, "cut": True, "cause": cause},
        )
        # Lo que hace que cortar una carretera signifique algo: las unidades que
        # ya iban por ahí tienen que recalcular. Sin esto, el camión sigue tan
        # tranquilo por una pista cortada mientras el dashboard dice otra cosa.
        for unit_id, (movement, action_id) in list(self._moving.items()):
            destino = movement.route[-1]
            nueva = self._route_from(unit_id, destino)
            if nueva is None:
                self._moving.pop(unit_id)
                await self._failed(action_id, f"route_cut:{edge_id}")
                await self._status(unit_id, "idle", "sin ruta")
            elif nueva != movement.route:
                self._moving[unit_id] = (
                    Movement(unit_id, nueva, DEFAULT_SPEED_MPS, self.graph), action_id
                )
                await self._status(unit_id, "moving", f"desvío por {edge_id} cortada")

    async def _fail_unit(self, unit_id: str, reason: str) -> None:
        if unit_id in self._moving:
            _, action_id = self._moving.pop(unit_id)
            await self._failed(action_id, f"unit_failure:{reason}")
        await self._status(unit_id, "unavailable", reason)

    # --- estado y utilidades ---

    def snapshot(self) -> dict[str, Any]:
        """Solo para depurar. El estado de verdad lo construye el core."""
        return {
            "run_id": self.run_id,
            "t_sim": self.t_sim,
            "wind": self.hazard.wind.model_dump(),
            "units": {u: self.units[u].model_dump() for u in self.units},
            "moving": {u: m.route for u, (m, _) in self._moving.items()},
            "burning": self.hazard.burning,
            "cut_roads": [r.id for r in self.scenario.roads if self.graph.is_cut(r.id)],
            "injects_fired": [i.type for i in self.injects.fired],
            "unpublished": len(_FALLBACK),
        }

    async def _render_road(self, edge_id: str, cut: bool) -> None:
        """Pinta el tramo. Carril lento: es decorado, no puede adelantar a un `/tp`."""
        edge = next((r for r in self.scenario.roads if r.id == edge_id), None)
        if edge is None:
            return
        a = self.graph.position_of(edge.a)
        b = self.graph.position_of(edge.b)
        await self.rcon.send_many(road_cut_commands(a, b, cut), LOW)

    def _join(self, unit_id: str, route: list[str]) -> list[str] | None:
        """Pega la unidad al principio de una ruta que viene ya resuelta."""
        origen = self._nearest(unit_id)
        if origen == route[0]:
            return route
        if origen in route:  # ya va por esa ruta, más adelantada
            return route[route.index(origen):]
        acceso = self.graph.shortest_path(origen, route[0])
        return None if acceso is None else acceso[:-1] + route

    def _nearest(self, unit_id: str) -> str:
        unit = self.units[unit_id]
        return min(
            self.graph.waypoint_ids,
            key=lambda w: (
                (self.graph.position_of(w)[0] - unit.x) ** 2
                + (self.graph.position_of(w)[1] - unit.z) ** 2
            ),
        )

    def _route_from(self, unit_id: str, waypoint_id: str) -> list[str] | None:
        """De dónde sale la unidad: del waypoint más cercano a su posición real,
        no del origen de su última ruta."""
        return self.graph.shortest_path(self._nearest(unit_id), waypoint_id)

    async def _status(self, unit_id: str, status: str, reason: str) -> None:
        self.units[unit_id] = self.units[unit_id].model_copy(
            update={"status": status}
        )
        await self._emit(
            EventType.WORLD_UNIT_STATUS,
            {"unit_id": unit_id, "status": status, "reason": reason},
        )

    async def _completed(self, action_id: str, result: dict) -> None:
        await self._emit(
            EventType.ACTION_COMPLETED, {"action_id": action_id, "result": result}
        )

    async def _failed(self, action_id: str, error: str) -> None:
        await self._emit(
            EventType.ACTION_FAILED, {"action_id": action_id, "error": error}
        )

    async def _emit(self, event_type: EventType, payload: dict) -> None:
        await _publish(
            Event(
                run_id=self.run_id,
                seq=0,  # lo sella el bus
                t_wall=datetime.now(UTC),
                t_sim=self.t_sim,
                type=event_type,
                source="sim",
                payload=payload,
            )
        )
