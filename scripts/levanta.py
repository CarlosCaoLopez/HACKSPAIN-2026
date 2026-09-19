"""Levanta todo lo que la demo necesita, y dice en voz alta lo que no puede levantar.

    uv run python scripts/levanta.py                    # comprueba y levanta lo que falte
    uv run python scripts/levanta.py --check            # solo diagnostica, no toca nada
    uv run python scripts/levanta.py --player <usuario> # además engancha el director
    uv run python scripts/levanta.py --demo             # y lanza la demo (LLAMADAS REALES)
    uv run python scripts/levanta.py --mock             # la demo en seco, sin marcar a nadie

Esto no es un lanzador: es una **comprobación previa**. El valor no está en arrancar
procesos —eso ya lo hace el Makefile— sino en cazar las cinco cosas que arruinan un
ensayo sin decir por qué. Las cinco están medidas, no supuestas:

1. **Un gateway huérfano en el 8000.** `scripts/demo.py` ya lo avisa en un comentario
   («el siguiente ensayo arranca contra el proceso viejo sin que se note»). Pasó: se
   midió un run entero contra código viejo y el journal salía con el flujo de antes.
2. **Dos directores a la vez.** Los dos mandan `/tp` al mismo jugador y se pelean por
   la cámara; el segundo no da ningún error.
3. **La URL de ngrok desincronizada** de la que tienen puesta los workflows de
   HappyRobot. Las llamadas salen igual, pero los webhooks del tool no vuelven: las
   unidades se quedan retenidas hasta agotar el plazo y parece un fallo del core.
4. **Nadie conectado a Minecraft.** El `/tp` a un jugador ausente no falla, solo no
   hace nada, y `sim.director` ni lo mira. La cámara se queda muda y no se sabe.
5. **`make world` antes de que Paper conteste por RCON.**

Lo que NO hace, a propósito: pulsar las teclas de `sim.camera` (necesita una terminal
de verdad, ver su guardia de `isatty`) ni entrar al servidor por ti. Eso te lo dice al
final, que es lo único honesto que puede hacer con ello.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from contracts.settings import settings

RAIZ = Path(__file__).resolve().parent.parent
LOGS = RAIZ / "runs" / ".levanta"
NGROK_API = "http://127.0.0.1:4040/api/tunnels"
PUERTO_GATEWAY = 8000
PUERTO_DASH = 5173

CLAVES = (
    "HAPPYROBOT_API_KEY",
    "HAPPYROBOT_HOOK_EVACUATION",
    "WEBHOOK_SHARED_TOKEN",
    "JUDGE_PHONE",
    "NEIGHBOR_PHONE",
    "FIRE_CREW_PHONE",
    "AMBULANCE_PHONE",
)
"""Lo mínimo para un run con las cuatro llamadas salientes. Los cuatro teléfonos van
aquí porque cada uno que falte es una llamada que no se hace, y el síntoma es una
unidad retenida sin explicación: `core.loop` no llama a quien no tiene número."""

OPCIONALES = ("HUMALIKE_API_KEY", "TYPESAFE_API_KEY", "TELEGRAM_BOT_TOKEN")
"""Se degradan y se anotan (Jev cae a fenic, Humalike al borrador, Telegram ausente).
No bloquean, pero conviene saber con qué se va al escenario."""

VERDE, ROJO, AMBAR, FIN = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


@dataclass
class Paso:
    nombre: str
    vale: bool
    detalle: str
    critico: bool = True

    def linea(self) -> str:
        if self.vale:
            color, marca = VERDE, "ok"
        elif self.critico:
            color, marca = ROJO, "FALLA"
        else:
            color, marca = AMBAR, "aviso"
        return f"  {color}{marca:>5}{FIN}  {self.nombre:<22} {self.detalle}"


# --- procesos ----------------------------------------------------------------


def _pids(patron: str) -> list[int]:
    """Los pids cuya línea de comando casa, sin contarnos a nosotros."""
    try:
        salida = subprocess.run(
            ["pgrep", "-f", patron], capture_output=True, text=True, check=False
        ).stdout
    except OSError:
        return []
    yo = os.getpid()
    return [int(p) for p in salida.split() if p.isdigit() and int(p) != yo]


def _pids_en_puerto(puerto: int) -> list[int]:
    salida = subprocess.run(
        ["lsof", "-ti", f":{puerto}"], capture_output=True, text=True, check=False
    ).stdout
    return [int(p) for p in salida.split() if p.isdigit()]


def _matar(pids: list[int], señal: int = signal.SIGKILL) -> None:
    for pid in pids:
        with contextlib_suppress():
            os.kill(pid, señal)


class contextlib_suppress:
    """`contextlib.suppress(OSError)` sin el import: un pid que ya no está no es
    un error, es la situación normal cuando se mata un árbol de procesos."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


def limpiar(check: bool, force: bool) -> Paso:
    """Mata los restos de ensayos anteriores. Una demo VIVA no se toca sin `--force`:
    puede ser el pitch de verdad, y no es una decisión de un script."""
    demos = _pids("scripts/demo.py")
    if demos and not force:
        return Paso(
            "procesos",
            False,
            f"hay una demo corriendo (pid {demos[0]}). `--force` la mata, o párala tú",
        )

    zombis: dict[str, list[int]] = {}
    if force and demos:
        zombis["demo.py"] = demos
    directores = _pids("sim.director")
    if len(directores) > (0 if force else 2):
        # Dos pids por director (el `uv run` y el python de dentro): más de uno de
        # verdad y se pelean por la cámara sin decir nada.
        zombis["directores de sobra"] = directores
    gateway = [p for p in _pids_en_puerto(PUERTO_GATEWAY) if p not in _pids("vite")]
    if gateway and not demos:
        # Con una demo viva el 8000 es suyo; sin ella, es un huérfano y es el fallo
        # que hace medir un run contra código viejo.
        zombis["gateway huérfano"] = gateway

    if not zombis:
        return Paso("procesos", True, "sin restos de ensayos anteriores")
    resumen = " · ".join(f"{k} ({len(v)})" for k, v in zombis.items())
    if check:
        return Paso("procesos", False, f"hay que matar: {resumen}")
    for pids in zombis.values():
        _matar(pids)
    time.sleep(2)
    return Paso("procesos", True, f"matados: {resumen}")


# --- comprobaciones ------------------------------------------------------------


def comprobar_env() -> list[Paso]:
    entorno = {}
    fichero = RAIZ / ".env"
    if fichero.exists():
        for linea in fichero.read_text(encoding="utf-8").splitlines():
            if "=" in linea and not linea.lstrip().startswith("#"):
                k, _, v = linea.partition("=")
                entorno[k.strip()] = v.strip()
    faltan = [k for k in CLAVES if not entorno.get(k)]
    flojos = [k for k in OPCIONALES if not entorno.get(k)]
    return [
        Paso(
            ".env",
            not faltan,
            "las siete claves de un run completo"
            if not faltan
            else f"faltan: {', '.join(faltan)}",
        ),
        Paso(
            "servicios opcionales",
            not flojos,
            "todos" if not flojos else f"sin {', '.join(flojos)} (se degrada y se anota)",
            critico=False,
        ),
    ]


_CLIENTE: object | None = None


async def _rcon(cmd: str) -> str | None:
    """Un comando por RCON, o `None` si Paper no contesta.

    Una sola conexión para todo el guion: abrir y cerrar una por comando dejaba
    tasks del worker colgando y la salida se llenaba de «Task was destroyed»."""
    global _CLIENTE
    from sim.rcon import LOW, RconClient

    try:
        if _CLIENTE is None:
            c = RconClient(settings.rcon_host, settings.rcon_port, settings.rcon_password)
            await asyncio.wait_for(c.connect(), timeout=4.0)
            _CLIENTE = c
        return await asyncio.wait_for(_CLIENTE.send(cmd, LOW), timeout=6.0)
    except Exception:  # noqa: BLE001 — sin Paper no hay nada que distinguir
        return None


async def _cerrar_rcon() -> None:
    global _CLIENTE
    if _CLIENTE is not None:
        with contextlib_suppress():
            await _CLIENTE.close()
        _CLIENTE = None


def _cuenta(respuesta: str | None) -> int:
    """Las entidades de un `execute if entity`. «Test failed» son cero, y cero es
    justo el caso que hay que ver: un mundo vacío no da error, simplemente no se ve
    nada en Minecraft y la cámara enseña un valle pelado. Pasó: matar una demo con
    `-9` justo después de su `/kill @e[tag=vela]` deja el mundo borrado, la demo
    siguiente mueve unidades que no existen y el `/tp` no se queja."""
    if not respuesta:
        return 0
    hallado = re.search(r"Count:\s*(\d+)", respuesta)
    return int(hallado.group(1)) if hallado else 0


async def paper(check: bool) -> Paso:
    """Paper vivo y contestando por RCON. Si no está, se levanta y se ESPERA: el
    `make world` de después falla si se lanza contra un puerto que aún no escucha."""
    if await _rcon("list") is not None:
        return Paso("Paper + RCON", True, f"escuchando en {settings.rcon_port}")
    if check:
        return Paso("Paper + RCON", False, "no contesta (con `--check` no se levanta)")

    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / "paper.log"
    print("  … levantando Paper, esto tarda", flush=True)
    with log.open("w") as fh:
        subprocess.Popen(  # noqa: ASYNC220 — guion secuencial, no un servidor
            [str(RAIZ / "infra" / "server" / "start.sh")],
            stdout=fh,
            stderr=subprocess.STDOUT,
            cwd=RAIZ,
            start_new_session=True,
        )
    for _ in range(60):
        await asyncio.sleep(2)
        if await _rcon("list") is not None:
            return Paso(
                "Paper + RCON", True, f"levantado · log en {log.relative_to(RAIZ)}"
            )
    return Paso(
        "Paper + RCON", False, f"no arrancó en 2 min · mira {log.relative_to(RAIZ)}"
    )


async def mundo(escenario: str, check: bool) -> Paso:
    """La geografía por RCON. Es idempotente (`/kill @e[tag=vela]` y otra vez), así
    que se regenera siempre: cuesta segundos y quita el run anterior de en medio."""
    if check:
        n = _cuenta(await _rcon("execute if entity @e[tag=vela]"))
        return Paso(
            "mundo",
            n > 0,
            f"{n} entidades `vela` (no se regenera)"
            if n
            else "VACÍO · nada visible en Minecraft · `make world`",
        )
    hecho = subprocess.run(  # noqa: ASYNC221 — worldgen tarda segundos y se espera
        [
            sys.executable,
            "-m",
            "sim.worldgen",
            "--scenario",
            f"scenarios/{escenario}.yaml",
        ],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    if hecho.returncode != 0:
        return Paso("mundo", False, (hecho.stderr or hecho.stdout).strip()[-120:])
    n = _cuenta(await _rcon("execute if entity @e[tag=vela]"))
    return Paso("mundo", n > 0, f"regenerado · {n} entidades `vela`")


async def tunel() -> Paso:
    """ngrok vivo, y la MISMA url que tienen puesta los workflows de HappyRobot.

    Si no coinciden, las llamadas salen y los webhooks del tool no vuelven: el tool
    `reportar_situacion` nunca llega, la unidad se queda retenida hasta el plazo y el
    síntoma apunta al core, que no tiene la culpa."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            datos = (await c.get(NGROK_API)).json()
        vivas = {t["public_url"] for t in datos.get("tunnels", []) if t.get("public_url")}
    except (httpx.HTTPError, ValueError, KeyError):
        return Paso("túnel", False, "ngrok no responde en :4040 · `ngrok http 8000`")
    if not vivas:
        return Paso("túnel", False, "ngrok vivo pero sin túneles")
    aqui = max(vivas)

    # `scripts/` no es un paquete: se añade al path para reutilizar retunnel en vez
    # de duplicar aquí lo que sabe de la API de HappyRobot.
    sys.path.insert(0, str(RAIZ / "scripts"))
    from retunnel import ENTORNOS, _hr, variables, workflows

    try:
        registradas: set[str] = set()
        async with _hr() as c:
            for w in await workflows(c):
                for v in await variables(c, w["id"]):
                    if v["key"] == "VELA_URL":
                        registradas |= {
                            (v.get(f"value_{e}") or "").rstrip("/") for e in ENTORNOS
                        }
    except Exception as exc:  # noqa: BLE001
        return Paso(
            "túnel", False, f"no se pudo leer HappyRobot: {exc!r}"[:120], critico=False
        )

    registradas.discard("")
    if registradas == {aqui.rstrip("/")}:
        return Paso("túnel", True, f"{aqui} · y es la que tiene HappyRobot")
    return Paso(
        "túnel",
        False,
        f"ngrok dice {aqui} y HappyRobot {sorted(registradas) or '(nada)'} · "
        f"arréglalo: uv run python scripts/retunnel.py --apply {aqui}",
    )


def dashboard(check: bool) -> Paso:
    if _pids_en_puerto(PUERTO_DASH):
        return Paso("dashboard", True, f"http://localhost:{PUERTO_DASH}")
    if check:
        return Paso("dashboard", False, f"nada en el {PUERTO_DASH}")
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / "dashboard.log"
    with log.open("w") as fh:
        subprocess.Popen(
            ["pnpm", "dev"],
            cwd=RAIZ / "apps" / "dashboard",
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    for _ in range(20):
        time.sleep(1)
        if _pids_en_puerto(PUERTO_DASH):
            return Paso("dashboard", True, f"levantado · http://localhost:{PUERTO_DASH}")
    return Paso(
        "dashboard", False, f"no arrancó · mira {log.relative_to(RAIZ)}", critico=False
    )


async def jugador() -> Paso:
    """Quién está dentro de Minecraft. No es crítico para que el run corra, pero sin
    jugador la cámara no existe: el `/tp` a alguien ausente no falla, solo no hace
    nada, y `sim.director` ni lo comprueba."""
    respuesta = await _rcon("list")
    if respuesta is None:
        return Paso("jugador", False, "sin RCON no se puede saber", critico=False)
    texto = respuesta.strip()
    nombres = texto.rsplit(":", 1)[-1].strip() if ":" in texto else ""
    if not nombres:
        return Paso(
            "jugador",
            False,
            "NADIE conectado · entra a localhost:25565 o no habrá cámara",
            critico=False,
        )
    return Paso("jugador", True, nombres)


# --- lanzar --------------------------------------------------------------------


def lanzar_demo(escenario: str, mock: bool) -> subprocess.Popen:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / "demo.log"
    orden = [sys.executable, "scripts/demo.py", "--scenario", escenario]
    if mock:
        orden.append("--mock-calls")
    print(f"\n  lanzando: {' '.join(orden[1:])} · log en {log.relative_to(RAIZ)}")
    with log.open("w") as fh:
        return subprocess.Popen(orden, cwd=RAIZ, stdout=fh, stderr=subprocess.STDOUT)


def lanzar_director(player: str, escenario: str) -> None:
    """El director se engancha al journal MÁS RECIENTE, y lo resuelve una sola vez al
    arrancar. Por eso va después de la demo y con un respiro: si se lanza antes, coge
    el run anterior, hace `seek` al final y no ve una línea en toda la demo."""
    runs = sorted((RAIZ / "runs").glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not runs:
        print("  ! sin journal todavía: arranca el director a mano cuando lo haya")
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / "director.log"
    with log.open("w") as fh:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "sim.director",
                "--player",
                player,
                "--scenario",
                f"scenarios/{escenario}.yaml",
                "--journal",
                str(runs[-1]),
            ],
            cwd=RAIZ,
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    print(
        f"  director sobre {player} · journal {runs[-1].name} · log {log.relative_to(RAIZ)}"
    )


# --- el guion ------------------------------------------------------------------


async def levantar(args: argparse.Namespace) -> int:
    print(f"\nvela · {'comprobando' if args.check else 'levantando'} para la demo\n")
    pasos = [limpiar(args.check, args.force)]
    if not pasos[0].vale and not args.check:
        print(pasos[0].linea())
        return 1

    pasos += comprobar_env()
    pasos.append(await paper(args.check))
    if pasos[-1].vale:
        pasos.append(await mundo(args.scenario, args.check))
    pasos.append(await tunel())
    pasos.append(dashboard(args.check))
    pasos.append(await jugador())

    print()
    for p in pasos:
        print(p.linea())

    await _cerrar_rcon()
    rotos = [p for p in pasos if not p.vale and p.critico]
    if rotos:
        print(f"\n{ROJO}{len(rotos)} cosa(s) por arreglar antes de la demo.{FIN}")
        return 1

    if args.demo or args.mock:
        proceso = lanzar_demo(args.scenario, mock=args.mock)
        if args.player:
            await asyncio.sleep(8)  # que la demo cree su journal antes de engancharse
            lanzar_director(args.player, args.scenario)
        print("\n  Ctrl-C aquí corta la demo.\n")
        proceso.wait()
        return proceso.returncode

    print(f"\n{VERDE}Todo listo.{FIN} Te falta, y esto no lo puede hacer un script:\n")
    print(
        "  1. entrar a Minecraft en localhost:25565 (cualquier nombre: online-mode=false)"
    )
    print("  2. la cámara, en TU terminal (necesita tty): make cam PLAYER=<usuario>")
    print("     teclas 1-6 · 5 = parque (camiones retenidos) · 6 = hospital")
    print("     o, si prefieres que conduzca sola: make director PLAYER=<usuario>")
    print("     las dos a la vez se pisan; la pareja que convive es `cam` + `narra`")
    print("  3. los cuatro teléfonos a mano, con el guion de use_cases.md delante")
    print("  4. y entonces: make demo     (o repite esto con --demo)")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Levanta la demo de vela · comprobación previa"
    )
    ap.add_argument("--scenario", default="wildfire_ridge")
    ap.add_argument("--check", action="store_true", help="solo diagnostica, no toca nada")
    ap.add_argument("--force", action="store_true", help="mata también una demo viva")
    ap.add_argument("--player", help="usuario de Minecraft: engancha el director al run")
    ap.add_argument("--demo", action="store_true", help="lanza la demo · LLAMADAS REALES")
    ap.add_argument(
        "--mock", action="store_true", help="lanza la demo en seco (--mock-calls)"
    )
    args = ap.parse_args()
    try:
        raise SystemExit(asyncio.run(levantar(args)))
    except KeyboardInterrupt:
        print("\ncortado")


if __name__ == "__main__":
    main()
