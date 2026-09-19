"""Cámara automática y narración en vivo. P2 mueve la cámara durante la demo.

Sigue el journal del run —el mismo fichero que escribe el bus— y decide a dónde
mirar según lo que está pasando: el frente de fuego, la unidad que se mueve, el
sitio donde acaba de caer un inject. Y lo va contando por consola.

No se mete en el proceso de la demo ni le pide nada: **solo lee el journal y
manda `/tp` por RCON**. Si se cae, la demo no se entera; si la demo se cae, esto
deja de ver líneas nuevas y calla. Es la propiedad que quieres en algo que corre
en paralelo mientras el jurado mira.

    uv run python -m sim.director --player <usuario>
"""

import argparse
import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from contracts.settings import settings
from sim.camera import shots
from sim.rcon import HIGH, RconClient
from sim.scenario import load
from sim.worldgen import GROUND_Y

RUNS = Path("runs")
POLL_S = 0.4
"""Cada cuánto se mira si el journal ha crecido."""

HOLD_S = 9.0
"""Lo que dura un plano antes de buscar otro, salvo que algo lo interrumpa.

Menos de esto marea; más y se pierde la acción. Un inject o una llegada sí cortan
en seco, porque son justo lo que hay que ver."""

FOCO_S = 12.0
"""Lo que un foco puntual manda sobre las heurísticas.

Hay hitos que son un momento, no un estado —la llamada al pueblo, el replan, el
pin del vecino—: cuando llega el evento, la cámara va allí y se queda este rato
aunque el fuego siga ardiendo. Pasado el foco, vuelve a mandar lo que se mueve."""

FOCO_LLAMADA_MAX_S = 180.0
"""Techo del plano que espera a que cuelguen.

Una llamada de despacho no dura un momento, dura lo que dure: medido, treinta
segundos. `FOCO_S` caducaba a los doce y la cámara se iba al fuego con los camiones
todavía parados, que es justo el beat que hay que enseñar. Así que ese foco no vence
por tiempo sino con el `call.ended` de su tarea — pero con techo, porque una llamada
que no cierra nunca no puede dejar la cámara clavada. El core suelta a la unidad a
los 150 s de conversación (`core.loop.DISPATCH_TALK_S`); esto va por encima. Medido:
la llamada real al retén duró 66 s, así que el techo de 75 que puse primero se
quedaba a nueve segundos de cortarla."""

DISPATCH_ROLES = {"fire_crew", "ambulance"}
"""Los `role` de llamada que retienen a su unidad en el core (`core.loop`). Son los
únicos en los que lo que hay que encuadrar es la unidad quieta, no el destino."""

CALLEE = {
    "fire_crew": "el retén",
    "ambulance": "la ambulancia",
    "ambulance_queued": "la ambulancia ocupada",
    "evacuation": "el pueblo que arde",
    "neighbor_alert": "el pueblo vecino",
}


@dataclass
class Escena:
    """Lo que el director sabe del mundo, reconstruido del journal."""

    t_sim: float = 0.0
    unidades: dict[str, tuple[float, float]] = field(default_factory=dict)
    moviendo: set[str] = field(default_factory=set)
    ardiendo: set[tuple[int, int]] = field(default_factory=set)
    pois: dict[str, tuple[float, float]] = field(default_factory=dict)
    cell_size: int = 4
    calls_por_task: dict[str, str] = field(default_factory=dict)
    """task_id → poi_id, de `call.requested`: `call.started` no trae el POI."""
    despacho_por_task: dict[str, tuple[str, str]] = field(default_factory=dict)
    """task_id → (unit_id, role) de una llamada que RETIENE a su unidad. Lo que hay
    que encuadrar entonces es dónde está esa unidad parada, no a dónde iría."""
    ultima_orden: str | None = None
    """La última unidad que recibió un `goto`: es la que acaba de girar."""

    def centro_del_fuego(self) -> tuple[float, float] | None:
        if not self.ardiendo:
            return None
        xs = [x for x, _ in self.ardiendo]
        zs = [z for _, z in self.ardiendo]
        return sum(xs) / len(xs), sum(zs) / len(zs)


def mirando_a(x: float, y: float, z: float, ox: float, oz: float) -> tuple[float, float]:
    """(yaw, pitch) para que una cámara en (x,y,z) mire a (ox, GROUND_Y, oz).

    En Minecraft yaw 0 es sur y crece hacia el oeste; de ahí el `-dx`.
    """
    dx, dz = ox - x, oz - z
    yaw = math.degrees(math.atan2(-dx, dz))
    pitch = math.degrees(math.atan2(y - GROUND_Y, math.hypot(dx, dz)))
    return yaw, pitch


def plano_sobre(ox: float, oz: float, dist: float, alto: float) -> tuple:
    """Cámara a `dist` al suroeste del objetivo y `alto` por encima, mirándolo."""
    x, z = ox - dist * 0.7, oz + dist * 0.7
    y = GROUND_Y + alto
    yaw, pitch = mirando_a(x, y, z, ox, oz)
    return x, y, z, yaw, pitch


class Director:
    def __init__(self, escenario: Path, player: str, mover: bool = True) -> None:
        s = load(escenario)
        self.player = player
        self.mover = mover
        """Si es falso, narra y sugiere el plano pero no manda un solo `/tp`.

        `sim.camera` y esto le mandan `/tp` al MISMO jugador y no se coordinan: un
        evento urgente aquí salta la histéresis y le roba el plano al que está
        pulsando teclas en menos de medio segundo, y el `gamemode spectator` del
        arranque rompe el modo `--follow`, que necesita creativo para leer la ranura.
        Con `--narrar-solo` los dos conviven: el humano conduce y esto le va diciendo
        qué debería estar mirando."""
        # Las unidades se siembran del escenario, no solo de `world.unit.position`:
        # el sim solo emite posición cuando algo se mueve, y una unidad RETENIDA no
        # se ha movido nunca. Sin esto no se la puede encuadrar, que es el caso.
        self.escena = Escena(
            pois={p.id: (p.x, p.z) for p in s.pois},
            unidades={u.id: (u.x, u.z) for u in s.units},
            cell_size=s.hazard.cell_size,
        )
        self._valle_shot = shots(s)["valle"]
        self.xs = [p.x for p in s.pois] + [w.x for w in s.waypoints]
        self.zs = [p.z for p in s.pois] + [w.z for w in s.waypoints]
        self._ultimo_corte = 0.0
        self._plano = ""
        self._foco: tuple[str, tuple] | None = None
        self._foco_hasta = 0.0
        self._foco_task: str | None = None
        """Si el foco espera a que cuelgue una llamada, su `task_id`."""

    # --- leer el journal ---

    def aplicar(self, ev: dict) -> str | None:
        """Actualiza la escena. Devuelve una línea de narración si merece contarse."""
        e, p = ev.get("type"), ev.get("payload", {})
        esc = self.escena

        if e == "world.tick":
            esc.t_sim = p.get("t_sim", esc.t_sim)
        elif e == "world.unit.position":
            esc.unidades[p["unit_id"]] = (p["x"], p["z"])
        elif e == "world.unit.status":
            u, st = p["unit_id"], p.get("status")
            (esc.moviendo.add if st == "moving" else esc.moviendo.discard)(u)
            if st == "unavailable":
                return f"{u} fuera de servicio · {p.get('reason', '')}"
        elif e == "world.cell.changed":
            cx, cz = (int(v) * esc.cell_size for v in p["cell_id"].split("_")[1:])
            if p["state"] == "burning":
                esc.ardiendo.add((cx, cz))
            elif p["state"] == "burnt":
                esc.ardiendo.discard((cx, cz))
                if p.get("cause") == "extinguished":
                    return f"un camión apaga {p['cell_id']}"
        elif e == "world.fire.detected":
            return f"IGNICIÓN en {p.get('cell_id')}"
        elif e == "world.inject":
            return f"INJECT · {p.get('inject_type')} · {p.get('detail')}"
        elif e == "world.road.changed" and p.get("cut"):
            return f"CARRETERA CORTADA · {p.get('edge_id')} · {p.get('cause', '')}"
        elif e == "world.unit.arrived":
            return f"{p['unit_id']} llega a {p.get('waypoint_id')}"
        elif e == "action.requested":
            a = p.get("args", {})
            if p.get("verb") == "goto" and a.get("unit_id"):
                esc.ultima_orden = a["unit_id"]
            destino = a.get("waypoint_id") or (a.get("route") or [""])[-1]
            return f"orden: {p.get('verb')} {a.get('unit_id', '')} → {destino}"
        elif e == "action.failed":
            return f"falla una orden · {p.get('error')}"
        elif e == "plan.policy.emitted":
            # Ver el tablero entero mientras el agente decide a qué frente ir.
            self._set_foco("el valle · política", self._valle())
            return "el modelo emite política nueva"
        elif e == "plan.replan.started":
            # Solo los replans "de demo" —divergencia (viento) y restricción dura
            # (carretera cortada)— merecen plano. El retasking rutinario
            # (`tasks_changed`) salta decenas de veces; si cada uno agarrase el
            # foco, la cámara se quedaría pegada al valle toda la segunda mitad.
            # Ese se narra en minúscula (no urgente) y deja que el fuego y las
            # unidades manden.
            if p.get("trigger") in ("divergence", "hard_violation"):
                self._set_foco("el valle · replan", self._valle())
                return f"REPLAN · {p.get('reason', '')}"
            return f"replan · {p.get('reason', '')}"
        elif e == "call.requested":
            if p.get("task_id") and p.get("poi_id"):
                esc.calls_por_task[p["task_id"]] = p["poi_id"]
            facts = p.get("facts") or {}
            rol, unidad = facts.get("role", ""), facts.get("unit_id", "")
            if rol in DISPATCH_ROLES and unidad and p.get("task_id"):
                esc.despacho_por_task[p["task_id"]] = (unidad, rol)
            return None
        elif e == "call.started":
            direction = p.get("direction")
            task_id = p.get("task_id") or ""
            poi_id = esc.calls_por_task.get(task_id)
            despacho = esc.despacho_por_task.get(task_id)
            if direction == "outbound" and despacho:
                # Llamada de despacho: la unidad está PARADA esperando a que
                # contesten, y lo que hay que ver es eso. El POI de la llamada no
                # sirve — el de la ambulancia es el destino del rescate, no el
                # hospital donde ella sigue sin arrancar.
                unidad, rol = despacho
                ux, uz = esc.unidades.get(unidad, esc.pois.get(poi_id or "", (0.0, 0.0)))
                self._set_foco(
                    f"{unidad} espera al teléfono",
                    plano_sobre(ux, uz, 30, 14),
                    hasta_task=task_id,
                )
                return f"PIDIENDO MEDIOS · {CALLEE.get(rol, rol)} · {unidad} retenida"
            if direction == "outbound" and poi_id in esc.pois:
                px, pz = esc.pois[poi_id]
                self._set_foco(f"llamada a {poi_id}", plano_sobre(px, pz, 30, 14))
                rol = (esc.despacho_por_task.get(task_id) or ("", ""))[1]
                quien = CALLEE.get(rol) or poi_id
                return f"LLAMADA saliente · {quien}"
            if direction == "inbound":
                self._set_foco("el valle · llamada entrante", self._valle())
                return "LLAMADA entrante · ubicación desconocida"
            return f"LLAMADA en curso · {p.get('to') or direction or ''}"
        elif e == "citizen.location":
            x, z = p.get("x"), p.get("z")
            donde = p.get("poi_name") or p.get("poi_id") or "el pin"
            if x is not None and z is not None:
                self._set_foco(f"vecino · {donde}", plano_sobre(x, z, 30, 14))
            return f"UBICACIÓN del vecino → {donde}"
        elif e == "call.ended":
            task_id = p.get("task_id") or ""
            despacho = esc.despacho_por_task.pop(task_id, None)
            if self._foco_task and self._foco_task == task_id:
                # Se acabó la espera: se suelta el foco para que mande la heurística
                # de "sigue a la unidad que acaba de recibir orden", y la nota va en
                # MAYÚSCULA para que `_quizas_mover` la trate como urgente y corte en
                # seco. Ese corte ES el beat: los camiones arrancando a la vez.
                self._foco, self._foco_task, self._foco_hasta = None, None, 0.0
            if despacho:
                return f"CUELGAN · sale {despacho[0]}"
            return "llamada terminada"
        elif e == "world.fact.asserted":
            return f"hecho: {p.get('key')} = {p.get('value')} ({p.get('confidence', '')})"
        elif e == "world.civilians.changed":
            return f"civiles {p.get('group_id')} → {p.get('state')}"
        return None

    # --- decidir el plano ---

    def _set_foco(self, nombre: str, plano: tuple, hasta_task: str | None = None) -> None:
        """Fija un plano puntual que manda sobre las heurísticas.

        Sin `hasta_task` dura `FOCO_S`, que es lo que dura un hito puntual. Con él
        dura hasta el `call.ended` de esa tarea, con `FOCO_LLAMADA_MAX_S` de techo:
        una llamada de despacho no es un momento, es una espera, y la espera es
        justo lo que hay que enseñar."""
        self._foco = (nombre, plano)
        self._foco_task = hasta_task
        self._foco_hasta = time.monotonic() + (
            FOCO_LLAMADA_MAX_S if hasta_task else FOCO_S
        )

    def _valle(self) -> tuple:
        """Cenital que encuadra el valle entero. Es el plano de fondo y el de replan.

        Es **el mismo** que la tecla 1 de `sim.camera`, no uno parecido: cuando cada
        uno se lo calculaba por su cuenta, este salía a y=204 y el de las teclas a
        y=175, y los 204 están por encima del techo de niebla que `camera.py`
        documenta como medido en el servidor — el plano salía lavado y gris. Un
        encuadre, una implementación."""
        v = self._valle_shot
        return (v.x, v.y, v.z, v.yaw, v.pitch)

    def elegir(self, urgente: bool) -> tuple[str, tuple]:
        esc = self.escena

        # Un foco puntual (llamada, replan, pin del vecino) manda mientras dura.
        if self._foco and time.monotonic() < self._foco_hasta:
            return self._foco

        fuego = esc.centro_del_fuego()

        # La unidad que acaba de recibir orden es la que gira: síguela a ella.
        if esc.ultima_orden in esc.moviendo and esc.ultima_orden in esc.unidades:
            x, z = esc.unidades[esc.ultima_orden]
            return f"siguiendo a {esc.ultima_orden}", plano_sobre(x, z, 34, 20)

        # Si no, cualquier unidad en marcha: es lo único que se mueve de verdad.
        if esc.moviendo:
            u = min(esc.moviendo)
            if u in esc.unidades:
                x, z = esc.unidades[u]
                return f"siguiendo a {u}", plano_sobre(x, z, 34, 20)

        if fuego and (urgente or len(esc.ardiendo) > 3):
            return "el frente de fuego", plano_sobre(*fuego, 48, 26)

        # Nada se mueve: plano general, que se vea el valle entero.
        return "el valle", self._valle()

    async def correr(self, journal: Path) -> None:
        rcon = RconClient(settings.rcon_host, settings.rcon_port, settings.rcon_password)
        await rcon.connect()
        if self.mover:
            await rcon.send(f"gamemode spectator {self.player}", HIGH)
        modo = (
            f"cámara sobre {self.player}"
            if self.mover
            else "SOLO NARRA (no toca la cámara)"
        )
        print(f"director · sigue {journal.name} · {modo}\n", flush=True)

        with journal.open() as fh:
            fh.seek(0, 2)  # solo lo que pase a partir de ahora
            while True:
                linea = fh.readline()
                if not linea:
                    await self._quizas_mover(rcon, urgente=False)
                    await asyncio.sleep(POLL_S)
                    continue
                try:
                    ev = json.loads(linea)
                except json.JSONDecodeError:
                    continue
                nota = self.aplicar(ev)
                if nota:
                    m, s = divmod(int(self.escena.t_sim), 60)
                    print(f"  {m}:{s:02d}  {nota}", flush=True)
                    urgente = nota[0].isupper() or "apaga" in nota
                    await self._quizas_mover(rcon, urgente)

    async def _quizas_mover(self, rcon: RconClient, urgente: bool) -> None:
        ahora = time.monotonic()
        if not urgente and ahora - self._ultimo_corte < HOLD_S:
            return
        nombre, (x, y, z, yaw, pitch) = self.elegir(urgente)
        if nombre == self._plano and not urgente:
            return
        self._ultimo_corte, self._plano = ahora, nombre
        if not self.mover:
            # Narrando solo: se dice qué habría que mirar y conduce el humano.
            print(f"         [cámara sugerida: {nombre}]", flush=True)
            return
        await rcon.send(
            f"tp {self.player} {x:.0f} {y:.0f} {z:.0f} {yaw:.0f} {pitch:.0f}", HIGH
        )
        print(f"         [cámara: {nombre}]", flush=True)


def ultimo_journal() -> Path | None:
    jl = sorted(RUNS.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return jl[-1] if jl else None


def main() -> None:
    ap = argparse.ArgumentParser(description="Cámara automática y narración · vela")
    ap.add_argument("--player", required=True)
    ap.add_argument("--scenario", default="scenarios/wildfire_ridge.yaml")
    ap.add_argument("--journal", help="por defecto, el run más reciente de runs/")
    ap.add_argument(
        "--narrar-solo",
        action="store_true",
        help="narra y sugiere plano sin mover la cámara · para correr a la vez que `make cam`",
    )
    args = ap.parse_args()

    j = Path(args.journal) if args.journal else ultimo_journal()
    if j is None:
        raise SystemExit("no hay ningún journal en runs/: arranca la demo primero")
    d = Director(Path(args.scenario), args.player, mover=not args.narrar_solo)
    try:
        asyncio.run(d.correr(j))
    except KeyboardInterrupt:
        print("\ndirector parado")


if __name__ == "__main__":
    main()
