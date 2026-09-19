"""La telefonía enlatada. P4. **Plan B nivel 2.**

`voice/fake.py` es de P3 y es la base oficial del `--mock-calls` (`docs/interfaces.md`).
Mientras no tenga cuerpo, esto ocupa su sitio: misma superficie (`place_call`), mismos
eventos (`call.started`, `call.transcript.partial`, `call.ended`), mismo `source`
(`call:<id>`). El puente de `bridges.py` no nota la diferencia y `packages/voice/**` no
se toca.

**Qué es enlatado y qué no**, porque la diferencia es justo lo que no se puede mentir:

- La **conversación** es enlatada: sale de `fixtures/transcripts/<intent>.txt`. Eso es
  exactamente lo que el backbone llama reproducir un audio grabado.
- La **extracción** se intenta de verdad: si `VoiceGateway.extract` tiene cuerpo, es la
  suya —fenic, `semantic.extract`— sobre la transcripción enlatada. Solo si no lo tiene
  se cae al `<intent>.facts.json` que acompaña al guion.
- Y **se dice siempre**: el run lleva `calls: "simuladas"` en `/api/health` y la
  cabecera del dashboard lo enseña mientras dure. Una llamada simulada que se presenta
  como real es la clase de mentira que este proyecto existe para no contar.

**Límite conocido**: esto son las llamadas que nacen de un `call.requested` del core:
nosotros llamamos al agente (HappyRobot) y él nos dicta la orden en esa misma llamada.
La del vecino asustado —información del terreno, el clímax de la demo— entra por el
webhook de humalike y es de P3: sin su router no hay forma de simularla desde aquí sin
inventarse un evento con `source` de otro, que es justo lo que `control.py` se niega a
hacer con `world.inject`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from contracts.calls import CallRequest, CallResult
from contracts.events import CallStarted, EventType, FactAsserted, TranscriptPartial

if TYPE_CHECKING:
    from gateway.runtime import Runtime

log = logging.getLogger("vela.voice")

TRANSCRIPTS_DIR = Path("fixtures/transcripts")

PARTIAL_GAP_S = 1.6
"""Lo que se espera entre dos líneas de transcripción. La llamada tiene que PARECER
viva en el panel: si caen las seis líneas de golpe, es un registro, no una llamada."""

SPEAKERS = ("agente", "vecino")
"""Las líneas alternan. El fichero es una línea por turno, empezando por el agente."""


class CannedVoice:
    """La superficie de `VoiceGateway.place_call` sobre transcripciones de fichero."""

    def __init__(
        self,
        rt: Runtime,
        real: object | None = None,
        directory: Path = TRANSCRIPTS_DIR,
    ) -> None:
        self.rt = rt
        # El `VoiceGateway` de P3, si el proceso llegó a construirlo. Se guarda aquí y no
        # se lee de `rt.voice` porque `rt.voice` pasa a ser ESTE objeto: para usar la
        # extracción de verdad hay que quedarse con la referencia antes de sustituirla.
        self.real = real
        self.directory = directory
        self.placed = 0
        self.available = sorted(p.stem for p in directory.glob("*.txt"))
        if not self.available:
            # Ruidoso y a la vista: es el fallo que deja la demo sin llamadas y el que
            # hay que descubrir el sábado, no el domingo.
            rt.notes["voice"] = (
                f"--mock-calls sin transcripciones en {directory}: no hay qué reproducir"
            )
            log.warning("%s", rt.notes["voice"])
        else:
            rt.notes["voice"] = f"simuladas · guiones: {', '.join(self.available)}"

    async def place_call(self, req: CallRequest) -> str:
        """Devuelve el `call_id` al instante; la conversación va en su propia task.

        Igual que la de verdad: nadie espera a una llamada de forma bloqueante, el
        resultado llega por evento.
        """
        self.placed += 1
        call_id = f"mock_{self.placed:04d}"
        script = self._script(req)
        if script is None:
            log.warning("sin guion para intent=%s: la llamada no se simula", req.intent)
            # Y se cierra igual. El core retiene a la unidad de una llamada de
            # despacho hasta que la llamada acaba: un intent sin guion dejaba al
            # camión parado en el parque hasta agotar el plazo, en todos los runs
            # con `--mock-calls`.
            await self.rt.publish(
                EventType.CALL_ENDED,
                CallResult(
                    call_id=call_id,
                    task_id=req.task_id,
                    direction="outbound",
                    started_t=self.rt.hub.last_t_sim,
                    ended_t=self.rt.hub.last_t_sim,
                    outcome="failed",
                    transcript="",
                    facts=None,
                ),
                f"call:{call_id}",
            )
            return call_id
        self.rt.spawn(f"call:{call_id}", self._converse(call_id, req, script))
        return call_id

    # --- el guion -----------------------------------------------------------------

    def _script(self, req: CallRequest) -> list[str] | None:
        """Las líneas de `<intent>.txt`. Sin fichero, no se inventa una conversación."""
        path = self.directory / f"{req.intent}.txt"
        if not path.exists():
            return None
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
        return [ln for ln in lines if ln] or None

    async def _converse(self, call_id: str, req: CallRequest, script: list[str]) -> None:
        started = await self.rt.publish(
            EventType.CALL_STARTED,
            CallStarted(
                call_id=call_id, task_id=req.task_id, to=req.to, direction="outbound"
            ),
            f"call:{call_id}",
        )
        started_t = started.t_sim

        for n, text in enumerate(script):
            await asyncio.sleep(PARTIAL_GAP_S)
            await self.rt.publish(
                EventType.CALL_TRANSCRIPT_PARTIAL,
                TranscriptPartial(
                    call_id=call_id, speaker=SPEAKERS[n % len(SPEAKERS)], text=text
                ),
                f"call:{call_id}",
                causes=[started.seq],
            )

        transcript = "\n".join(script)
        facts = await self._extract(req, transcript)
        ended = await self.rt.publish(
            EventType.CALL_ENDED,
            CallResult(
                call_id=call_id,
                task_id=req.task_id,
                direction="outbound",
                started_t=started_t,
                ended_t=self.rt.hub.last_t_sim,
                outcome="answered",
                transcript=transcript,
                facts=facts,
            ),
            f"call:{call_id}",
            causes=[started.seq],
        )
        await self._assert_facts(call_id, req, facts, ended.seq)

    # --- la extracción ---------------------------------------------------------------

    async def _extract(self, req: CallRequest, transcript: str):
        """La de P3 si existe; si no, la que viene con el guion. Nunca inventada aquí."""
        extract = getattr(self.real, "extract", None)
        if extract is not None:
            try:
                facts = await extract(transcript)
                # `None` NO es una respuesta: `voice.extract` devuelve None cuando fenic
                # falla o no hay clave, y tragárselo dejaba el plan B entero sin un solo
                # `world.fact.asserted` —sin replan, sin rescate y sin ambulancia—, que
                # es justo lo que el enlatado está para cubrir. Medido en
                # `runs/run_b7a7da8a8427.jsonl`: cuatro `call.ended` con `facts=null`.
                if facts is not None:
                    return facts
            except (NotImplementedError, AttributeError):
                pass  # P3 sin cuerpo: se sigue con el enlatado, que es el plan B
            except Exception as exc:  # noqa: BLE001 — una extracción que falla es normal
                log.warning("extract falló: %r · sigo con el enlatado", exc)

        path = self.directory / f"{req.intent}.facts.json"
        if not path.exists():
            # `facts=None` es un caso previsto y el panel ya lo pinta *sin extraer*
            # (REQ-138): mejor eso que unos hechos inventados por mí.
            return None
        from contracts.calls import CallFacts

        return CallFacts.model_validate(json.loads(path.read_text(encoding="utf-8")))

    async def _assert_facts(
        self, call_id: str, req: CallRequest, facts, cause: int
    ) -> None:
        """`CallFacts` → `world.fact.asserted`, con `to_facts` de P3 si tiene cuerpo.

        Sin esta cadena no hay replan y no hay colgar → giro que medir: es el clímax
        entero del plan B nivel 2. Cada hecho sale con su `causes` apuntando al
        `call.ended`, que es lo que deja seguir la cadena en el panel de cambios.
        """
        if facts is None:
            return
        to_facts = getattr(self.real, "to_facts", None)
        if to_facts is None:
            self.rt.notes["voice_facts"] = (
                "voice.to_facts sin cuerpo: la llamada simulada no asierta hechos (P3)"
            )
            return
        try:
            derived = to_facts(facts, self.rt.hub.last_t_sim, call_id)
        except (NotImplementedError, AttributeError):
            self.rt.notes["voice_facts"] = (
                "voice.to_facts sin cuerpo: la llamada simulada no asierta hechos (P3)"
            )
            return

        for fact in derived:
            await self.rt.publish(
                EventType.WORLD_FACT_ASSERTED,
                FactAsserted(
                    key=fact.key,
                    value=fact.value,
                    confidence=fact.confidence,
                    source=fact.source,
                    severity=fact.severity,
                ),
                f"call:{call_id}",
                causes=[cause],
            )
