"""`voice` · P3 · telefonía. HappyRobot ejecuta en las dos direcciones; Humalike
modula cómo habla el agente (`voice.humanlike`).

P3 nunca toca el estado: solo emite `world.fact.asserted`. La traducción de
`CallFacts` a `Fact` es suya, con el mapa de claves que le da P1
(`contracts.factkeys`).
"""

from __future__ import annotations

import asyncio
import logging

from contracts.calls import CallFacts, CallRequest, Fact
from contracts.factkeys import road_cut_key, validate_fact_key
from voice import pois
from voice.webhooks import router

__all__ = ["VoiceGateway", "router"]

log = logging.getLogger("voice")

EXTRACT_TIMEOUT_S = 4.0


class VoiceGateway:
    """La superficie pública del paquete. El core no sabe que existe HappyRobot y
    no debe saberlo."""

    async def place_call(self, req: CallRequest) -> str:
        """Devuelve el `call_id` en cuanto la plataforma acepta, no cuando la
        llamada termina. El resultado llega por evento: nadie espera a una llamada
        de forma bloqueante. El disparo es de Carlos (`voice.happyrobot.trigger`)."""
        from contracts.bus import current_run_id
        from voice import happyrobot

        return await happyrobot.trigger(req, current_run_id())

    async def signal(self, call_id: str, key: str, payload: dict) -> str | None:
        """Algo que el agente tiene que decir en vivo. Lo redacta y refina
        `voice.humanlike`; aquí solo se enruta al monitor de esa llamada."""
        from voice import humanlike

        mon = humanlike.MONITORS.get(call_id)
        if mon is None:
            log.warning("signal para llamada desconocida %s", call_id)
            return None
        await mon.on_signal_requested(key, payload, [])
        return call_id

    async def extract(self, transcript: str) -> CallFacts | None:
        """`fenic.semantic.extract` sobre un DataFrame de una fila. None si falla o
        si tarda más de 4 s: la transcripción cruda va al dashboard sin extraer y
        la demo continúa."""
        if not transcript.strip():
            return None
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_fenic_extract, transcript), EXTRACT_TIMEOUT_S
            )
        except TimeoutError:
            log.warning("extract: más de %.0f s, sin extraer", EXTRACT_TIMEOUT_S)
            return None
        except Exception as exc:  # noqa: BLE001 - la demo continúa
            log.warning("extract: %s", exc)
            return None

    def to_facts(self, cf: CallFacts, t_sim: float, call_id: str) -> list[Fact]:
        """Un `CallFacts` produce de 0 a N `Fact`. Claves de
        `contracts.factkeys`, `source="call:<call_id>"`.

        Ningún hecho aparece en pantalla sin decir de qué llamada viene."""
        source = f"call:{call_id}"
        sev = cf.urgency
        conf = cf.confidence
        out: list[Fact] = []

        def add(key: str, value: str | float | bool) -> None:
            if validate_fact_key(key) is None:
                log.warning("clave fuera del mapa, no se emite: %s", key)
                return
            out.append(
                Fact(
                    key=key,
                    value=value,
                    confidence=conf,
                    source=source,
                    severity=sev,
                    t_sim=t_sim,
                )
            )

        if cf.road_blocked:
            edge = pois.resolve_edge_local(cf.road_blocked)
            if edge:
                add(road_cut_key(edge), True)
                add(f"road:{edge}:cause", cf.road_blocked)
            else:
                log.warning("carretera sin resolver: %r", cf.road_blocked)
        poi = cf.resolved_poi_id or pois.resolve_poi_local(cf.location_hint)
        if poi:
            if cf.people_immobile is not None:
                add(f"poi:{poi}:immobile", int(cf.people_immobile))
            if cf.injuries is not None:
                add(f"poi:{poi}:injuries", int(cf.injuries))
            if cf.confirmed_order is not None:
                add(f"poi:{poi}:confirmed", bool(cf.confirmed_order))
        elif cf.people_immobile or cf.injuries or cf.confirmed_order is not None:
            log.warning("hecho sin ubicar, no entra al estado: %r", cf.location_hint)
        return out


def _fenic_extract(transcript: str) -> CallFacts | None:
    """El camino frío. Si `fenic` no está o cambia su API, None y se sigue."""
    try:
        import fenic as fc  # type: ignore[import-not-found]
    except ImportError:
        return None
    session = fc.Session.get_or_create(fc.SessionConfig(app_name="vela"))
    df = session.create_dataframe([{"transcript": transcript}])
    rows = df.select(
        fc.semantic.extract(fc.col("transcript"), CallFacts).alias("f")
    ).to_pylist()
    if not rows:
        return None
    raw = rows[0].get("f")
    if raw is None:
        return None
    return CallFacts.model_validate(raw if isinstance(raw, dict) else raw.__dict__)
