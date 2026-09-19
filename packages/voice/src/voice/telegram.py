"""Telegram: el «dónde» exacto del reporte ciudadano. P3.

La llamada (HappyRobot) da el *qué*; el vecino que no sabe ubicarse («estoy al final
de la pista, junto a unas casas») no puede dar el *dónde*, y un pin GPS sí. Este
módulo es el segundo canal del mismo reporte (use_cases, beats 3:50 y 4:25):

- `POST /webhooks/telegram`: el webhook de la Bot API. Un `Update` con `message` o
  `edited_message` (ubicación en vivo) con `location` y/o `text`. Cada chat es una
  «llamada» más para el journal, `call_id = tg_<chat_id>`: `call.started` la primera
  vez, `call.transcript.partial` por cada texto, `citizen.location` por cada pin,
  y `world.fact.asserted poi:<id>:confirmed` (`observed`, fuente `call:tg_<chat>`) si
  el pin cae a menos de `GeoAnchor.snap_m` de un POI. Un pin **no** se resuelve por
  *fuzzy match*: se ancla por distancia o se queda sin POI, y el hueco se ve.
- El texto que acompaña al pin pasa por el mismo camino de percepción que el tool
  `report_fact`: Jev si está (elige entre ids del escenario), si no `fenic` con
  `Literal` (`inferred`), y como última red de seguridad reglas de texto explícitas.
- `signal_dispatcher`: `call.signal.requested` para un `tg_*` se redacta con
  `humanlike.draft_for`, se refina con Humalike (mismo `DISPATCH_PROMPT`) y se manda
  por `sendMessage`; se publica `call.signal.sent`. Las sesiones de voz las atiende
  `humanlike.signal_dispatcher`, que ignora los `tg_*`.

Sin `TELEGRAM_BOT_TOKEN` (o con `VELA_NO_TELEGRAM`) todo funciona igual salvo el
envío, que se anota y no se manda: los tests no tocan la red y la demo no depende de
que el bot conteste.

La ruta trae su propio secreto (`X-Telegram-Bot-Api-Secret-Token`, el que se pasa a
`setWebhook`): Telegram no puede mandar el `X-Vela-Token` del resto de `/webhooks/*`.
**Sin `TELEGRAM_SECRET_TOKEN` la ruta responde 401**: es la única puerta que ve internet
sin el token compartido, y no se deja abierta por olvidar una variable.

El pin se cuelga de la llamada de voz que lo motivó (`recent_voice_call`): el
`citizen.location` declara en `causes` el `call.started` de esa llamada (el dashboard
cierra con él su hueco de `location_hint`), y los recuentos que el tool dejó sin ubicar
(«dos que no pueden andar» sin saber dónde) se publican con el POI del pin.

Una ubicación en vivo (`edited_message`) publica `citizen.location` en cada edición
(el mapa sigue el pin) pero solo confirma el POI y contesta cuando el POI cambia.

El token del bot no sale por el log: `httpx` loguea a INFO la URL de cada petición y la
URL de la Bot API lo lleva; un `logging.Filter` lo tapa (`bot***`).

CLI: `python -m voice.telegram --set-webhook <url_pública>` | `--info` |
`--simulate --to http://localhost:8000 --lat 40.4168 --lon -3.7038 --text "..."`.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import math
import re
import time
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Header, HTTPException, Request

from contracts.bus import current_run_id, current_t_sim, make_event, publish, subscribe
from contracts.calls import CallFacts, Fact, Severity
from contracts.events import EventType
from contracts.scenario import GeoAnchor
from contracts.settings import settings
from voice import humanlike, pois
from voice.webhooks import _fact_event, _gateway

log = logging.getLogger("voice.telegram")

_TOKEN_RE = re.compile(r"bot\d+:[\w-]+")


class RedactBotToken(logging.Filter):
    """`httpx` loguea a INFO «HTTP Request: POST https://api.telegram.org/bot<TOKEN>/…».
    Se instala en el logger de `httpx` al importar: el token nunca llega a un handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        token = settings.telegram_bot_token
        hit = bool(_TOKEN_RE.search(msg)) or bool(token and token in msg)
        if hit:
            if token:
                msg = msg.replace(token, "***")
            record.msg = _TOKEN_RE.sub("bot***", msg)
            record.args = ()
        return True


def install_log_filter() -> None:
    for name in ("httpx", "httpcore"):
        lg = logging.getLogger(name)
        if not any(isinstance(f, RedactBotToken) for f in lg.filters):
            lg.addFilter(RedactBotToken())


install_log_filter()

router = APIRouter(tags=["voice"])
"""Sin prefijo: el gateway lo monta con `prefix="/webhooks"` (`POST /webhooks/telegram`).
No lo incluye `voice.router` para que `python -m voice.telegram` no importe el paquete
entero dos veces."""

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
TELEGRAM_API = "https://api.telegram.org"
SEND_TIMEOUT_S = 5.0
CALL_PREFIX = humanlike.TELEGRAM_PREFIX  # "tg_"
PIN_CONFIDENCE = 0.95
"""Un pin GPS de un móvil: observado, pero no infalible (precisión del GPS, un pin
compartido desde otro sitio). Por encima del umbral duro (0,85) y por debajo de 1."""
PENDING_CONFIDENCE = 0.7
"""Un recuento del tool («dos que no pueden andar») colocado por el pin: el dato es de la
llamada y el sitio del pin, ninguno verificado por Jev. `inferred`, por encima del umbral
blando (0,55) y por debajo del pin."""
RECENT_CALL_S = 300.0
"""Una llamada colgada hace menos de esto sigue siendo «la llamada» de la que viene el
pin: el vecino cuelga y manda la ubicación."""

M_PER_DEG_LAT = 111_320.0
"""Metros por grado de latitud; la longitud se corrige con el coseno de la latitud."""

ACK_WITH_POI = (
    "Ubicación recibida: junto a {poi}. Ya avisamos a las unidades; no se mueva."
)
ACK_WITHOUT_POI = (
    "Ubicación recibida. No coincide con ningún punto conocido; la tenemos en el mapa "
    "y avisamos a las unidades. No se mueva."
)
ACK_TEXT_ONLY = (
    "Recibido. Si puede, mande su ubicación (clip → Ubicación) y sabremos exactamente "
    "dónde está."
)


# --- Proyección y anclaje ------------------------------------------------------


def project(geo: GeoAnchor, lat: float, lon: float) -> tuple[float, float]:
    """lat/lon → (x, z) del mundo. Equirectangular local alrededor del ancla: a la
    escala de un escenario (cientos de metros de mundo) el error es despreciable.
    x apunta al este y z al sur, como en Minecraft."""
    cos_lat = math.cos(math.radians(geo.lat))
    dx_m = (lon - geo.lon) * M_PER_DEG_LAT * cos_lat * geo.scale
    dz_m = -(lat - geo.lat) * M_PER_DEG_LAT * geo.scale
    return geo.x + dx_m, geo.z + dz_m


# --- El Update de la Bot API ---------------------------------------------------


@dataclass
class Incoming:
    update_id: int
    chat_id: str
    first_name: str
    text: str | None
    lat: float | None
    lon: float | None
    live: bool


def parse_update(update: dict) -> Incoming | None:
    """`message` o `edited_message` con `chat.id` y, opcionalmente, `location` y
    `text`. Cualquier otro update (callbacks, canales) → None, se ignora."""
    msg = update.get("message")
    live = False
    if msg is None:
        msg = update.get("edited_message")
        live = msg is not None  # una ubicación en vivo llega como edición del mensaje
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat") or {}
    if chat.get("id") is None:
        return None
    loc = msg.get("location") or {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    has_loc = isinstance(lat, (int, float)) and isinstance(lon, (int, float))
    text = msg.get("text")
    if not has_loc and not text:
        return None
    return Incoming(
        update_id=int(update.get("update_id") or 0),
        chat_id=str(chat["id"]),
        first_name=str((msg.get("from") or {}).get("first_name") or "vecino"),
        text=str(text).strip() if text else None,
        lat=float(lat) if has_loc else None,
        lon=float(lon) if has_loc else None,
        live=live and has_loc,
    )


# --- Estado por proceso --------------------------------------------------------

_seen_updates: set[int] = set()
_started: dict[str, str] = {}  # chat_id → run_id en el que ya se publicó call.started
_sessions: dict[str, humanlike.ConversationMonitor] = {}
_last_poi: dict[str, str | None] = {}  # chat_id → POI del último pin (None = sin anclar)
_dry = itertools.count(1)


def reset() -> None:
    """Solo tests."""
    _seen_updates.clear()
    _started.clear()
    _sessions.clear()
    _last_poi.clear()


def session(call_id: str, run_id: str) -> humanlike.ConversationMonitor:
    """El monitor de un chat: guarda la transcripción y lleva la percepción de Jev.
    No se arranca (`start`) porque no hay SSE que consumir, y no entra en
    `humanlike.MONITORS` porque sus señales no van a HappyRobot, van por aquí."""
    mon = _sessions.get(call_id)
    if mon is None:
        hl, hr = humanlike.clients()
        mon = humanlike.ConversationMonitor(
            humanlike.CallState(session_id=call_id, run_id=run_id), hl, hr
        )
        _sessions[call_id] = mon
    return mon


# --- La ruta ---------------------------------------------------------------------


NO_SECRET_DETAIL = "sin TELEGRAM_SECRET_TOKEN"


def _check_secret(token: str) -> None:
    """Sin secreto configurado la ruta está cerrada, no abierta: el gateway exime a
    `/webhooks/telegram` del `X-Vela-Token` solo si este secreto existe."""
    expected = settings.telegram_secret_token
    if not expected:
        raise HTTPException(status_code=401, detail=NO_SECRET_DETAIL)
    if token != expected:
        raise HTTPException(status_code=401, detail="secret")


@router.post("/telegram")
async def telegram_webhook(
    request: Request, x_telegram_bot_api_secret_token: str = Header(default="")
) -> dict:
    _check_secret(x_telegram_bot_api_secret_token)
    update = await request.json()
    if not isinstance(update, dict):
        raise HTTPException(status_code=422, detail="update")
    inc = parse_update(update)
    if inc is None:
        return {"ok": True, "ignored": True}
    if inc.update_id in _seen_updates:
        return {"dup": True}
    try:
        out = await handle(inc)
    except Exception as exc:  # se anota y Telegram reintenta el update
        # Se responde 200 para que Telegram no reintente en bucle; el update no se marca
        # como visto, así que si lo reintenta (o el vecino reenvía) se procesa entero.
        log.exception("telegram: update %s de %s falló", inc.update_id, inc.chat_id)
        return {"ok": False, "error": repr(exc)}
    _seen_updates.add(inc.update_id)  # visto solo cuando se ha procesado de verdad
    return out


async def handle(inc: Incoming) -> dict:
    """Un update, de punta a punta: eventos al bus, percepción del texto y acuse al
    vecino. Nunca lanza por el envío: un bot caído se anota."""
    run_id = current_run_id()
    if not run_id:
        log.warning("telegram: update de %s sin run en curso", inc.chat_id)
    call_id = f"{CALL_PREFIX}{inc.chat_id}"
    mon = session(call_id, run_id)
    voice = recent_voice_call()
    causes: list[int] = []

    if _started.get(inc.chat_id) != run_id:
        _started[inc.chat_id] = run_id
        ev = make_event(
            EventType.CALL_STARTED,
            {
                "call_id": call_id,
                "task_id": None,
                "to": inc.chat_id,
                "direction": "inbound",
                "channel": "telegram",
            },
            source="voice",
        )
        await publish(ev)
        causes.append(ev.seq)

    if inc.text:
        mon.add_turn("user", inc.text)
        ev = make_event(
            EventType.CALL_TRANSCRIPT_PARTIAL,
            {"call_id": call_id, "speaker": humanlike.CALLER_NAME, "text": inc.text},
            source="voice",
        )
        await publish(ev)
        causes.append(ev.seq)

    poi_id: str | None = None
    poi_name: str | None = None
    n_pending = 0
    if inc.lat is not None and inc.lon is not None:
        # El pin declara la llamada de voz de la que viene: con ese seq en `causes` el
        # dashboard cierra el hueco `location_hint` de ESA llamada, no solo el del chat.
        loc_causes = list(causes)
        if voice is not None and voice.started_seq is not None:
            loc_causes.append(voice.started_seq)
        poi_id, poi_name, loc_seq = await _publish_location(inc, call_id, loc_causes)
        causes = [loc_seq]
        changed = inc.chat_id not in _last_poi or _last_poi[inc.chat_id] != poi_id
        _last_poi[inc.chat_id] = poi_id
        if inc.live and not changed:
            # Ubicación en vivo sin cambio de POI: el mapa sigue el pin y nada más. Ni
            # acuse (sería uno por segundo) ni hecho (ya está) ni replan.
            log.info("telegram %s: pin en vivo, mismo POI (%s)", call_id, poi_id)
            return {
                "ok": True,
                "call_id": call_id,
                "poi_id": poi_id,
                "poi_name": poi_name,
                "live": True,
                "unchanged": True,
                "facts_from_text": 0,
                "ack": None,
                "sent": None,
            }
        if poi_id:
            key = f"poi:{poi_id}:confirmed"
            if key not in mon.state.tool_facts_keys:
                fact = _fact_event(_pin_fact(poi_id, call_id), call_id)
                fact.causes = [loc_seq]
                await publish(fact)
                causes.append(fact.seq)
                mon.state.tool_facts_keys.add(key)
            if voice is not None:
                n_pending = await publish_pending(voice, mon, poi_id, loc_seq)

    n_text_facts = 0
    if inc.text:
        n_text_facts = await perceive_text(mon, inc.text, poi_id, causes)

    if inc.lat is None:
        ack = ACK_TEXT_ONLY
    elif poi_name:
        ack = ACK_WITH_POI.format(poi=poi_name)
    else:
        ack = ACK_WITHOUT_POI
    sent = await send_message(inc.chat_id, ack)
    mon.add_turn("assistant", ack)
    await publish(
        make_event(
            EventType.CALL_TRANSCRIPT_PARTIAL,
            {"call_id": call_id, "speaker": humanlike.AGENT_NAME, "text": ack},
            source="voice",
            causes=causes,
        )
    )
    log.info(
        "telegram %s (%s): pin=%s poi=%s llamada=%s pendientes=%d hechos_texto=%d acuse=%s",
        call_id,
        inc.first_name,
        inc.lat is not None,
        poi_id,
        voice.call_id if voice else "-",
        n_pending,
        n_text_facts,
        sent or "no enviado",
    )
    return {
        "ok": True,
        "call_id": call_id,
        "poi_id": poi_id,
        "poi_name": poi_name,
        "voice_call_id": voice.call_id if voice else None,
        "facts_from_call": n_pending,
        "facts_from_text": n_text_facts,
        "ack": ack,
        "sent": sent,
    }


# --- La llamada de voz de la que viene el pin ----------------------------------------


def recent_voice_call() -> humanlike.CallState | None:
    """La llamada de voz a la que se cuelga el pin: la más reciente con el ack del tool
    aún esperando; si no, la entrante viva más reciente; si no, la última colgada hace
    menos de `RECENT_CALL_S`. None si no hay ninguna: el pin va solo, como hasta ahora."""
    alive = [
        m.state
        for cid, m in humanlike.MONITORS.items()
        if not cid.startswith(CALL_PREFIX) and not m.state.ended
    ]
    pool = [st for st in alive if st.tool_pending] or alive
    if pool:
        return max(pool, key=lambda st: st.started_t)
    now = time.time()
    ended = [
        st
        for cid, st in humanlike.RECENT_ENDED.items()
        if not cid.startswith(CALL_PREFIX) and now - st.ended_t < RECENT_CALL_S
    ]
    return max(ended, key=lambda st: st.ended_t) if ended else None


async def publish_pending(
    voice: humanlike.CallState,
    mon: humanlike.ConversationMonitor,
    poi_id: str,
    loc_seq: int,
) -> int:
    """Los recuentos que la llamada dejó sin ubicar (`pending_facts` del camino sin Jev,
    o `people_immobile` observado con `location_hint` abierto en el de Jev) entran al
    estado con el POI del pin: `inferred` (el dato es de la llamada, el sitio del pin),
    fuente el chat, causa el pin. Devuelve cuántos."""
    counts = dict(voice.pending_facts)
    severity: Severity = voice.pending_severity  # type: ignore[assignment]
    vm = humanlike.MONITORS.get(voice.session_id)
    if vm is not None and vm.perception.active:
        from_jev = vm.perception.unlocated_counts()
        if from_jev:
            counts.update(from_jev)
            severity = vm.perception.completeness.severity()
    n = 0
    call_id = mon.state.call_id
    for suffix, value in counts.items():
        key = f"poi:{poi_id}:{suffix}"
        if key in mon.state.tool_facts_keys:
            continue
        ev = _fact_event(
            Fact(
                key=key,
                value=value,
                confidence=PENDING_CONFIDENCE,
                source=f"call:{call_id}",
                severity=severity,
                t_sim=current_t_sim(),
                kind="inferred",
                call_id=call_id,
            ),
            call_id,
        )
        ev.causes = [loc_seq]
        await publish(ev)
        mon.state.tool_facts_keys.add(key)
        n += 1
    if n:
        log.info(
            "telegram %s: %d recuento(s) de %s colocados en %s",
            call_id,
            n,
            voice.call_id,
            poi_id,
        )
    voice.pending_facts.clear()
    return n


async def _publish_location(
    inc: Incoming, call_id: str, causes: list[int]
) -> tuple[str | None, str | None, int]:
    """`citizen.location`, proyectado y anclado si el escenario tiene `geo`."""
    assert inc.lat is not None and inc.lon is not None
    geo = pois.geo()
    x = z = None
    poi_id = poi_name = None
    if geo is not None:
        x, z = project(geo, inc.lat, inc.lon)
        near = pois.nearest_poi(x, z, geo.snap_m)
        if near is not None:
            poi_id, poi_name = near.id, near.name
    else:
        log.info("telegram: el escenario no tiene `geo`; el pin no se proyecta")
    ev = make_event(
        EventType.CITIZEN_LOCATION,
        {
            "call_id": call_id,
            "channel": "telegram",
            "chat_id": inc.chat_id,
            "lat": inc.lat,
            "lon": inc.lon,
            "x": None if x is None else round(x, 1),
            "z": None if z is None else round(z, 1),
            "poi_id": poi_id,
            "poi_name": poi_name,
            "text": inc.text,
            "live": inc.live,
        },
        source="voice",
        causes=causes,
    )
    await publish(ev)
    return poi_id, poi_name, ev.seq


def _pin_fact(poi_id: str, call_id: str) -> Fact:
    return Fact(
        key=f"poi:{poi_id}:confirmed",
        value=True,
        confidence=PIN_CONFIDENCE,
        source=f"call:{call_id}",
        severity="critical",
        t_sim=current_t_sim(),
        kind="observed",
        call_id=call_id,
    )


# --- El texto: el mismo camino de percepción que el tool -------------------------


async def perceive_text(
    mon: humanlike.ConversationMonitor,
    text: str,
    poi_id: str | None,
    causes: list[int],
) -> int:
    """Lo que dice el texto (inmóviles, heridos, carretera cortada) entra por el mismo
    sitio que en la llamada. Con Jev: un tick final sobre la transcripción del chat,
    con `location_hint` ya observado por el pin (el pin manda sobre el texto). Sin
    Jev: `fenic` con `Literal` y, si tampoco está, reglas de texto; en los dos casos
    los hechos salen `inferred` y con el POI del pin. Devuelve cuántos hechos nuevos."""
    if mon.perception.active:
        if poi_id:
            mon.perception.pin(poi_id, PIN_CONFIDENCE)  # Jev no lo pregunta ni lo pisa
        before = mon.perception.facts_published
        await mon.perception.tick(mon.jev_turns(), final=True, wait=True)
        return mon.perception.facts_published - before

    cf = await extract_text(text)
    if cf is None:
        cf = keyword_facts(text)
    if cf is None:
        return 0
    cf.resolved_poi_id = poi_id or pois.resolve_poi_local(cf.location_hint)
    facts = [
        f
        for f in _gateway().to_facts(cf, current_t_sim(), mon.state.call_id)
        if f.key not in mon.state.tool_facts_keys
    ]
    for f in facts:
        ev = _fact_event(f, mon.state.call_id)
        ev.causes = list(causes)
        await publish(ev)
        mon.state.tool_facts_keys.add(f.key)
    return len(facts)


async def extract_text(text: str) -> CallFacts | None:
    """`fenic.semantic.extract` con `Literal` (el plan B de la llamada). None si no
    hay sesión de fenic o tarda más de lo que `VoiceGateway.extract` tolera."""
    return await _gateway().extract(text)


_IMMOBILE_RE = re.compile(
    r"\b(no puede[n]? (andar|moverse|caminar|salir)|"
    r"sin movilidad|en silla de ruedas|atrapad[oa]s?|no se puede[n]? mover)\b"
)
_INJURED_RE = re.compile(r"\bherid[oa]s?\b")
_CUT_RE = re.compile(r"\b(cortad[oa]|bloquead[oa]|impracticable|un árbol|arbol)\b")
_WORDS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}


def keyword_facts(text: str) -> CallFacts | None:
    """Red de seguridad sin Jev ni fenic: reglas de texto explícitas, confianza baja,
    `inferred`. No inventa un POI (eso lo da el pin) y no cuenta más de lo que dice el
    texto: «mi madre no puede andar» es una persona sin movilidad, no tres."""
    norm = pois.normalize(text)
    cf = CallFacts(confidence=0.5)
    found = False
    if _IMMOBILE_RE.search(norm):
        m = re.search(r"(\w+) personas?", norm)
        n = _WORDS.get(m.group(1)) if m else None
        cf.people_immobile = n or 1
        cf.urgency = "critical"
        found = True
    if _INJURED_RE.search(norm):
        m = re.search(r"(\w+) herid", norm)
        cf.injuries = _WORDS.get(m.group(1), 1) if m else 1
        cf.urgency = "critical"
        found = True
    if _CUT_RE.search(norm):
        edge = pois.resolve_edge_local(text)
        if edge:
            cf.road_blocked = edge
            found = True
    return cf if found else None


# --- Bot API ---------------------------------------------------------------------


def _token_ok() -> bool:
    return bool(settings.telegram_bot_token) and not settings.vela_no_telegram


def _client() -> httpx.AsyncClient:
    """Los tests lo sustituyen por uno con `MockTransport`."""
    return httpx.AsyncClient(timeout=SEND_TIMEOUT_S)


async def api_call(method: str, body: dict) -> dict | None:
    """Un método de la Bot API. Devuelve `result` o None si falla; nunca lanza. El
    token no sale por el log: se dice el método y el motivo."""
    url = f"{TELEGRAM_API}/bot{settings.telegram_bot_token}/{method}"
    try:
        async with _client() as c:
            r = await c.post(url, json=body)
    except httpx.HTTPError as exc:
        log.warning("telegram %s: %s", method, type(exc).__name__)
        return None
    if r.status_code != 200:
        log.warning("telegram %s: HTTP %s %s", method, r.status_code, r.text[:160])
        return None
    data = r.json()
    if not data.get("ok"):
        log.warning("telegram %s: %s", method, data.get("description"))
        return None
    return data.get("result") if isinstance(data.get("result"), dict) else {}


async def send_message(chat_id: str, text: str) -> str | None:
    """`sendMessage`. Devuelve `tg_msg_<message_id>` si se mandó, `tg_dry_<n>` si no
    hay token o se ha apagado el canal (se anota, no se manda), None si la API falló."""
    if not _token_ok():
        sid = f"tg_dry_{next(_dry)}"
        log.info("telegram sin token: no se manda a %s (%s): %s", chat_id, sid, text)
        return sid
    res = await api_call("sendMessage", {"chat_id": chat_id, "text": text})
    if res is None:
        return None
    return f"tg_msg_{res.get('message_id', '?')}"


# --- Señales del core para un chat ----------------------------------------------


async def signal_dispatcher() -> None:
    """Tarea de fondo: `call.signal.requested` de un `tg_*` → mensaje al chat."""
    async for ev in subscribe(EventType.CALL_SIGNAL_REQUESTED):
        call_id = str(ev.payload.get("call_id", ""))
        if not call_id.startswith(CALL_PREFIX):
            continue
        try:
            await on_signal(
                call_id,
                str(ev.payload.get("key", "")),
                dict(ev.payload.get("payload") or {}),
                [ev.seq],
            )
        except Exception as exc:  # noqa: BLE001 — una señal fallida no para las demás
            log.warning("telegram signal %s: %r", call_id, exc)


async def on_signal(
    call_id: str, key: str, payload: dict, causes: list[int]
) -> str | None:
    """Borrador → Humalike (`FORESEE_HOT_S`, mismo prompt que en voz) → `sendMessage`
    → `call.signal.sent`. Si Humalike no llega, va el borrador."""
    t0 = time.perf_counter()
    chat_id = call_id.removeprefix(CALL_PREFIX)
    mon = _sessions.get(call_id)
    draft = humanlike.draft_for(key, payload)
    message, refined = draft, False
    hl, _ = humanlike.clients()
    prompt = (
        humanlike.DISPATCH_PROMPT if key == "unit_dispatched" else humanlike.SYSTEM_PROMPT
    )
    try:
        res = await asyncio.wait_for(
            hl.foresee(
                mon.transcript_turns() if mon else [],
                draft,
                system_prompt=prompt,
                subject_name=humanlike.CALLER_NAME,
            ),
            humanlike.FORESEE_HOT_S,
        )
    except TimeoutError:
        res = None
    if res is not None and res.refined_reply:
        message, refined = res.refined_reply, True
    sid = await send_message(chat_id, message)
    if mon is not None:
        mon.add_turn("assistant", message)
    if sid is None:
        log.warning("telegram signal %s no enviada (%s)", call_id, key)
        return None
    await publish(
        make_event(
            EventType.CALL_SIGNAL_SENT,
            {
                "call_id": call_id,
                "key": key,
                "signal_id": sid,
                "message": message,
                "latency_ms": (time.perf_counter() - t0) * 1000,
                "refined": refined,
            },
            source="voice",
            causes=causes,
        )
    )
    return sid


# --- CLI --------------------------------------------------------------------------


def _webhook_url(public_url: str) -> str:
    return public_url.rstrip("/") + "/webhooks/telegram"


def fake_update(
    chat_id: str,
    lat: float | None,
    lon: float | None,
    text: str | None,
    live: bool = False,
    update_id: int | None = None,
) -> dict:
    """Un `Update` como el que manda la Bot API, para ensayar sin móvil."""
    msg: dict = {
        "message_id": int(time.time()) % 100_000,
        "date": int(time.time()),
        "chat": {"id": int(chat_id), "type": "private", "first_name": "Vecino"},
        "from": {"id": int(chat_id), "is_bot": False, "first_name": "Vecino"},
    }
    if lat is not None and lon is not None:
        msg["location"] = {"latitude": lat, "longitude": lon}
    if text:
        msg["text"] = text
    return {
        "update_id": update_id if update_id is not None else int(time.time() * 1000),
        ("edited_message" if live else "message"): msg,
    }


async def _cli(args: argparse.Namespace) -> int:
    if args.set_webhook:
        if not _token_ok():
            print("sin TELEGRAM_BOT_TOKEN (o VELA_NO_TELEGRAM): no se puede registrar")
            return 1
        body = {
            "url": _webhook_url(args.set_webhook),
            "allowed_updates": ["message", "edited_message"],
            "drop_pending_updates": True,
        }
        if settings.telegram_secret_token:
            body["secret_token"] = settings.telegram_secret_token
        res = await api_call("setWebhook", body)
        print("setWebhook", body["url"], "→", "ok" if res is not None else "fallo")
        info = await api_call("getWebhookInfo", {})
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0 if res is not None else 1
    if args.info:
        info = await api_call("getWebhookInfo", {}) if _token_ok() else None
        print(json.dumps(info, ensure_ascii=False, indent=2) if info else "sin token")
        return 0
    if args.simulate:
        update = fake_update(args.chat, args.lat, args.lon, args.text, live=args.live)
        headers = {SECRET_HEADER: settings.telegram_secret_token}
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(_webhook_url(args.to), json=update, headers=headers)
        print(r.status_code, r.text)
        return 0 if r.status_code == 200 else 1
    return 2


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="Bot de Telegram de vela")
    p.add_argument("--set-webhook", metavar="URL", help="URL pública del gateway")
    p.add_argument("--info", action="store_true", help="getWebhookInfo")
    p.add_argument("--simulate", action="store_true", help="POST de un Update falso")
    p.add_argument("--to", default="http://localhost:8000", help="gateway local")
    p.add_argument("--chat", default="4471123")
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    p.add_argument("--text")
    p.add_argument("--live", action="store_true", help="como edited_message")
    args = p.parse_args()
    if not (args.set_webhook or args.info or args.simulate):
        p.print_help()
        raise SystemExit(2)
    raise SystemExit(asyncio.run(_cli(args)))


if __name__ == "__main__":
    main()
