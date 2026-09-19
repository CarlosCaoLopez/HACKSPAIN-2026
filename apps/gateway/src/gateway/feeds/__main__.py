"""`python -m gateway.feeds --probe <escenario>`: probar las fuentes sin publicar. SPEC-007 · REQ-268/269.

Es lo que se ejecuta al llegar a la sala, con la wifi de la sala: una pasada por cada fuente
activa que **imprime los hechos que publicaría** y sale con 0 si todas respondieron, con 1 si
alguna falló. Si va a fallar, mejor que falle aquí y no en el minuto cuatro del pitch.

- `--probe ESCENARIO`: no guarda ni publica nada.
- `--capture ESCENARIO`: lo mismo, pero además guarda las respuestas crudas en
  `VELA_FEEDS_DIR/<ancla>/<fuente>/` (materia prima de `recorded` y auditoría de un `source`).
- `--anchor-override LAT,LON[,AAAA-MM-DD]`: probar un candidato sin editar el YAML. Es como se
  elige el ancla: se prueba un incendio real y se mira el resumen del final.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx

from contracts.settings import settings
from gateway.feeds import Observation
from gateway.feeds.anchor import GeoAnchor, load_anchor
from gateway.feeds.poller import HTTP_TIMEOUT_S, NAMES, USER_AGENT, Feeds
from gateway.scenarios import load_scenario, scenario_path


def parse_override(text: str) -> tuple[float, float, datetime | None]:
    """`40.5,-4.2` o `40.5,-4.2,2025-08-14`. Sin fecha es en vivo; con ella, ese día en UTC."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) not in (2, 3):
        raise ValueError(f"--anchor-override espera LAT,LON[,AAAA-MM-DD]: {text!r}")
    lat, lon = float(parts[0]), float(parts[1])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"coordenadas fuera de rango: {lat}, {lon}")
    day = datetime.strptime(parts[2], "%Y-%m-%d").replace(tzinfo=UTC) if len(parts) == 3 else None
    return lat, lon, day


class _ProbeRt:
    """Lo que `Feeds` pide de un `Runtime`, sin serlo: la sonda no tiene run, ni core, ni bus."""

    hub = SimpleNamespace(last_t_sim=0.0)

    def world_state(self) -> None:
        return None

    def current_plan(self) -> None:
        return None

    async def publish(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("la sonda no publica: eso es lo que la distingue de un run")


def _line(obs: Observation) -> str:
    f = obs.fact
    when = obs.t_real.strftime("%Y-%m-%dT%H:%MZ") if obs.t_real else "ahora"
    return (
        f"  {f.source.split(':')[1]:<11} {f.key} = {f.value}  ·  {f.kind} {f.confidence:.2f}"
        f"  ·  {f.severity}  ·  {when}  ·  {f.source}"
    )


async def probe(
    anchor: GeoAnchor,
    scenario_id: str,
    *,
    capture: bool = False,
    only: frozenset[str] = frozenset(),
    client: httpx.AsyncClient | None = None,
    feeds_dir: Path | None = None,
    firms_key: str | None = None,
    out: Callable[[str], object] = print,
) -> int:
    """Una pasada por cada fuente activa. `0` si todas respondieron; `1` si alguna falló o si
    no había ninguna que probar (una sonda que dice «bien» sin haber probado nada miente)."""
    feeds = Feeds(
        rt=_ProbeRt(),  # type: ignore[arg-type]  # el doble solo tiene lo que `Feeds` usa
        anchor=anchor,
        scenario=load_scenario(scenario_id),
        mode="live",
        only=only,
        feeds_dir=feeds_dir or Path(settings.vela_feeds_dir),
        firms_key=settings.firms_map_key if firms_key is None else firms_key,
        save_captures=capture,
    )
    day = anchor.reference_start.date().isoformat() if anchor.reference_start else "en vivo"
    out(
        f"sonda · {scenario_id} · ancla: {anchor.place}"
        f" · {anchor.meters_per_block:g} m/bloque · {day} · capturas: {'sí' if capture else 'no'}"
    )

    async with _client(client) as http:
        feeds.client = http
        specs = feeds.plan_sources()
        for spec in specs:
            await feeds.cycle(spec)

    for name in NAMES:
        st = feeds.states[name]
        detail = st.last_error or st.note or f"{st.published} publicados" if st.status != "ok" else "ok"
        out(f"  {name:<11} {st.status:<9} {detail}")

    observations = sorted(
        feeds.schedule.pop_due(math.inf),
        key=lambda o: (o.t_real or datetime.min.replace(tzinfo=UTC), o.fact.key),
    )
    out(f"hechos que se publicarían ({len(observations)}):")
    for obs in observations:
        out(_line(obs))

    detections = feeds.status()["detections"]
    firms_in_grid = sum(1 for o in observations if o.fact.source.startswith("api:firms:"))
    turns = sum(
        1 for o in observations if o.fact.key == "wind:bearing_deg" and o.fact.severity == "critical"
    )
    out("resumen para elegir el ancla:")
    out(
        f"  focos FIRMS en el cuadro: {len(detections)}"
        f" (n/h: {sum(1 for d in detections if d['confidence'] in ('n', 'h'))})"
        f" · en la rejilla del valle: {firms_in_grid}"
    )
    out(f"  giros de viento >= 60 grados: {turns}")

    if not specs:
        out("ninguna fuente activa: no hay nada que probar (mira las notas de arriba)")
        return 1
    return 0 if all(feeds.states[s.name].status == "ok" for s in specs) else 1


def _client(client: httpx.AsyncClient | None) -> httpx.AsyncClient:
    if client is not None:
        return client
    return httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT_S)


def main(argv: list[str] | None = None) -> int:
    # La consola de Windows puede no ser UTF-8; un carácter raro no debe matar la sonda.
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]  # es un TextIOWrapper

    parser = argparse.ArgumentParser(prog="python -m gateway.feeds", description=__doc__.split("\n\n")[0])
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--probe", metavar="ESCENARIO", help="probar sin publicar ni guardar")
    what.add_argument("--capture", metavar="ESCENARIO", help="probar y guardar las respuestas crudas")
    parser.add_argument("--only", default="", help="open_meteo,dgt,firms (vacío = todas)")
    parser.add_argument("--anchor-override", metavar="LAT,LON[,AAAA-MM-DD]")
    args = parser.parse_args(argv)

    scenario_id = args.probe or args.capture
    if not scenario_path(scenario_id).exists():
        parser.error(f"escenario desconocido: {scenario_id}")
    anchor = load_anchor(scenario_id)
    if anchor is None:
        parser.error(f"{scenario_id} no tiene ancla (feeds/anchors/{scenario_id}.yaml)")
    if args.anchor_override:
        try:
            lat, lon, day = parse_override(args.anchor_override)
        except ValueError as exc:
            parser.error(str(exc))
        anchor = anchor.model_copy(
            update={"lat0": lat, "lon0": lon, "fixed": True, "reference_start": day or anchor.reference_start}
        )

    only = frozenset(n.strip() for n in args.only.split(",") if n.strip())
    return asyncio.run(probe(anchor, scenario_id, capture=bool(args.capture), only=only))


if __name__ == "__main__":
    sys.exit(main())
