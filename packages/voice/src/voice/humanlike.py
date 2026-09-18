"""Humalike encima de HappyRobot: el vecino llama, HappyRobot descuelga, Humalike
modula cómo habla el agente.

HappyRobot es la telefonía y la ejecución: audio, STT/TTS, end-of-turn, tools,
signals, SSE. Humalike es el middleware de comportamiento: turn-taking (decisión
`speak` / `stay_silent` y tags de silencio), tono emocional (`foresee`), ritmo
(`pace` en la señal `coach`), y `analyze` al colgar.

Modo "coach y refinado, nunca gate": HappyRobot sigue decidiendo cuándo habla el
agente; Humalike ajusta tono, ritmo y pausas por señales y redacta lo que nosotros
controlamos (el ack del tool y el aviso de replan). **El replan nunca espera a
Humalike**: `foresee` tiene 1,5 s y se cae al borrador.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from contracts.bus import make_event, publish, subscribe
from contracts.calls import CallOutcome, CallResult
from contracts.events import EventType
from contracts.settings import settings

log = logging.getLogger("voice.humanlike")

HUMALIKE_BASE = "https://api.humalike.com"
HAPPYROBOT_BASE = "https://platform.happyrobot.ai/api/v2"

FORESEE_HOT_S = 3.0
PLAN_WAIT_S = 3.5
"""Cuánto espera el ack del tool a que el core replanifique. Si el plan llega, el
agente dice en la misma frase qué unidad va: una sola ida y vuelta, sin signals.
Si no llega, el ack sale igual y la noticia irá por signal cuando haya plan."""
"""En el camino de vuelta al agente (ack del tool, signal): por encima, borrador.
Medido el viernes: `foresee` tarda 2,4 a 2,6 s; con 1,5 s nunca llegaba."""
FORESEE_BG_S = 4.0
"""Lectura emocional en background por turno del vecino. Nadie la espera."""
TURN_S = 2.0
"""`submit_messages` tarda 1,1 a 1,4 s. Va en background: nadie lo espera."""
SILENCE_S = 6.0
"""Sin mensaje de nadie durante esto → coach `acknowledge`. En 1:1 el turn-taking de
Humalike siempre dice `speak` y no etiqueta silencios de voz: lo medimos aquí."""
SIGNAL_S = 2.0
ANALYZE_S = 10.0
EXTRACT_TIMEOUT_S = 4.0
"""Por encima de esto se emite `CallResult` con `facts=None` y la transcripción
cruda va al dashboard marcada como *sin extraer*. La demo continúa."""
COACH_MIN_GAP_S = 3.0
EMOTION_DELTA = 0.3
SSE_BACKOFF = (0.5, 1.0, 2.0, 4.0)

AGENT_NAME = "operador"
CALLER_NAME = "vecino"
NEUTRAL_DRAFT = "Le escucho. Dígame exactamente dónde está."

SYSTEM_PROMPT = (
    "Eres el operador de emergencias del 112 durante un incendio forestal. "
    "Hablas en español de España, siempre de usted, con calma y frases cortas. "
    "Nunca tutees ni uses vosotros. Tu objetivo: saber "
    "dónde está la persona, cuántos son, si alguien no puede moverse y qué "
    "carretera está cortada, y que se sienta acompañada hasta que llegue ayuda."
)

RISK_RANK = {"low": 0.2, "medium": 0.5, "high": 0.85}


def _bearer(token: str) -> dict[str, str]:
    """Sin token no se manda cabecera: httpx rechaza `Bearer ` vacío."""
    return {"Authorization": f"Bearer {token}"} if token else {}


# --- Resultados tipados ------------------------------------------------------


@dataclass
class ForeseeResult:
    refined_reply: str
    emotions: list[dict[str, Any]]
    risk: float | None
    raw: dict[str, Any]


@dataclass
class TurnDecision:
    decision: str  # "speak" | "stay_silent"
    turn_epoch: int
    tags: list[str]


def _risk_to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return RISK_RANK.get(value.lower())
    return None


def _emotions_of(res: dict[str, Any], subject: str | None = None) -> list[dict[str, Any]]:
    states = res.get("mental_state") or []
    for st in states:
        if subject is None or st.get("name") == subject:
            return [
                {"type": e.get("type", "?"), "intensity": float(e.get("intensity", 0))}
                for e in st.get("emotions") or []
            ]
    return []


def _worst_risk(res: dict[str, Any]) -> float | None:
    risks = [_risk_to_float(r.get("risk")) for r in res.get("predicted_reaction") or []]
    risks = [r for r in risks if r is not None]
    return max(risks) if risks else None


# --- Humalike ----------------------------------------------------------------


class HumalikeClient:
    """Un cliente para las seis APIs. Todo error (402, 5xx, timeout, red) devuelve
    `None`, se loguea una vez por ruta y nunca lanza: Humalike modula, no bloquea."""

    def __init__(
        self,
        token: str | None = None,
        base: str = HUMALIKE_BASE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token if token is not None else settings.humanlike_api_key
        self._client = client or httpx.AsyncClient(
            base_url=base, headers=_bearer(self._token)
        )
        self._warned: set[str] = set()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, body: dict, timeout: float) -> dict | None:
        try:
            r = await self._client.post(path, json=body, timeout=timeout)
        except (TimeoutError, httpx.HTTPError) as exc:
            self._warn(path, f"{type(exc).__name__}: {exc}")
            return None
        if r.status_code == 402:
            self._warn(path, "402 sin créditos: no se ejecutó ni se cobró")
            return None
        if r.status_code >= 400:
            self._warn(path, f"{r.status_code} {r.text[:200]}")
            return None
        try:
            return r.json()
        except ValueError:
            self._warn(path, "respuesta no JSON")
            return None

    def _warn(self, path: str, msg: str) -> None:
        if path not in self._warned:
            self._warned.add(path)
            log.warning("humalike %s: %s", path, msg)

    async def foresee(
        self,
        transcript: list[dict[str, str]],
        candidate_reply: str,
        agent_name: str = AGENT_NAME,
        system_prompt: str = SYSTEM_PROMPT,
        subject_name: str | None = None,
        timeout: float = FORESEE_HOT_S,
    ) -> ForeseeResult | None:
        """Theory of Mind: estado mental del vecino + la frase refinada."""
        if not transcript:
            transcript = [{"speaker": CALLER_NAME, "text": "..."}]
        body: dict[str, Any] = {
            "transcript": transcript,
            "candidate_reply": candidate_reply,
            "agent_name": agent_name,
            "system_prompt": system_prompt,
        }
        if subject_name:
            body["subject_name"] = subject_name
        res = await self._post("/v1/foresee/actions/foresee", body, timeout)
        if not res:
            return None
        return ForeseeResult(
            refined_reply=str(res.get("refined_reply") or candidate_reply),
            emotions=_emotions_of(res, subject_name),
            risk=_worst_risk(res),
            raw=res,
        )

    async def analyze(
        self,
        transcript: list[dict[str, str]],
        agent_name: str = AGENT_NAME,
        focus: str | None = None,
        timeout: float = ANALYZE_S,
    ) -> dict | None:
        """Social Observability, al colgar: `health_score`, recepción, hallazgos."""
        if not transcript:
            return None
        messages = [
            {"id": f"m{i + 1}", "speaker": t["speaker"], "text": t["text"]}
            for i, t in enumerate(transcript)
        ]
        body: dict[str, Any] = {
            "agent_name": agent_name,
            "transcript": {"messages": messages, "source": "vela"},
        }
        if focus:
            body["focus"] = focus
        return await self._post("/v1/social-observability/actions/analyze", body, timeout)

    async def ingest(self, scope_id: str, speaker: str, text: str) -> None:
        """Social Memory, escritura gratis y fire-and-forget."""
        await self._post(
            "/v1/social-memory/actions/ingest",
            {"scope_id": scope_id, "transcript": [{"speaker": speaker, "text": text}]},
            timeout=2.0,
        )

    async def recall(self, scope_id: str, speaker: str, text: str) -> str:
        """Contexto inyectable sobre quien llama, antes de la saliente."""
        res = await self._post(
            "/v1/social-memory/actions/recall",
            {"scope_id": scope_id, "message": {"speaker": speaker, "text": text}},
            timeout=2.0,
        )
        return str((res or {}).get("context") or "")

    async def open_thread(
        self, thread_id: str | None = None, social_signals: bool = True
    ) -> tuple[str, str] | None:
        """Turn-taking: abre (o reabre) el hilo. Devuelve (thread_id, connect_url)."""
        body: dict[str, Any] = {}
        if thread_id:
            body["thread_id"] = thread_id
        if social_signals:
            body["integrations"] = {"social_signals": {"enabled": True}}
        res = await self._post("/v1/turn-taking/actions/open_thread", body, timeout=3.0)
        if not res:
            return None
        thread = res.get("thread") or {}
        realtime = res.get("realtime") or thread.get("realtime") or {}
        tid = thread.get("id") or res.get("thread_id")
        if not tid:
            return None
        return str(tid), str(realtime.get("connect_url") or "")

    async def submit_messages(
        self,
        thread_id: str,
        sender: str,
        content: str,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> TurnDecision | None:
        """Turn-taking: `speak` / `stay_silent`, `turn_epoch` y tags de comportamiento."""
        res = await self._post(
            "/v1/turn-taking/actions/submit_messages",
            {
                "thread_id": thread_id,
                "messages": [{"sender": sender, "content": content}],
                "system_prompt": system_prompt,
            },
            timeout=TURN_S,
        )
        if not res:
            return None
        return TurnDecision(
            decision=str(res.get("decision") or "speak"),
            turn_epoch=int(res.get("turn_epoch") or 0),
            tags=[str(t) for t in res.get("tags") or []],
        )

    async def respond(
        self,
        thread_id: str,
        content: str,
        turn_epoch: int,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> list[dict[str, Any]] | None:
        """Reservado (experimento del sábado noche): refina y pacea la frase en 1–5
        trozos con `deliver_at`. Devuelve `scheduled`, o None si `superseded`."""
        res = await self._post(
            "/v1/turn-taking/actions/respond",
            {
                "thread_id": thread_id,
                "content": content,
                "turn_epoch": turn_epoch,
                "system_prompt": system_prompt,
                "agent_name": AGENT_NAME,
            },
            timeout=3.0,
        )
        if not res or res.get("superseded"):
            return None
        return list(res.get("scheduled") or [])


# --- HappyRobot, lado vivo (SSE y signals) -------------------------------------


class HappyRobotLive:
    """Lo que `voice` necesita de HappyRobot durante una llamada entrante. El
    disparo de la saliente vive en `voice.happyrobot` (Carlos)."""

    def __init__(
        self,
        api_key: str | None = None,
        base: str = HAPPYROBOT_BASE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        key = api_key if api_key is not None else settings.happyrobot_api_key
        self._client = client or httpx.AsyncClient(base_url=base, headers=_bearer(key))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def stream(self, session_id: str, backfill: int = 50) -> AsyncIterator[dict]:
        """`GET /sessions/{id}/stream`: un dict por mensaje, en vivo. Reconecta con
        backoff; tras agotar los intentos, termina en silencio."""
        attempt = 0
        while True:
            try:
                async with self._client.stream(
                    "GET",
                    f"/sessions/{session_id}/stream",
                    params={"backfillLimit": backfill},
                    timeout=httpx.Timeout(10.0, read=None),
                ) as r:
                    if r.status_code >= 400:
                        raise httpx.HTTPStatusError(
                            f"{r.status_code}", request=r.request, response=r
                        )
                    attempt = 0
                    backfill = 0
                    event_name = "message"
                    async for line in r.aiter_lines():
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                            continue
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw:
                            continue
                        try:
                            data = json.loads(raw)
                        except ValueError:
                            continue
                        if event_name in ("session_ended", "end", "close"):
                            return
                        if isinstance(data, dict):
                            yield data
                        event_name = "message"
                    return  # el servidor cerró: la sesión terminó
            except (TimeoutError, httpx.HTTPError) as exc:
                if attempt >= len(SSE_BACKOFF):
                    log.warning("sse %s: agotados los reintentos (%s)", session_id, exc)
                    return
                await asyncio.sleep(SSE_BACKOFF[attempt])
                attempt += 1

    async def signal(self, session_id: str, payload: dict) -> str | None:
        """`POST /signals` a `session.<id>`. Un reintento. Devuelve `signal_id`."""
        body = {"key": f"session.{session_id}", "payload": payload}
        for _ in range(2):
            try:
                r = await self._client.post("/signals", json=body, timeout=SIGNAL_S)
                if r.status_code < 400:
                    return str(r.json().get("signal_id") or "")
                log.warning("signal %s: %s %s", session_id, r.status_code, r.text[:200])
            except (TimeoutError, httpx.HTTPError, ValueError) as exc:
                log.warning("signal %s: %s", session_id, exc)
        return None


# --- Estado por llamada y el monitor -------------------------------------------


@dataclass
class CallState:
    session_id: str
    run_id: str
    call_id: str = ""
    started_t: float = field(default_factory=time.time)
    thread_id: str | None = None
    turn_epoch: int = 0
    transcript: list[dict[str, str]] = field(default_factory=list)
    last_emotions: list[dict[str, Any]] = field(default_factory=list)
    last_coach: dict | None = None
    last_coach_t: float = 0.0
    foresee_inflight: bool = False
    seen_tool_hashes: dict[str, dict] = field(default_factory=dict)
    last_assistant_t: float = 0.0
    last_user_t: float = 0.0
    silence_coached: bool = False
    tool_facts_keys: set[str] = field(default_factory=set)
    ended: bool = False
    tool_pending: bool = (
        False  # el ack del tool está esperando; el plan va dentro del ack
    )
    plan_message: str | None = None  # último mensaje de plan aún no dicho por el ack

    def __post_init__(self) -> None:
        self.call_id = self.call_id or self.session_id


def draft_for(key: str, payload: dict) -> str:
    """El borrador que Humalike refina. Corto, concreto, en la voz del operador."""
    if key == "unit_dispatched":
        unit = payload.get("unit", "una unidad")
        route = payload.get("route", "la ruta alternativa")
        eta = int(payload.get("eta_s") or 0)
        m, s = divmod(eta, 60)
        when = f"{m} min {s} s" if m else f"{s} segundos"
        return f"Ya va {unit} por {route}, llega en {when}. No se mueva de donde está."
    if key == "coach":
        return str(payload.get("say") or NEUTRAL_DRAFT)
    return str(payload.get("message") or payload.get("say") or NEUTRAL_DRAFT)


def dominant(emotions: list[dict[str, Any]]) -> tuple[str, float]:
    if not emotions:
        return ("", 0.0)
    e = max(emotions, key=lambda x: float(x.get("intensity", 0)))
    return (str(e.get("type", "")), float(e.get("intensity", 0)))


CALM_SLOW = {
    "fear",
    "panic",
    "anxiety",
    "miedo",
    "panico",
    "ansiedad",
    "temor",
    "angustia",
    "nervios",
}
FIRM = {
    "anger",
    "frustration",
    "irritation",
    "enfado",
    "frustracion",
    "ira",
    "irritacion",
    "rabia",
}
WARM = {
    "sadness",
    "grief",
    "resignation",
    "dependence",
    "tristeza",
    "resignacion",
    "dependencia",
    "desamparo",
    "soledad",
}


def _plain(kind: str) -> str:
    import unicodedata

    k = unicodedata.normalize("NFKD", kind.lower())
    return "".join(c for c in k if not unicodedata.combining(c)).strip()


def tone_for(emotions: list[dict[str, Any]]) -> tuple[str, str]:
    """Emoción dominante → (tono, ritmo) para la señal `coach`. Humalike devuelve los
    nombres en español o en inglés según le dé; se aceptan los dos."""
    kind, intensity = dominant(emotions)
    k = _plain(kind)
    if intensity >= 0.5:
        if k in CALM_SLOW:
            return ("calm", "slow")
        if k in FIRM:
            return ("firm", "normal")
        if k in WARM:
            return ("warm", "slow")
    return ("calm", "normal")


class ConversationMonitor:
    """Uno por llamada entrante. Consume el SSE de HappyRobot, alimenta a Humalike y
    devuelve señales al agente. Nunca bloquea el camino del replan."""

    def __init__(
        self,
        state: CallState,
        hl: HumalikeClient,
        hr: HappyRobotLive,
        agent_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.state = state
        self.hl = hl
        self.hr = hr
        self.prompt = agent_prompt
        self._task: asyncio.Task[None] | None = None
        self._bg: set[asyncio.Task[None]] = set()
        self._plan_event = asyncio.Event()

    # -- ciclo de vida --

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def run(self) -> None:
        self._spawn(self._silence_watchdog())
        opened = await self.hl.open_thread()
        if opened:
            self.state.thread_id = opened[0]
        async for msg in self.hr.stream(self.state.session_id):
            role = str(msg.get("role") or msg.get("speaker") or "user").lower()
            text = str(msg.get("content") or msg.get("text") or "").strip()
            if text:
                await self.on_message(role, text)
        # el SSE cerró: la llamada terminó por el lado de HappyRobot

    async def close(self) -> dict | None:
        """Al colgar: `analyze`. Devuelve el informe o None."""
        self.state.ended = True
        if self._task and not self._task.done():
            self._task.cancel()
        for t in list(self._bg):
            t.cancel()
        return await self.hl.analyze(self.state.transcript, AGENT_NAME)

    # -- transcripción --

    def _speaker(self, role: str) -> str:
        return AGENT_NAME if role in ("assistant", "agent", AGENT_NAME) else CALLER_NAME

    def add_turn(self, role: str, text: str) -> None:
        speaker = self._speaker(role)
        self.state.transcript.append({"speaker": speaker, "text": text})
        now = time.time()
        self.state.silence_coached = False
        if speaker == AGENT_NAME:
            self.state.last_assistant_t = now
        else:
            self.state.last_user_t = now

    def transcript_turns(self) -> list[dict[str, str]]:
        return list(self.state.transcript)

    def transcript_text(self) -> str:
        return "\n".join(f"{t['speaker']}: {t['text']}" for t in self.state.transcript)

    async def on_message(self, role: str, text: str) -> None:
        self.add_turn(role, text)
        speaker = self._speaker(role)
        await publish(
            make_event(
                EventType.CALL_TRANSCRIPT_PARTIAL,
                {"call_id": self.state.call_id, "speaker": speaker, "text": text},
                source="voice",
            )
        )
        self._spawn(self.hl.ingest(f"run:{self.state.run_id}", speaker, text))
        if speaker == CALLER_NAME:
            decision = await self._turn(text)
            self._spawn(self._foresee_bg(decision))

    def _spawn(self, coro: Any) -> None:
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    # -- Humalike en tiempo real --

    async def _turn(self, text: str) -> TurnDecision | None:
        if not self.state.thread_id:
            return None
        dec = await self.hl.submit_messages(
            self.state.thread_id, CALLER_NAME, text, self.prompt
        )
        if dec:
            self.state.turn_epoch = dec.turn_epoch
        return dec

    async def _foresee_bg(self, decision: TurnDecision | None) -> None:
        if self.state.foresee_inflight:
            return
        self.state.foresee_inflight = True
        try:
            res = await self.hl.foresee(
                self.transcript_turns(),
                NEUTRAL_DRAFT,
                subject_name=CALLER_NAME,
                timeout=FORESEE_BG_S,
            )
        finally:
            self.state.foresee_inflight = False
        if res is None:
            return
        await self.publish_affect(res)
        await self._coach(decision, res.emotions)

    async def publish_affect(self, res: ForeseeResult) -> None:
        await publish(
            make_event(
                EventType.CALL_AFFECT,
                {
                    "call_id": self.state.call_id,
                    "emotions": res.emotions,
                    "risk": res.risk,
                },
                source="voice",
            )
        )

    async def _silence_watchdog(self) -> None:
        """Cada segundo: si nadie ha hablado en SILENCE_S, un coach `acknowledge`."""
        while not self.state.ended:
            await asyncio.sleep(1.0)
            await self.check_silence()

    async def check_silence(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        last = max(self.state.last_user_t, self.state.last_assistant_t)
        if not last or self.state.silence_coached or now - last < SILENCE_S:
            return False
        self.state.silence_coached = True
        await self._coach(None, self.state.last_emotions, silence=True, now=now)
        return True

    async def _coach(
        self,
        decision: TurnDecision | None,
        emotions: list[dict[str, Any]],
        silence: bool = False,
        now: float | None = None,
    ) -> None:
        """Máximo una señal por turno, separadas ≥3 s, solo si algo cambió."""
        now = time.time() if now is None else now
        if now - self.state.last_coach_t < COACH_MIN_GAP_S:
            return
        tags = set(decision.tags) if decision else set()
        if silence:
            tags.add("long_silence")
        prev_kind, prev_int = dominant(self.state.last_emotions)
        kind, intensity = dominant(emotions)
        shifted = kind != prev_kind or abs(intensity - prev_int) >= EMOTION_DELTA
        self.state.last_emotions = emotions
        tone, pace = tone_for(emotions)
        payload: dict[str, Any] | None = None
        if "long_silence" in tags or any("silence" in t for t in tags):
            payload = {
                "action": "acknowledge",
                "say": "Sigo aquí, tómese su tiempo.",
                "reason": "long_silence",
            }
        elif (
            decision
            and decision.decision == "stay_silent"
            and now - self.state.last_assistant_t > 0.4
        ):
            payload = {"action": "hold", "say": None, "reason": "stay_silent"}
        elif shifted and kind:
            payload = {"action": "speak", "say": None, "reason": "emotion_shift"}
        if payload is None:
            return
        payload = {"kind": "coach", "tone": tone, "pace": pace, **payload}
        if payload == self.state.last_coach:
            return
        self.state.last_coach = payload
        self.state.last_coach_t = now
        await self._send(payload, key="coach")

    async def _send(
        self,
        payload: dict,
        key: str,
        causes: list[int] | None = None,
        t0: float | None = None,
        refined: bool | None = None,
    ) -> str | None:
        sid = await self.hr.signal(self.state.session_id, payload)
        if sid is not None:
            latency = (time.perf_counter() - t0) * 1000 if t0 is not None else None
            await publish(
                make_event(
                    EventType.CALL_SIGNAL_SENT,
                    {
                        "call_id": self.state.call_id,
                        "key": key,
                        "signal_id": sid or "-",
                        "message": payload.get("message") or payload.get("say"),
                        "latency_ms": latency,
                        "refined": refined,
                    },
                    source="voice",
                    causes=causes,
                )
            )
            if latency is not None:
                log.info(
                    "signal %s → %s en %.0f ms (%s)",
                    key,
                    self.state.session_id,
                    latency,
                    "refinada por Humalike" if refined else "borrador",
                )
        return sid

    async def on_signal_requested(
        self, key: str, payload: dict, causes: list[int]
    ) -> None:
        """El core quiere que el agente diga algo: borrador → `foresee` (1,5 s) →
        signal. Si Humalike no llega, va el borrador."""
        t0 = time.perf_counter()
        draft = draft_for(key, payload)
        message = draft
        try:
            res = await asyncio.wait_for(
                self.hl.foresee(self.transcript_turns(), draft, subject_name=CALLER_NAME),
                FORESEE_HOT_S,
            )
        except TimeoutError:
            res = None
        if res is not None:
            message = res.refined_reply
            await self.publish_affect(res)
        if key == "unit_dispatched" and self.state.tool_pending:
            # El tool sigue esperando: la noticia va dentro del ack, no por signal.
            self.state.plan_message = message
            self._plan_event.set()
            log.info("plan de %s entregado por el ack del tool", self.state.session_id)
            return
        body = {**payload, "kind": key, "message": message}
        await self._send(body, key=key, causes=causes, t0=t0, refined=res is not None)

    # -- el tool report_fact --

    def tool_hash(self, params: dict) -> str:
        raw = self.state.session_id + json.dumps(
            params, sort_keys=True, ensure_ascii=False
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    async def refine_ack(self, draft: str) -> tuple[str, ForeseeResult | None]:
        """El ack del tool, refinado si Humalike llega a tiempo (FORESEE_HOT_S)."""
        try:
            res = await asyncio.wait_for(
                self.hl.foresee(self.transcript_turns(), draft, subject_name=CALLER_NAME),
                FORESEE_HOT_S,
            )
        except TimeoutError:
            return draft, None
        if res is None:
            return draft, None
        await self.publish_affect(res)
        return res.refined_reply, res

    async def ack_with_plan(self, draft: str) -> tuple[str, ForeseeResult | None, bool]:
        """Refina el ack y, en paralelo, espera hasta PLAN_WAIT_S a que el core
        replanifique. Si el plan llega, se dice en la misma frase. Devuelve
        (mensaje, resultado de foresee, plan incluido)."""
        self.state.tool_pending = True
        self.state.plan_message = None
        self._plan_event.clear()

        async def wait_plan() -> bool:
            try:
                await asyncio.wait_for(self._plan_event.wait(), PLAN_WAIT_S)
                return True
            except TimeoutError:
                return False

        try:
            (message, res), got_plan = await asyncio.gather(
                self.refine_ack(draft), wait_plan()
            )
        finally:
            self.state.tool_pending = False
        if got_plan and self.state.plan_message:
            message = f"{message} {self.state.plan_message}"
            self.state.plan_message = None
        return message, res, got_plan


# --- Registro de monitores y el despachador de señales -------------------------

MONITORS: dict[str, ConversationMonitor] = {}
_hl: HumalikeClient | None = None
_hr: HappyRobotLive | None = None
_autostart = True


def configure(
    hl: HumalikeClient | None = None,
    hr: HappyRobotLive | None = None,
    autostart: bool = True,
) -> None:
    """Inyección para tests y `--mock-calls`: `FakeHumalike` y `FakeLive` de
    `voice.fake` cumplen la misma superficie."""
    global _hl, _hr, _autostart
    _hl = hl
    _hr = hr
    _autostart = autostart


def clients() -> tuple[HumalikeClient, HappyRobotLive]:
    global _hl, _hr
    if _hl is None:
        _hl = HumalikeClient()
    if _hr is None:
        _hr = HappyRobotLive()
    return _hl, _hr


def get_or_start(session_id: str, run_id: str) -> ConversationMonitor:
    mon = MONITORS.get(session_id)
    if mon is None:
        hl, hr = clients()
        mon = ConversationMonitor(CallState(session_id=session_id, run_id=run_id), hl, hr)
        MONITORS[session_id] = mon
        if _autostart:
            mon.start()
    return mon


def forget(session_id: str) -> None:
    MONITORS.pop(session_id, None)


async def signal_dispatcher() -> None:
    """Tarea de fondo: `call.signal.requested` → el monitor de esa llamada."""
    async for ev in subscribe(EventType.CALL_SIGNAL_REQUESTED):
        call_id = str(ev.payload.get("call_id", ""))
        mon = MONITORS.get(call_id)
        if mon is None:
            log.warning("signal para sesión desconocida %s", call_id)
            continue
        await mon.on_signal_requested(
            str(ev.payload.get("key", "")),
            dict(ev.payload.get("payload") or {}),
            [ev.seq],
        )


# --- Fin de llamada ------------------------------------------------------------

_seen_calls: set[str] = set()

_OUTCOME: dict[str, CallOutcome] = {
    "completed": "answered",
    "answered": "answered",
    "missed": "no_answer",
    "voicemail": "no_answer",
    "no_answer": "no_answer",
    "busy": "busy",
    "failed": "failed",
    "canceled": "failed",
    "cancelled": "failed",
    "hung_up": "hung_up",
}


def is_duplicate(call_id: str) -> bool:
    """Webhook duplicado (pasa): descartad por `call_id` ya visto. Idempotencia
    obligatoria."""
    if call_id in _seen_calls:
        return True
    _seen_calls.add(call_id)
    return False


def parse_webhook(body: dict) -> CallResult:
    """El cuerpo del webhook de fin de llamada → `CallResult`, sin extraer aún.

    El nodo Webhook del workflow se configura con este cuerpo:
    `{"type":"end","session_id":..,"run_id":..,"task_id":..,"direction":..,
      "status":..,"transcript":..,"started_at":..,"ended_at":..,"audio_url":..}`.
    Se aceptan también las claves anidadas de `call.*` por si llega el formato
    de los outbound webhooks de la plataforma."""
    call = body.get("call") if isinstance(body.get("call"), dict) else {}
    meta = (call.get("metadata") or {}).get("custom") if call else None
    meta = meta if isinstance(meta, dict) else {}
    call_id = str(body.get("session_id") or body.get("call_id") or call.get("id") or "")
    task_id = body.get("task_id") or meta.get("task_id")
    direction = str(body.get("direction") or call.get("direction") or "inbound")
    if direction not in ("inbound", "outbound"):
        direction = "inbound"
    status = str(body.get("status") or call.get("status") or "completed").lower()
    transcript = body.get("transcript") or call.get("transcript") or ""
    if isinstance(transcript, list):
        transcript = "\n".join(
            f"{m.get('role') or m.get('speaker') or '?'}: {m.get('content') or m.get('text') or ''}"
            for m in transcript
            if isinstance(m, dict)
        )
    now = time.time()
    started = _as_epoch(body.get("started_at") or call.get("started_at")) or now
    ended = _as_epoch(body.get("ended_at") or call.get("ended_at")) or now
    return CallResult(
        call_id=call_id,
        task_id=str(task_id) if task_id else None,
        direction=direction,  # type: ignore[arg-type]
        started_t=started,
        ended_t=ended,
        outcome=_OUTCOME.get(status, "answered"),
        transcript=str(transcript),
        facts=None,
        audio_url=body.get("audio_url") or call.get("recording_url"),
    )


def _as_epoch(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


# --- CLI: probar el token sin montar nada -------------------------------------


async def _cli(args: argparse.Namespace) -> int:
    hl = HumalikeClient()
    try:
        if args.foresee:
            res = await hl.foresee(
                [{"speaker": CALLER_NAME, "text": args.foresee}],
                NEUTRAL_DRAFT,
                subject_name=CALLER_NAME,
                timeout=FORESEE_BG_S,
            )
            if res is None:
                print("foresee → None (token, red o 402; mira el log)")
                return 1
            print("refined_reply:", res.refined_reply)
            print("emotions:", res.emotions)
            print("risk:", res.risk)
        if args.analyze:
            turns = [
                {"speaker": CALLER_NAME, "text": args.analyze},
                {"speaker": AGENT_NAME, "text": NEUTRAL_DRAFT},
            ]
            print(json.dumps(await hl.analyze(turns), ensure_ascii=False, indent=2))
    finally:
        await hl.aclose()
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="Prueba rápida del token de Humalike")
    p.add_argument("--foresee", metavar="TEXTO", help="una frase del vecino")
    p.add_argument("--analyze", metavar="TEXTO", help="una frase, se audita el par")
    raise SystemExit(asyncio.run(_cli(p.parse_args())))


if __name__ == "__main__":
    main()
