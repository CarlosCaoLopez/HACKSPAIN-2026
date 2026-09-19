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
    def __init__(self, escenario: Path, player: str) -> None:
        s = load(escenario)
        self.player = player
        self.escena = Escena(
            pois={p.id: (p.x, p.z) for p in s.pois}, cell_size=s.hazard.cell_size
        )
        self.xs = [p.x for p in s.pois] + [w.x for w in s.waypoints]
        self.zs = [p.z for p in s.pois] + [w.z for w in s.waypoints]
        self._ultimo_corte = 0.0
        self._plano = ""
        self._foco: tuple[str, tuple] | None = None
        self._foco_hasta = 0.0

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
            return None
        elif e == "call.started":
            direction = p.get("direction")
            poi_id = esc.calls_por_task.get(p.get("task_id") or "")
            if direction == "outbound" and poi_id in esc.pois:
                px, pz = esc.pois[poi_id]
                self._set_foco(f"llamada a {poi_id}", plano_sobre(px, pz, 30, 14))
                return f"LLAMADA saliente · {poi_id}"
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
            return "llamada terminada"
        elif e == "world.fact.asserted":
            return f"hecho: {p.get('key')} = {p.get('value')} ({p.get('confidence', '')})"
        elif e == "world.civilians.changed":
            return f"civiles {p.get('group_id')} → {p.get('state')}"
        return None

    # --- decidir el plano ---

    def _set_foco(self, nombre: str, plano: tuple) -> None:
        """Fija un plano puntual que manda sobre las heurísticas durante `FOCO_S`."""
        self._foco = (nombre, plano)
        self._foco_hasta = time.monotonic() + FOCO_S

    def _valle(self) -> tuple:
        """Cenital que encuadra el valle entero. Es el plano de fondo y el de replan."""
        cx = (min(self.xs) + max(self.xs)) / 2
        cz = (min(self.zs) + max(self.zs)) / 2
        alto = 1.15 * (max(self.zs) - min(self.zs)) / (2 * math.tan(math.radians(35)))
        return (cx, GROUND_Y + alto, cz, -90, 90)

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
        await rcon.send(f"gamemode spectator {self.player}", HIGH)
        print(
            f"director · sigue {journal.name} · cámara sobre {self.player}\n", flush=True
        )

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
    args = ap.parse_args()

    j = Path(args.journal) if args.journal else ultimo_journal()
    if j is None:
        raise SystemExit("no hay ningún journal en runs/: arranca la demo primero")
    d = Director(Path(args.scenario), args.player)
    try:
        asyncio.run(d.correr(j))
    except KeyboardInterrupt:
        print("\ndirector parado")


if __name__ == "__main__":
    main()
