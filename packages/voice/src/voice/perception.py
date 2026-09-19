"""La percepción de UNA llamada: Jev → completitud → hechos con `kind` → bus.

Un `CallPerception` por llamada. Cada tick (cada 5 s durante la llamada, uno inmediato
cuando el tool `report_fact` avisa, y uno final al colgar):

1. una petición a Jev con todas las preguntas del catálogo;
2. `Completeness.update` decide: asertar, preguntar una cosa, o rellenar;
3. se publican los hechos nuevos (`world.fact.asserted`, con `kind` y `call_id`) y el
   vector de completitud (`call.completeness`), que es lo que pinta el dashboard.

No habla con HappyRobot ni con Humalike: devuelve el campo por el que hay que
preguntar y el monitor de la llamada (`voice.humanlike`) decide cómo decirlo.
Nunca lanza: sin Jev no hay percepción y la llamada sigue (plan B: `--no-jev`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from contracts.bus import current_t_sim, make_event, publish
from contracts.calls import CallFacts, Fact
from contracts.events import EventType
from contracts.questions import NOT_STATED
from voice import gapfill, jev, pois
from voice.budget import TRACKED, Completeness, FieldState, safe_default

log = logging.getLogger("voice.perception")

CONFIRM_THRESHOLD = 0.85
"""Que alguien acepte una orden de evacuación es un hecho que se actúa: barra alta."""


def _count(value: str | None) -> int | None:
    """`5plus` es una cota inferior: se guarda 5, y el `kind` dice cuánto fiarse."""
    if value == "5plus":
        return 5
    return int(value) if value and value.isdigit() else None


class CallPerception:
    def __init__(self, call_id: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.call_id = call_id
        self.completeness = Completeness()
        self._clock = clock
        self._emitted: dict[str, tuple] = {}
        self._fill_tried = False
        self._lock = asyncio.Lock()
        self.confirmed_order: bool | None = None
        self.pinned_poi: str | None = None
        """Un pin GPS (Telegram) anclado a un POI: `location_hint` queda fijado como
        observado y Jev ni pregunta por él ni puede sustituirlo por lo que diga el texto
        («estamos al molino» no gana a un pin que cae en Pueblo B)."""

    @property
    def facts_published(self) -> int:
        return len(self._emitted)

    def pin(self, poi_id: str, confidence: float) -> None:
        """Fija `location_hint` al POI del pin. Manda sobre cualquier tick posterior."""
        self.pinned_poi = poi_id
        self.completeness.fields["location_hint"] = FieldState(
            "observed", poi_id, confidence
        )

    def _hold_pin(self) -> None:
        if (
            self.pinned_poi
            and self.completeness.fields["location_hint"].value != self.pinned_poi
        ):
            self.completeness.fields["location_hint"] = FieldState(
                "observed",
                self.pinned_poi,
                self.completeness.fields["location_hint"].confidence,
            )

    def unlocated_counts(self) -> dict[str, int]:
        """Lo que la llamada contó pero no supo ubicar (`people_immobile` con
        `location_hint` sin resolver): lo que un pin de Telegram puede colocar. Sufijo de
        la clave `poi:<id>:<sufijo>` → valor."""
        f = self.completeness.fields
        loc, imm, inj = f["location_hint"], f["people_immobile"], f["injuries"]
        if loc.value and loc.value != NOT_STATED and loc.status == "observed":
            return {}
        out: dict[str, int] = {}
        if imm.value and imm.value != NOT_STATED and imm.status == "observed":
            count = _count(imm.value)
            if count:
                out["immobile"] = count
        if inj.value and inj.value != NOT_STATED and inj.status == "observed":
            count = _count(inj.value)
            if count:
                out["injuries"] = count
        return out

    @property
    def active(self) -> bool:
        """Hay Jev, hay escenario y no se ha forzado `--no-jev`."""
        return jev.get_jev().enabled and bool(pois.pois())

    async def tick(
        self,
        turns: list[dict[str, str]],
        *,
        new_text: bool = True,
        final: bool = False,
        wait: bool = False,
    ) -> str | None:
        """Devuelve el campo por el que preguntar, o None. Sin `new_text` no hay red:
        solo se hace avanzar el reloj del presupuesto."""
        if not self.active or (self._lock.locked() and not (final or wait)):
            return None  # un tick en vuelo basta; el final y el tool esperan su turno
        async with self._lock:
            answers: dict[str, tuple[str, float]] = {}
            if new_text and turns:
                answers = await self._ask_jev(turns)
            if self.pinned_poi:
                answers.pop("location_hint", None)  # el pin manda sobre el texto
            dec = self.completeness.update(answers, self._clock())
            self._hold_pin()
            if dec.fill and not final and not self._fill_tried:
                await self._fill(dec.fill, turns)
            await self._publish()
            return None if final else dec.ask

    async def _ask_jev(self, turns: list[dict[str, str]]) -> dict[str, tuple[str, float]]:
        questions = pois.questions()
        # `contradicts_known` compara contra los hechos vigentes del sistema, que voice
        # no ve (invariante 4): sin ese contexto la pregunta no significa nada.
        questions.pop("contradicts_known", None)
        if self.pinned_poi:
            questions.pop("location_hint", None)  # ya lo dio el pin: no se pregunta
        perc = await jev.get_jev().tick(jev.build_state(turns), questions)
        if perc is None:
            return {}
        conf = perc.get("confirmed_order")
        if conf is not None:
            self.confirmed_order = (
                bool(conf.value) and conf.confidence >= CONFIRM_THRESHOLD
            )
        return {
            k: (str(a.value), a.confidence)
            for k, a in perc.answers.items()
            if k in TRACKED
        }

    async def _fill(self, fields: list[str], turns: list[dict[str, str]]) -> None:
        """Presupuesto agotado: el LLM elige entre las opciones cerradas y, si no
        contesta, el valor seguro. Todo `assumed_default`; sin base, el hueco sigue
        abierto en vez de inventarse."""
        self._fill_tried = True
        text = "\n".join(f"{t['speaker']}: {t['text']}" for t in turns)
        chosen = await gapfill.fill(fields, text, pois.questions())
        for key in fields:
            value = chosen.get(key) or safe_default(key, self.completeness.fields[key])
            if value:
                self.completeness.assume(key, value)

    async def _publish(self) -> None:
        now = self._clock()
        t_sim = current_t_sim()
        for f in [*self.completeness.facts(self.call_id, t_sim), *self._confirmed(t_sim)]:
            sig = (f.value, f.kind)
            if self._emitted.get(f.key) == sig:
                continue
            self._emitted[f.key] = sig
            await publish(
                make_event(
                    EventType.WORLD_FACT_ASSERTED,
                    {
                        "key": f.key,
                        "value": f.value,
                        "confidence": f.confidence,
                        "source": f.source,
                        "severity": f.severity,
                        "kind": f.kind,
                        "call_id": f.call_id,
                    },
                    source=f.source,
                    t_sim=t_sim,
                )
            )
        await publish(
            make_event(
                EventType.CALL_COMPLETENESS,
                self.completeness.snapshot(self.call_id, now),
                source="voice",
            )
        )

    def _confirmed(self, t_sim: float) -> list[Fact]:
        """Aceptar la orden de evacuación: `poi:<id>:confirmed`, si se sabe de qué POI."""
        loc = self.completeness.fields["location_hint"]
        if not self.confirmed_order or not loc.value or loc.value == NOT_STATED:
            return []
        return [
            Fact(
                key=f"poi:{loc.value}:confirmed",
                value=True,
                confidence=CONFIRM_THRESHOLD,
                source=f"call:{self.call_id}",
                severity=self.completeness.severity(),
                t_sim=t_sim,
                kind="assumed_default" if loc.status == "assumed_default" else "observed",
                call_id=self.call_id,
            )
        ]

    def call_facts(self) -> CallFacts:
        """El resultado de la llamada para `call.ended`: solo lo observado o asumido,
        con ids del escenario (nunca texto libre)."""
        f = self.completeness.fields
        loc = f["location_hint"]
        road = f["road_blocked"]
        imm = f["people_immobile"]
        confs = [
            s.confidence for s in f.values() if s.status == "observed" and s.confidence
        ]
        has_poi = loc.value and loc.value != NOT_STATED
        return CallFacts(
            location_hint=pois.poi_name(loc.value) if has_poi else None,
            resolved_poi_id=loc.value if has_poi else None,
            road_blocked=road.value if road.value and road.value != NOT_STATED else None,
            people_immobile=_count(imm.value),
            injuries=_count(f["injuries"].value),
            confirmed_order=self.confirmed_order,
            urgency=self.completeness.severity(),
            confidence=min(confs) if confs else 0.5,
        )
