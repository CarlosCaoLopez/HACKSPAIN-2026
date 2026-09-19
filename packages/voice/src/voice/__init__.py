"""`voice` · P3 · telefonía. HappyRobot ejecuta en las dos direcciones; Humalike
modula cómo habla el agente (`voice.humanlike`).

P3 nunca toca el estado: solo emite `world.fact.asserted`. La traducción de
`CallFacts` a `Fact` es suya, con el mapa de claves que le da P1
(`contracts.factkeys`).
"""

from __future__ import annotations

import asyncio
import logging
import os

from contracts.calls import CallFacts, CallRequest, Fact
from contracts.events import EventType
from contracts.factkeys import road_cause_key, road_cut_key, validate_fact_key
from contracts.settings import settings
from voice import pois
from voice.extract_schema import build_extract_model, to_call_facts
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
        de forma bloqueante. El disparo es de Carlos (`voice.happyrobot.trigger`);
        aquí se publica `call.started` con el `call_id` que devuelve."""
        from contracts.bus import current_run_id, make_event, publish
        from voice import happyrobot

        run_id = current_run_id()
        call_id = await happyrobot.trigger(req, run_id)
        await publish(
            make_event(
                EventType.CALL_STARTED,
                {"call_id": call_id, "task_id": req.task_id, "to": req.to, "direction": "outbound"},
                source="voice",
            )
        )
        return call_id

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
        """Plan B (`--no-jev`): `fenic.semantic.extract` con `Literal` sobre los ids del
        escenario. None si falla o si tarda más de 4 s: la transcripción cruda va al
        dashboard sin extraer y la demo continúa."""
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
        `contracts.factkeys`, `source="call:<call_id>"`. Camino sin Jev: salen como
        `inferred` (sin confianza calibrada), y solo la dirección segura (cortar) puede
        cambiar rutas.

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
                    kind="inferred",
                    call_id=call_id,
                )
            )

        if cf.road_blocked:
            edge = pois.resolve_edge_local(cf.road_blocked)
            if edge:
                add(road_cut_key(edge), True)
                add(road_cause_key(edge), cf.road_blocked)
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


FENIC_OPENAI_MODEL = "gpt-5.6-luna"
FENIC_ANTHROPIC_MODEL = "claude-haiku-4-5"
"""Camino frío (fin de llamada, sintéticas). OpenAI con gpt-5.6-luna si hay
OPENAI_API_KEY; si no, Anthropic. fenic lee la key del entorno; `settings` la carga
del .env."""


_fenic_failed: str | None = None
"""Si la sesión de fenic falló una vez (sin key, key inválida), no se reintenta en
cada llamada: se recuerda el motivo y se cae a las heurísticas en silencio."""


def fenic_session():
    """Sesión de fenic con Anthropic como modelo por defecto, o (None, None) si no
    hay `fenic` o no hay API key. Nunca lanza."""
    os.environ.setdefault(
        "TQDM_DISABLE", "1"
    )  # antes de importar: fenic pinta barras en el log
    try:
        import fenic as fc
    except ImportError:
        return None, None
    global _fenic_failed
    if _fenic_failed is not None:
        return None, None
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
        model = fc.OpenAILanguageModel(
            model_name=FENIC_OPENAI_MODEL, rpm=100, tpm=100_000
        )
    elif settings.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
        model = fc.AnthropicLanguageModel(
            model_name=FENIC_ANTHROPIC_MODEL,
            rpm=100,
            input_tpm=100_000,
            output_tpm=20_000,
        )
    else:
        return None, None
    try:
        config = fc.SessionConfig(
            app_name="vela",
            semantic=fc.SemanticConfig(
                language_models={"llm": model}, default_language_model="llm"
            ),
        )
        return fc, fc.Session.get_or_create(config)
    except Exception as exc:  # noqa: BLE001
        _fenic_failed = str(exc)[:200]
        log.warning("fenic sin sesión (no se reintenta): %s", _fenic_failed)
        return None, None


def warmup() -> None:
    """Crea la sesión de fenic en un hilo al arrancar, para que el primer extract
    real no pague los ~2 s de arranque del cliente."""
    import threading

    threading.Thread(target=fenic_session, daemon=True).start()


def _fenic_extract(transcript: str) -> CallFacts | None:
    """El camino frío. Si `fenic` no está o cambia su API, None y se sigue."""
    fc, session = fenic_session()
    if fc is None:
        return None
    df = session.create_dataframe([{"transcript": transcript}])
    model = build_extract_model(
        list(pois.pois()),
        list(pois.roads()),
    )
    rows = df.select(
        fc.semantic.extract(
            fc.col("transcript"), model, request_timeout=EXTRACT_TIMEOUT_S
        ).alias("f")
    ).to_pylist()
    if not rows:
        return None
    raw = rows[0].get("f")
    if raw is None:
        return None
    return to_call_facts(raw, {i: p.name for i, p in pois.pois().items()})
