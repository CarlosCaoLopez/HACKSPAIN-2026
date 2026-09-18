"""El guion de la demo. P4.

Seis minutos, un solo clímax: una llamada real cuelga y las unidades giran en
pantalla en menos de 3 segundos.

`--mock-calls` es el plan B nivel 2: reproduce un audio grabado y publica los
mismos eventos. Probadlo de verdad.

---

Este script **supervisa, no reimplementa**. Levanta el gateway, le pide un run y se
queda mirando el mismo chorro de eventos que ve el dashboard para ir cantando la línea
temporal del backbone por consola. Todo lo que hace pasa por la API pública:

    uvicorn gateway.main:app   →   POST /api/run   →   ws://…/ws   →   POST /api/run/stop

Cuatro decisiones que explican la forma del fichero:

1. **Arranca en `VELA_MODE=dev`, no en `demo`.** El modo `demo` arranca el run él solo
   (`main.lifespan`) y entonces no hay dónde meter las banderas: el run lo pide este
   script, con `--no-minecraft` y `--mock-calls` dentro del cuerpo del POST.
2. **Solo biblioteca estándar para el HTTP.** `httpx` es dependencia de desarrollo
   (`pyproject.toml`) y esto corre en la demo: tres peticiones se hacen con `urllib` y
   no se añade una dependencia al camino crítico del domingo.
3. **El colgar → giro se mide con `HangupToTurn`**, el mismo código que puntúa los
   journals en `gateway.score_fallback`. Si el número del pitch y el número del panel
   salieran de dos implementaciones distintas, un día no coincidirían y sería en el
   escenario.
4. **Nada de `pnpm` desde aquí.** El dashboard se levanta aparte (`make dev-dash` o un
   build servido): meter Node en el árbol de procesos de la demo es un modo de fallo
   más, y el dashboard es justo lo que no se puede caer.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from contracts.events import Event, EventType
from gateway.score_fallback import HangupToTurn

SCENARIO_DEFAULT = "wildfire_ridge"

HOST, PORT = "127.0.0.1", 8000
BASE = f"http://{HOST}:{PORT}"
WS = f"ws://{HOST}:{PORT}/ws"

BOOT_TIMEOUT_S = 20.0
"""Lo que se le da a uvicorn para responder `/api/health`. Con el sim y el core sin
cuerpo arranca en menos de un segundo; con todo montado, en tres o cuatro."""

STOP_GRACE_S = 5.0


# --- Lo que se canta por consola --------------------------------------------------
#
# Es mi chuleta mientras hablo: los hitos de la tabla del backbone y nada más. Un log
# completo en la consola de la demo es un log que no se lee.

TURNING_POINTS: dict[EventType, str] = {
    EventType.RUN_STARTED: "arranca el run",
    EventType.WORLD_FIRE_DETECTED: "ignición detectada",
    EventType.PLAN_POLICY_EMITTED: "política del planner",
    EventType.PLAN_EMITTED: "plan nuevo del solver",
    EventType.WORLD_INJECT: "INJECT",
    EventType.CALL_STARTED: "llamada en curso",
    EventType.CALL_ENDED: "llamada terminada",
    EventType.PLAN_REPLAN_STARTED: "REPLAN",
    EventType.PLAN_VIOLATION: "violación de restricción",
    EventType.ACTION_FAILED: "acción fallida",
    EventType.EVENT_MALFORMED: "evento malformado",
    EventType.HUMAN_OVERRIDE: "intervención humana",
    EventType.RUN_ENDED: "fin del run",
}


def line(ev: Event) -> str:
    """Una línea por hito, con `t_sim` delante para poder seguirla con el dashboard."""
    what = TURNING_POINTS[ev.type]
    detail = _detail(ev)
    mm, ss = divmod(int(ev.t_sim), 60)
    return f"  {mm:02d}:{ss:02d}  {what}{' · ' + detail if detail else ''}"


def _detail(ev: Event) -> str:
    """La frase que importa de cada hito. Lo que se dice en voz alta, no el payload."""
    p = ev.payload
    match ev.type:
        case EventType.PLAN_POLICY_EMITTED | EventType.PLAN_REPLAN_STARTED:
            return str(p.get("rationale") or p.get("reason") or "")
        case EventType.PLAN_EMITTED:
            n = len(p.get("assignments") or [])
            return f"{n} asignaciones"
        case EventType.WORLD_INJECT:
            return str(p.get("inject_type", ""))
        case EventType.CALL_STARTED | EventType.CALL_ENDED:
            head = f"{p.get('direction', '')} {p.get('call_id', '')}".strip()
            return f"{head} · {p['outcome']}" if p.get("outcome") else head
        case EventType.PLAN_VIOLATION | EventType.EVENT_MALFORMED:
            return str(p.get("message") or p.get("error") or "")
        case EventType.ACTION_FAILED:
            return f"{p.get('action_id', '')} · {p.get('error', '')}"
        case EventType.HUMAN_OVERRIDE:
            return f"{p.get('kind', '')} → {p.get('target', '')}"
        case EventType.RUN_ENDED:
            return f"score {p['score']}" if p.get("score") is not None else ""
        case _:
            return str(p.get("scenario_id", ""))


# --- HTTP con biblioteca estándar -------------------------------------------------


def _request(method: str, path: str, body: dict | None = None) -> dict:
    # `BASE` es siempre 127.0.0.1: aquí no se construye una URL con nada que venga de
    # fuera, que es lo que haría peligroso un `urlopen`.
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"content-type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read() or b"{}")


async def request(method: str, path: str, body: dict | None = None) -> dict:
    """`urllib` bloquea; el hilo no. Son tres peticiones en toda la demo."""
    return await asyncio.to_thread(_request, method, path, body)


# --- Arranque y parada ------------------------------------------------------------


def spawn_gateway(speed: float) -> subprocess.Popen[bytes]:
    """uvicorn en su propio proceso, con el entorno que tocan las banderas."""
    env = os.environ | {
        "VELA_MODE": "dev",  # el run lo pide este script, con sus banderas
        "VELA_REPLAY_SPEED": str(speed),
        "PYTHONUNBUFFERED": "1",
    }
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "gateway.main:app", "--port", str(PORT)],
        env=env,
    )


async def wait_for_gateway() -> dict:
    """Hasta que `/api/health` conteste. Sin esto, el POST del run sale antes de que
    uvicorn tenga el socket y la demo se cae en el segundo cero."""
    deadline = time.monotonic() + BOOT_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            return await request("GET", "/api/health")
        except (urllib.error.URLError, OSError, TimeoutError):
            await asyncio.sleep(0.2)
    raise SystemExit(f"el gateway no respondió en {BOOT_TIMEOUT_S:.0f} s")


# --- El bucle ---------------------------------------------------------------------


async def follow(turns: HangupToTurn, started: float) -> None:
    """El mismo chorro que ve el dashboard, cantado por consola.

    Se conecta al WS público en vez de leer `runs/<run_id>.jsonl` porque el writer es de
    P1 y hoy no tiene cuerpo: sin esto, la demo no tendría de dónde sacar el guion.
    """
    import websockets  # dependencia ya declarada por el gateway (uvicorn[standard])

    async with websockets.connect(WS, max_size=None) as ws:
        async for raw in ws:
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if frame.get("kind") != "event":
                continue  # el snapshot inicial no es un hito del guion
            try:
                ev = Event.model_validate(frame["event"])
            except Exception as exc:  # noqa: BLE001 — un evento raro no para la demo
                print(f"  (evento ilegible, sigo · {exc})")
                continue

            delta = turns.feed(ev)
            if delta is not None:
                # EL número. Se canta en cuanto pasa, no al final: es el momento en el
                # que hay que mirar la pantalla.
                print(f"  ⏱  COLGAR → GIRO · {delta:.2f} s (tiempo real)")
            if ev.type in TURNING_POINTS:
                print(line(ev))
            if ev.type == EventType.RUN_ENDED:
                print(f"\n  duración de pared: {time.monotonic() - started:.1f} s")
                return


def report(turns: HangupToTurn) -> None:
    """Lo que va a `docs-nacho/ensayo.md` después de cada pasada."""
    print("\n--- la pasada ---")
    if not turns.turns:
        print("  colgar → giro: sin medir (ninguna llamada produjo una orden)")
    else:
        for seq, delta in turns.turns:
            print(f"  colgar (seq {seq}) → giro: {delta:.2f} s")
        mean = turns.mean_s or 0.0
        verdict = "por debajo de 3 s" if mean < 3.0 else "POR ENCIMA DE 3 s"
        print(f"  media: {mean:.2f} s · {verdict}")
    if turns.unresolved:
        print(f"  {turns.unresolved} llamada(s) sin orden que descienda de ellas")


def _terminate(gateway: subprocess.Popen[bytes]) -> None:
    """Cortesía, y luego a la fuerza. Nunca lanza: es lo último que corre."""
    with contextlib.suppress(Exception):
        gateway.terminate()
    with contextlib.suppress(Exception):
        gateway.wait(timeout=STOP_GRACE_S)
    if gateway.poll() is None:
        with contextlib.suppress(Exception):
            gateway.kill()


async def run_demo(
    scenario_id: str,
    mock_calls: bool,
    *,
    minecraft: bool = True,
    speed: float = 1.0,
) -> None:
    """Arranca el gateway, lanza el run y sigue la línea temporal del backbone."""
    gateway = spawn_gateway(speed)
    turns = HangupToTurn()
    try:
        health = await wait_for_gateway()
        print(f"gateway arriba · componentes: {health['components']}")

        run = await request(
            "POST",
            "/api/run",
            {
                "scenario_id": scenario_id,
                "minecraft": minecraft,
                "mock_calls": mock_calls,
                "speed": speed,
            },
        )
        # Se imprime lo APLICADO, no lo pedido: `speed` vuelve diciendo si el sim aceptó
        # el multiplicador o si el mundo va a 1× de todas formas (REQ-172).
        print(
            f"run {run['run_id']} · escenario {run['scenario_id']} · "
            f"minecraft {'sí' if run['minecraft'] else 'NO'} · "
            f"llamadas {'simuladas' if run['mock_calls'] else 'reales'} · "
            f"velocidad {run['speed']}"
        )
        print("dashboard: http://localhost:5173 (pnpm dev) · guion:\n")

        started = time.monotonic()
        await follow(turns, started)
    except KeyboardInterrupt:
        print("\ncorte a mano: cierro el run")
    finally:
        # Anidado a propósito: pase lo que pase ahí dentro —una excepción al imprimir, o
        # un `CancelledError` en el `await`, que `suppress(Exception)` no atrapa porque no
        # es `Exception`— uvicorn se muere. Un gateway huérfano se queda con el puerto
        # 8000 y el siguiente ensayo arranca contra el proceso viejo sin que se note.
        try:
            report(turns)
            # El run se para SIEMPRE, también tras un Ctrl-C: si no, el journal queda
            # abierto y `/api/runs` lo lee como incompleto en la comparación de después.
            with contextlib.suppress(Exception):
                await request("POST", "/api/run/stop")
        finally:
            _terminate(gateway)


# --- La línea de comandos ---------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """`--scenario`, `--mock-calls`, `--speed`, `--no-minecraft` (plan B nivel 3)."""
    ap = argparse.ArgumentParser(description="El guion de la demo · vela")
    ap.add_argument("--scenario", default=SCENARIO_DEFAULT)
    ap.add_argument(
        "--mock-calls",
        action="store_true",
        help="plan B nivel 2: telefonía simulada (voice.fake) en vez de real",
    )
    ap.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="multiplicador de tiempo. 1 = tiempo real, que es como se ensaya",
    )
    ap.add_argument(
        "--no-minecraft",
        action="store_true",
        help="plan B nivel 3: ni se abre el socket RCON. El dashboard solo",
    )
    return ap.parse_args()


def main() -> None:
    # La consola de Windows viene en cp1252 y el "⏱" la tumba con UnicodeEncodeError.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    args = parse_args()
    if not Path(f"scenarios/{args.scenario}.yaml").exists():
        raise SystemExit(f"no existe scenarios/{args.scenario}.yaml")

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            run_demo(
                args.scenario,
                args.mock_calls,
                minecraft=not args.no_minecraft,
                speed=args.speed,
            )
        )


if __name__ == "__main__":
    main()
