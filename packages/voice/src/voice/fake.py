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
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from contracts.bus import make_event, publish
from contracts.calls import CallRequest, CallResult
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
        await publish(
            make_event(
                EventType.CALL_ENDED, result.model_dump(mode="json"), source="voice"
            )
        )

    def canned_result(self, req: CallRequest) -> CallResult:
        """La transcripción de fichero que corresponde a ese `intent`."""
        path = self.dir / f"{req.intent}.txt"
        transcript = (
            path.read_text(encoding="utf-8")
            if path.exists()
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
            facts=None,
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
