"""Servidor de desarrollo de `voice`, para trabajar sin el gateway de Nacho.

Monta solo lo que necesita P3: el bus con journal, el router de webhooks, la
tabla de POIs y carreteras, y el despachador de señales. Dos rutas de ayuda bajo
`/dev` para probar a mano lo que en la demo hará el core:

    uv run python -m voice.dev                      # servidor en :8000
    uv run python -m voice.dev --mock-calls         # + una llamada de guion (FakeLive)
    ngrok http 8000                                 # el túnel para el tool de HappyRobot

    curl -X POST localhost:8000/dev/signal -H 'content-type: application/json' \\
      -d '{"call_id": "<session_id>", "key": "unit_dispatched",
           "payload": {"unit": "camión 2", "route": "pista norte", "eta_s": 40}}'
    curl localhost:8000/dev/events?n=20

Cuando el gateway exista, esto sobra; hasta entonces es el `make dev-voice` real.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from collections import deque
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from contracts import bus
from contracts.events import Event, EventType
from contracts.world import POI, RoadEdge
from voice import humanlike, pois
from voice.webhooks import router

log = logging.getLogger("voice.dev")

DEV_POIS = [
    POI(
        id="poi_pueblo_a",
        name="Pueblo A",
        kind="village",
        x=-120,
        z=40,
        waypoint_id="wp_a",
    ),
    POI(
        id="poi_pueblo_b",
        name="Pueblo B",
        kind="village",
        x=140,
        z=60,
        waypoint_id="wp_b",
    ),
    POI(
        id="poi_molino",
        name="Molino viejo",
        kind="landmark",
        x=90,
        z=110,
        waypoint_id="wp_sur_03",
    ),
    POI(
        id="poi_hospital",
        name="Hospital",
        kind="hospital",
        x=0,
        z=-80,
        waypoint_id="wp_h",
        min_coverage=1,
    ),
    POI(
        id="poi_refugio",
        name="Refugio",
        kind="shelter",
        x=-40,
        z=-140,
        waypoint_id="wp_r",
    ),
]
DEV_ROADS = [
    RoadEdge(id="wp_sur_03-wp_sur_04", a="wp_sur_03", b="wp_sur_04", length_m=180),
    RoadEdge(
        id="wp_norte_02-wp_norte_03", a="wp_norte_02", b="wp_norte_03", length_m=220
    ),
    RoadEdge(id="wp_a-wp_h", a="wp_a", b="wp_h", length_m=150),
    RoadEdge(id="wp_b-wp_r", a="wp_b", b="wp_r", length_m=260),
]
DEV_POI_ALIASES = {
    "el molino": "poi_molino",
    "molino viejo": "poi_molino",
    "el pueblo de arriba": "poi_pueblo_a",
}
DEV_ROAD_ALIASES = {
    "pista del sur": "wp_sur_03-wp_sur_04",
    "pista sur": "wp_sur_03-wp_sur_04",
    "carretera del sur": "wp_sur_03-wp_sur_04",
    "pista del norte": "wp_norte_02-wp_norte_03",
    "carretera del norte": "wp_norte_02-wp_norte_03",
}

_recent: deque[Event] = deque(maxlen=500)


def load_pois(scenario: Path | None) -> str:
    """El YAML de Luis si tiene POIs; si no, la tabla de desarrollo de arriba."""
    if (
        scenario
        and scenario.exists()
        and pois.load_scenario_yaml(scenario)
        and pois.pois()
    ):
        return f"escenario {scenario}"
    pois.set_scenario(DEV_POIS, DEV_ROADS, DEV_POI_ALIASES, DEV_ROAD_ALIASES)
    return "POIs de desarrollo (el YAML no tiene pois)"


def build_app(
    scenario: Path | None, mock_calls: bool, speed: float, dummy_core: bool = False
) -> FastAPI:
    app = FastAPI(title="vela · voice dev")
    app.include_router(router)

    @app.on_event("startup")
    async def _startup() -> None:
        run_id = bus.configure(run_id=f"dev_{uuid.uuid4().hex[:6]}")
        log.info("run %s · journal en runs/%s.jsonl", run_id, run_id)
        log.info("POIs: %s", load_pois(scenario))
        if mock_calls:
            from voice.fake import install_fakes

            install_fakes(speed=speed)
            log.info(
                "--mock-calls: FakeHumalike + FakeLive; el guion hará el POST real al tool"
            )
        asyncio.create_task(humanlike.signal_dispatcher())
        asyncio.create_task(_tail())
        if dummy_core:
            asyncio.create_task(_dummy_core())
            log.info(
                "--dummy-core: carretera cortada por llamada → unit_dispatched en 300 ms"
            )
        if mock_calls:
            asyncio.create_task(_mock_session(run_id))

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        bus.close()

    @app.get("/dev/events")
    async def dev_events(n: int = 30) -> list[dict]:
        return [e.model_dump(mode="json") for e in list(_recent)[-n:]]

    @app.post("/dev/signal")
    async def dev_signal(body: dict) -> dict:
        """Lo que hará el core tras un replan: `call.signal.requested`."""
        ev = bus.make_event(
            EventType.CALL_SIGNAL_REQUESTED,
            {
                "call_id": body["call_id"],
                "key": body.get("key", "unit_dispatched"),
                "payload": body.get("payload") or {},
            },
            source="core",
        )
        await bus.publish(ev)
        return {"seq": ev.seq, "monitors": list(humanlike.MONITORS)}

    @app.get("/dev/latency")
    async def dev_latency(call_id: str | None = None) -> dict:
        """Cronómetro de la última llamada (o de `call_id`): desde el primer hecho del
        tool hasta que HappyRobot aceptó la señal, con los tramos intermedios."""
        return latency_report(list(_recent), call_id)

    @app.get("/dev/calls")
    async def dev_calls() -> dict:
        return {
            sid: {
                "turns": len(m.state.transcript),
                "thread_id": m.state.thread_id,
                "last_emotions": m.state.last_emotions,
                "facts": sorted(m.state.tool_facts_keys),
            }
            for sid, m in humanlike.MONITORS.items()
        }

    return app


async def _tail() -> None:
    async for ev in bus.subscribe():
        _recent.append(ev)
        if ev.type in (
            EventType.WORLD_FACT_ASSERTED,
            EventType.CALL_AFFECT,
            EventType.CALL_SIGNAL_SENT,
            EventType.CALL_ENDED,
        ):
            log.info("%s %s", ev.type, _short(ev))


def _short(ev: Event) -> str:
    p = ev.payload
    match ev.type:
        case EventType.WORLD_FACT_ASSERTED:
            return f"{p['key']}={p['value']} ({p['source']})"
        case EventType.CALL_AFFECT:
            return ", ".join(
                f"{e['type']}:{e['intensity']:.1f}" for e in p.get("emotions", [])
            )
        case EventType.CALL_SIGNAL_SENT:
            return f"{p['key']} → {p['signal_id']}"
        case EventType.CALL_ENDED:
            return f"health_score={p.get('health_score')}"
    return ""


def latency_report(events: list[Event], call_id: str | None = None) -> dict:
    def cid(e: Event) -> str | None:
        if e.type == EventType.WORLD_FACT_ASSERTED:
            return (
                e.source.removeprefix("call:") if e.source.startswith("call:") else None
            )
        return e.payload.get("call_id")

    def ms(a: Event | None, b: Event | None) -> int | None:
        if a is None or b is None:
            return None
        return round((b.t_wall - a.t_wall).total_seconds() * 1000)

    if call_id is None:
        for e in reversed(events):
            if (
                e.type == EventType.CALL_SIGNAL_SENT
                and e.payload.get("key") == "unit_dispatched"
            ):
                call_id = e.payload["call_id"]
                break
    if call_id is None:
        return {"error": "sin unit_dispatched todavía"}
    mine = [e for e in events if cid(e) == call_id]
    first_fact = next((e for e in mine if e.type == EventType.WORLD_FACT_ASSERTED), None)
    requested = next((e for e in mine if e.type == EventType.CALL_SIGNAL_REQUESTED), None)
    sent = next(
        (
            e
            for e in mine
            if e.type == EventType.CALL_SIGNAL_SENT
            and e.payload.get("key") == "unit_dispatched"
        ),
        None,
    )
    return {
        "call_id": call_id,
        "fact_to_requested_ms": ms(first_fact, requested),
        "requested_to_sent_ms": ms(requested, sent),
        "fact_to_sent_ms": ms(first_fact, sent),
        "voice_latency_ms": sent.payload.get("latency_ms") if sent else None,
        "refined": sent.payload.get("refined") if sent else None,
        "message": sent.payload.get("message") if sent else None,
        "objetivo": "fact_to_sent_ms <= 2000",
    }


async def _dummy_core() -> None:
    """Lo que hará el core de Carlos tras el replan, en tonto: una carretera cortada
    por llamada → 300 ms de "planner" → `call.signal.requested` con la desviación."""
    async for ev in bus.subscribe(EventType.WORLD_FACT_ASSERTED):
        p = ev.payload
        if not (
            p["key"].startswith("road:") and p["key"].endswith(":cut") and p["value"]
        ):
            continue
        if not ev.source.startswith("call:"):
            continue
        edge = p["key"].split(":")[1]
        route = "pista norte" if "sur" in edge else "pista sur"
        await asyncio.sleep(0.3)
        await bus.publish(
            bus.make_event(
                EventType.CALL_SIGNAL_REQUESTED,
                {
                    "call_id": ev.source.removeprefix("call:"),
                    "key": "unit_dispatched",
                    "payload": {"unit": "camión 2", "route": route, "eta_s": 40},
                },
                source="core",
                causes=[ev.seq],
            )
        )


async def _mock_session(run_id: str) -> None:
    """Una llamada de guion: arranca el monitor (FakeLive reproduce el jsonl y
    hace el POST real al tool) y, tras el hecho, manda la señal como haría el core."""
    await asyncio.sleep(1.0)
    session_id = f"mock_{uuid.uuid4().hex[:6]}"
    log.info("llamada de guion: session %s", session_id)
    mon = humanlike.get_or_start(session_id, run_id)
    while not mon.state.tool_facts_keys and not mon.state.ended:
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.5)
    if not any(
        e.type == EventType.CALL_SIGNAL_REQUESTED
        and e.payload.get("call_id") == session_id
        for e in _recent
    ):
        await bus.publish(
            bus.make_event(
                EventType.CALL_SIGNAL_REQUESTED,
                {
                    "call_id": session_id,
                    "key": "unit_dispatched",
                    "payload": {"unit": "camión 2", "route": "pista norte", "eta_s": 40},
                },
                source="core",
            )
        )
    if mon._task:
        await mon._task
    from voice.webhooks import _on_end

    await _on_end(
        {
            "type": "end",
            "session_id": session_id,
            "status": "completed",
            "direction": "inbound",
        }
    )
    log.info("llamada de guion terminada; GET /dev/events para verla")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--scenario", type=Path, default=Path("scenarios/wildfire_ridge.yaml"))
    p.add_argument("--mock-calls", action="store_true")
    p.add_argument(
        "--dummy-core",
        action="store_true",
        help="un core tonto: cada carretera cortada por llamada dispara unit_dispatched",
    )
    p.add_argument(
        "--speed", type=float, default=1.0, help="velocidad del guion en --mock-calls"
    )
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    uvicorn.run(
        build_app(args.scenario, args.mock_calls, args.speed, args.dummy_core),
        host="0.0.0.0",
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
