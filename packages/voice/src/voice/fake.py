"""El mock de telefonía y de comportamiento. Lo escribe P3 el viernes.

Tres piezas con la misma superficie que las reales, para que P2 y P4 no noten la
diferencia y para el `--mock-calls` del plan B:

- `FakeVoice`: acepta `CallRequest`, espera 8 s y publica un `CallResult` con una
  transcripción de fichero (`fixtures/transcripts/<intent>.txt`).
- `FakeHumalike`: `foresee` devuelve el borrador y miedo 0,8 en 300 ms; `analyze`
  un informe enlatado; el resto no hace nada.
- `FakeLive`: reproduce `fixtures/transcripts/citizen_report.jsonl` como si fuera
  el SSE de HappyRobot; una línea con `tool` hace el POST real a
  `/webhooks/happyrobot/fact`, así el endpoint, el bus y el core corren igual
  que en directo. `signal` solo loguea.

Probadlo de verdad: el domingo puede ser lo único que suene.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from contracts.bus import make_event, publish
from contracts.calls import CallFacts, CallRequest, CallResult
from contracts.events import EventType
from contracts.settings import settings

log = logging.getLogger("voice.fake")

FAKE_DELAY_S = 8.0
TRANSCRIPTS_DIR = Path("fixtures/transcripts")
CITIZEN_SCRIPT = "citizen_report.jsonl"
FACT_URL = "http://localhost:8000/webhooks/happyrobot/fact"


class FakeVoice:
    """Misma superficie que `VoiceGateway`. P2 y P4 no notan la diferencia."""

    def __init__(
        self, transcripts_dir: Path = TRANSCRIPTS_DIR, delay_s: float = FAKE_DELAY_S
    ) -> None:
        self.dir = Path(transcripts_dir)
        self.delay_s = delay_s

    async def place_call(self, req: CallRequest) -> str:
        """Devuelve un `call_id` sintético al instante y publica `call.ended` a
        los 8 segundos."""
        call_id = f"fake_{uuid.uuid4().hex[:8]}"
        await publish(
            make_event(
                EventType.CALL_STARTED,
                {
                    "call_id": call_id,
                    "task_id": req.task_id,
                    "to": req.to,
                    "direction": "outbound",
                },
                source="voice",
            )
        )
        asyncio.create_task(self._finish(call_id, req))
        return call_id

    async def _finish(self, call_id: str, req: CallRequest) -> None:
        await asyncio.sleep(self.delay_s)
        result = self.canned_result(req).model_copy(update={"call_id": call_id})
        ended = make_event(
            EventType.CALL_ENDED, result.model_dump(mode="json"), source="voice"
        )
        await publish(ended)
        await self._assert_facts(call_id, result.facts, ended)

    async def _assert_facts(
        self, call_id: str, facts: CallFacts | None, cause
    ) -> None:
        """Los hechos de la llamada enlatada al bus, igual que los publicaría el
        webhook en directo.

        Sin esto `--mock-calls` no asertaba **ni un solo hecho**: el plan B corría sin
        confirmación de evacuación, sin inmóviles y por tanto sin rescate ni ambulancia.
        Con las evacuaciones a pie eso además deja a los pueblos sin cerrar y
        `civilians_safe` a cero, así que el plan B dejaba de puntuar."""
        if facts is None:
            return
        from contracts.events import FactAsserted
        from voice import to_facts

        for fact in to_facts(facts, cause.t_sim, call_id):
            await publish(
                make_event(
                    EventType.WORLD_FACT_ASSERTED,
                    FactAsserted(
                        key=fact.key,
                        value=fact.value,
                        confidence=fact.confidence,
                        source=fact.source,
                        severity=fact.severity,
                        kind=fact.kind,
                        call_id=fact.call_id,
                    ).model_dump(mode="json"),
                    source=f"call:{call_id}",
                    causes=[cause.seq],
                )
            )

    def canned_facts(self, req: CallRequest) -> CallFacts | None:
        """Los hechos enlatados de `<intent>.facts.json`, anclados al POI **de la
        petición**.

        En una saliente el POI no se adivina del texto: lo sabe el core y viaja en el
        `CallRequest`. Resolverlo por *fuzzy match* sobre la transcripción ataba la
        confirmación de evacuación al pueblo equivocado.

        Y `confirmed_order` solo cuenta en la orden de evacuación. El campo es
        «si acepta la instrucción dada», y la instrucción no es la misma en cada
        guion: al vecino se le pregunta si puede acoger gente, al retén si pueden
        salir. Dejarlo pasar cerraba la evacuación de un pueblo al que solo se había
        avisado."""
        path = self._fixture(req, "facts.json")
        if path is None:
            return None
        cf = CallFacts.model_validate(json.loads(path.read_text(encoding="utf-8")))
        cambios: dict[str, object] = {}
        if req.poi_id:
            cambios["resolved_poi_id"] = req.poi_id
        if req.facts.get("role") != "evacuation":
            cambios["confirmed_order"] = None
        return cf.model_copy(update=cambios) if cambios else cf

    def _fixture(self, req: CallRequest, suffix: str) -> Path | None:
        """El fichero del guion: por `role` si existe uno suyo, y si no por `intent`.

        Un solo `intent` atiende varios encargos —`ambulance_dispatch` es la ambulancia
        que va y la que está en cola—, así que indexar solo por intent hacía que una
        llamada «no queda ninguna libre» se simulara con el guion que dice «salimos del
        hospital ahora mismo»."""
        role = str(req.facts.get("role") or "")
        for nombre in (role, req.intent):
            if nombre and (path := self.dir / f"{nombre}.{suffix}").exists():
                return path
        return None

    def canned_result(self, req: CallRequest) -> CallResult:
        """La transcripción de fichero que corresponde a ese `intent`."""
        path = self._fixture(req, "txt")
        transcript = (
            path.read_text(encoding="utf-8")
            if path is not None
            else f"operador: {req.facts.get('poi_name', req.poi_id)}, orden de evacuación.\nvecino: entendido, salimos ya."
        )
        now = time.time()
        return CallResult(
            call_id="fake",
            task_id=req.task_id,
            direction="outbound",
            started_t=now - self.delay_s,
            ended_t=now,
            outcome="answered",
            transcript=transcript,
            facts=self.canned_facts(req),
        )

    async def signal(self, call_id: str, key: str, payload: dict) -> str | None:
        log.info("fake signal %s %s %s", call_id, key, payload)
        return call_id


class FakeHumalike:
    """Misma superficie que `HumalikeClient`, sin red."""

    def __init__(
        self, latency_s: float = 0.3, emotions: list[dict[str, Any]] | None = None
    ) -> None:
        self.latency_s = latency_s
        self.emotions = emotions or [{"type": "fear", "intensity": 0.8}]
        self.calls: list[tuple[str, dict]] = []

    async def foresee(
        self,
        transcript,
        candidate_reply,
        agent_name="operador",
        system_prompt="",
        subject_name=None,
        timeout=1.5,
    ):
        from voice.humanlike import ForeseeResult

        self.calls.append(("foresee", {"draft": candidate_reply}))
        await asyncio.sleep(min(self.latency_s, timeout))
        return ForeseeResult(
            refined_reply=candidate_reply,
            emotions=list(self.emotions),
            risk=0.2,
            raw={"fake": True},
        )

    async def analyze(self, transcript, agent_name="operador", focus=None, timeout=10.0):
        self.calls.append(("analyze", {"turns": len(transcript)}))
        if not transcript:
            return None
        return {
            "health_score": 0.82,
            "summary": "El vecino se sintió atendido; el operador confirmó la ubicación a la primera.",
            "findings": [],
            "fake": True,
        }

    async def ingest(self, scope_id, speaker, text):
        self.calls.append(("ingest", {"scope": scope_id}))

    async def recall(self, scope_id, speaker, text):
        return ""

    async def open_thread(self, thread_id=None, social_signals=True):
        return (thread_id or f"thr_{uuid.uuid4().hex[:6]}", "")

    async def submit_messages(self, thread_id, sender, content, system_prompt=""):
        from voice.humanlike import TurnDecision

        self.calls.append(("submit", {"content": content}))
        return TurnDecision(decision="speak", turn_epoch=len(self.calls), tags=[])

    async def respond(self, thread_id, content, turn_epoch, system_prompt=""):
        return [{"content": content, "position": 0, "deliver_at": None}]

    async def aclose(self) -> None:
        return None


_NUMBERS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4}


class FakeJev:
    """Misma superficie que `JevClient`, sin red. Lee solo los turnos del vecino con
    reglas de texto (no es Jev: es lo justo para que el bucle, el presupuesto y los
    hechos corran de punta a punta en `--mock-calls` y en los tests).

    `script`, si se da, manda: una lista de `dict[campo → (valor, confianza)]`, una
    por tick, para escribir tests deterministas de la conversación."""

    def __init__(self, script: list[dict[str, tuple[str, float]]] | None = None) -> None:
        self.script = script
        self.ticks = 0
        self.enabled = True
        self.failed_reason: str | None = None

    async def aclose(self) -> None:
        return None

    async def tick(self, state: dict, questions: dict):
        from voice import pois
        from voice.jev import Answer, Perception

        idx = self.ticks
        self.ticks += 1
        if self.script is not None:
            got = self.script[min(idx, len(self.script) - 1)]
            return Perception(
                answers={k: Answer(v, c) for k, (v, c) in got.items()}, latency_ms=100.0
            )
        text = " ".join(
            t["text"] for t in state.get("transcript", []) if t.get("speaker") == "caller"
        )
        norm = pois.normalize(text)
        poi = pois.resolve_poi_local(text) if norm else None
        cut = any(w in norm for w in ("cortad", "arbol", "bloquead", "impracticable"))
        edge = pois.resolve_edge_local(text) if cut else None
        m = re.search(r"(\w+) personas? (?:que )?no pued", norm)
        n = _NUMBERS.get(m.group(1)) if m else None
        if any(w in norm for w in ("no pueden", "no puede", "atrapad", "herid")):
            urgency = "critical"
        elif cut or "humo" in norm:
            urgency = "medium"
        else:
            urgency = "low"
        answers = {
            "location_hint": Answer(poi or "not_stated", 0.9),
            "road_blocked": Answer(edge or "not_stated", 0.93 if edge else 0.9),
            "people_immobile": Answer(str(n) if n else "not_stated", 0.9),
            "urgency": Answer(urgency, 0.9),
            "contradicts_known": Answer(False, 0.9),
        }
        return Perception(answers=answers, latency_ms=100.0)


class FakeLive:
    """Misma superficie que `HappyRobotLive`. Reproduce un guion en vez del SSE.

    Guion (`fixtures/transcripts/citizen_report.jsonl`), una línea por turno:
    `{"role": "user"|"assistant", "text": "...", "delay_s": 1.5,
      "tool": {"location_hint": "...", "road_blocked": "...", ...}}`
    Cuando una línea trae `tool`, se hace el POST real al endpoint del tool."""

    def __init__(
        self,
        script: Path = TRANSCRIPTS_DIR / CITIZEN_SCRIPT,
        fact_url: str = FACT_URL,
        speed: float = 1.0,
        post_tools: bool = True,
    ) -> None:
        self.script = Path(script)
        self.fact_url = fact_url
        self.speed = speed
        self.post_tools = post_tools
        self.signals: list[dict] = []

    def _lines(self) -> list[dict]:
        if not self.script.exists():
            log.warning("no hay guion en %s", self.script)
            return []
        out = []
        for raw in self.script.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw and not raw.startswith("#"):
                out.append(json.loads(raw))
        return out

    async def stream(self, session_id: str, backfill: int = 0) -> AsyncIterator[dict]:
        for line in self._lines():
            await asyncio.sleep(float(line.get("delay_s", 1.0)) / max(self.speed, 0.01))
            yield {"role": line.get("role", "user"), "content": line.get("text", "")}
            tool = line.get("tool")
            if tool and self.post_tools:
                await self._post_tool(session_id, tool)

    async def _post_tool(self, session_id: str, params: dict) -> None:
        body = {"session_id": session_id, "params": params}
        headers = {"X-Vela-Token": settings.webhook_shared_token}
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.post(self.fact_url, json=body, headers=headers)
                log.info("fake tool → %s %s", r.status_code, r.text[:160])
        except httpx.HTTPError as exc:
            log.warning("fake tool: %s", exc)

    async def signal(self, session_id: str, payload: dict) -> str | None:
        self.signals.append(payload)
        log.info("fake signal → %s: %s", session_id, payload.get("message") or payload)
        return f"sig_fake_{len(self.signals)}"

    async def aclose(self) -> None:
        return None


def install_fakes(
    script: Path | None = None, speed: float = 1.0, post_tools: bool = True
) -> tuple[FakeHumalike, FakeLive]:
    """`--mock-calls`: inyecta los fakes en `voice.humanlike`. Devuelve ambos para
    que el guion de la demo pueda mirar `FakeLive.signals`."""
    from voice import humanlike

    hl = FakeHumalike()
    live = FakeLive(
        script or TRANSCRIPTS_DIR / CITIZEN_SCRIPT, speed=speed, post_tools=post_tools
    )
    humanlike.configure(hl=hl, hr=live)  # type: ignore[arg-type]
    return hl, live
