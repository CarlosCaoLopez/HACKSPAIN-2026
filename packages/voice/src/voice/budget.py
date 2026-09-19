"""Umbrales y presupuesto de completitud. Código puro: sin red, sin reloj propio.

Qué decide, tick a tick, con lo que Jev ha resuelto:

- campo por encima de su umbral → se aserta como `observed`;
- por debajo y con presupuesto → el agente pregunta **una** cosa, la que más importa;
- por debajo y sin presupuesto → se rellena como `assumed_default` (regla 4: el hecho
  asumido nunca se disfraza de observado, y la restricción se sostiene en la dirección
  segura: la pista está cortada hasta que se confirme transitable, nunca al revés).

El reloj lo marca la gravedad: `urgency` es lo primero que se resuelve y de ella sale
cuánto tiempo hay. No se retiene una ambulancia mientras se completa un cuestionario.

Los valores salen del sábado por la tarde corriendo el catálogo sobre
`fixtures/transcripts/`, no del papel. Empiezan conservadores.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contracts.calls import Fact, FactKind, Severity
from contracts.events import FieldStatus
from contracts.factkeys import road_cut_key
from contracts.questions import NOT_STATED

THRESHOLDS: dict[str, float] = {
    "assert_soft_fact": 0.55,  # crear una tarea rescue: recuperable
    "assert_hard_fact": 0.85,  # marcar una arista cut: cambia rutas de civiles
    "ask_followup": 0.55,  # por debajo, el agente pregunta si hay presupuesto
}

COMPLETION_BUDGET_S: dict[str, float] = {"critical": 8.0, "medium": 25.0, "low": 60.0}
"""El backbone habla de critical / high / medium; el contrato tiene critical / medium /
low. El mapa es 1:1 por orden: `medium` es el `high` del backbone (25 s) y `low` su
`medium` (60 s)."""

ASSUMED_CONFIDENCE = 0.3

TRACKED: tuple[str, ...] = (
    "road_blocked",
    "people_immobile",
    "injuries",
    "location_hint",
    "urgency",
)
"""Campos que el presupuesto persigue, en orden de prioridad de pregunta: lo que cambia
rutas primero, luego a quién rescatar, luego dónde. `urgency` no se pregunta: se mide.
`injuries` se observa pero no se pregunta ni se asume (no está en `ASK_PRIORITY`): el
operador ya lo pide y un herido nunca se da por supuesto."""

ASK_PRIORITY: tuple[str, ...] = ("road_blocked", "people_immobile", "location_hint")

FOLLOWUP_DRAFT: dict[str, str] = {
    "road_blocked": "¿Qué tramo de carretera está cortado? ¿Por dónde no se puede pasar?",
    "people_immobile": "¿Hay alguien que no pueda moverse por su pie? ¿Cuántas personas?",
    "location_hint": "¿Desde dónde me llama exactamente? Dígame el pueblo o un punto conocido.",
}


def threshold_for(field_key: str) -> float:
    """Cortar una arista cambia las rutas de los civiles: pide la barra alta."""
    return THRESHOLDS[
        "assert_hard_fact" if field_key == "road_blocked" else "assert_soft_fact"
    ]


@dataclass
class FieldState:
    status: FieldStatus = "open"
    value: str | None = None  # el candidato: el observado, o el mejor por ahora
    confidence: float | None = None


@dataclass
class Decision:
    """Lo que el tick pide hacer al monitor. Nada más."""

    newly_observed: list[str] = field(default_factory=list)
    ask: str | None = None
    fill: list[str] = field(default_factory=list)


class Completeness:
    """El estado de completitud de UNA llamada."""

    def __init__(self) -> None:
        self.fields: dict[str, FieldState] = {k: FieldState() for k in TRACKED}
        self.budget_s: float | None = None
        self._started_at: float | None = None

    # -- el tick --

    def update(self, answers: dict[str, tuple[str, float]], now: float) -> Decision:
        """`answers`: campo → (valor elegido, confianza). Una respuesta que llega
        después de un `assumed_default` lo sustituye si supera el umbral: el sistema
        intenta falsar activamente lo que asumió."""
        dec = Decision()
        for key in TRACKED:
            got = answers.get(key)
            if got is None:
                continue
            value, conf = got
            st = self.fields[key]
            stated = value != NOT_STATED
            if stated and conf >= threshold_for(key):
                if st.status != "observed" or st.value != value:
                    dec.newly_observed.append(key)
                self.fields[key] = FieldState("observed", value, conf)
            elif stated and st.status not in ("observed",):
                # medio-confiado: se recuerda como candidato, sigue sin ser un hecho
                keep = "asked" if st.status == "asked" else "open"
                if st.status != "assumed_default":
                    self.fields[key] = FieldState(keep, value, conf)
        self._maybe_start_clock(now)
        dec.ask, dec.fill = self._budget_decision(now)
        return dec

    def _maybe_start_clock(self, now: float) -> None:
        urg = self.fields["urgency"]
        if self.budget_s is None and urg.status == "observed" and urg.value:
            self.budget_s = COMPLETION_BUDGET_S.get(
                urg.value, COMPLETION_BUDGET_S["medium"]
            )
            self._started_at = now

    def elapsed_s(self, now: float) -> float:
        return 0.0 if self._started_at is None else max(0.0, now - self._started_at)

    def _budget_decision(self, now: float) -> tuple[str | None, list[str]]:
        if self.budget_s is None:
            return None, []  # sin urgencia no hay reloj: se espera al siguiente tick
        pending = [k for k in ASK_PRIORITY if self.fields[k].status in ("open", "asked")]
        if not pending:
            return None, []
        if self.elapsed_s(now) >= self.budget_s:
            return None, pending  # el monitor decide el valor y llama a `assume`
        if any(self.fields[k].status == "asked" for k in pending):
            return None, []  # una pregunta a la vez: la anterior sigue viva
        target = pending[0]
        self.fields[target] = FieldState(
            "asked", self.fields[target].value, self.fields[target].confidence
        )
        return target, []

    def assume(self, key: str, value: str) -> None:
        """El relleno del LLM (o del valor seguro): `assumed_default`, nunca `observed`."""
        self.fields[key] = FieldState("assumed_default", value, ASSUMED_CONFIDENCE)

    # -- salida --

    def snapshot(self, call_id: str, now: float) -> dict:
        """El payload de `call.completeness`."""
        return {
            "call_id": call_id,
            "budget_s": self.budget_s,
            "elapsed_s": round(self.elapsed_s(now), 1),
            "fields": [
                {
                    "key": k,
                    "status": st.status,
                    "value": st.value,
                    "confidence": None
                    if st.confidence is None
                    else round(st.confidence, 3),
                }
                for k, st in self.fields.items()
            ],
        }

    def severity(self) -> Severity:
        urg = self.fields["urgency"]
        if urg.status == "observed":
            if urg.value == "low":
                return "low"
            if urg.value == "critical":
                return "critical"
        return "medium"

    def facts(self, call_id: str, t_sim: float) -> list[Fact]:
        """Los hechos que se derivan del estado actual. Puro: el monitor compara con lo
        que ya publicó y emite solo lo nuevo o cambiado.

        Un hecho sin ubicar (`poi:<id>:immobile` sin lugar) no entra al estado: se
        registra y se espera a que `location_hint` llegue."""
        out: list[Fact] = []
        source = f"call:{call_id}"
        sev = self.severity()

        def add(
            key: str, value: str | float | bool, st: FieldState, kind: FactKind
        ) -> None:
            out.append(
                Fact(
                    key=key,
                    value=value,
                    confidence=st.confidence
                    if st.confidence is not None
                    else ASSUMED_CONFIDENCE,
                    source=source,
                    severity=sev,
                    t_sim=t_sim,
                    kind=kind,
                    call_id=call_id,
                )
            )

        road = self.fields["road_blocked"]
        if (
            road.value
            and road.value != NOT_STATED
            and road.status in ("observed", "assumed_default")
        ):
            # Cortada es la dirección segura: un `assumed_default` puede cerrar, jamás abrir.
            add(road_cut_key(road.value), True, road, _kind(road))

        loc, imm = self.fields["location_hint"], self.fields["people_immobile"]
        if (
            loc.value
            and loc.value != NOT_STATED
            and loc.status in ("observed", "assumed_default")
            and imm.value
            and imm.value != NOT_STATED
            and imm.status in ("observed", "assumed_default")
        ):
            count = 5 if imm.value == "5plus" else int(imm.value)
            kind: FactKind = (
                "assumed_default"
                if "assumed_default" in (loc.status, imm.status)
                else "observed"
            )
            add(f"poi:{loc.value}:immobile", count, imm, kind)

        inj = self.fields["injuries"]
        if (
            loc.value
            and loc.value != NOT_STATED
            and loc.status in ("observed", "assumed_default")
            and inj.value
            and inj.value != NOT_STATED
            and inj.status in ("observed", "assumed_default")
        ):
            count = 5 if inj.value == "5plus" else int(inj.value)
            kind = (
                "assumed_default"
                if "assumed_default" in (loc.status, inj.status)
                else "observed"
            )
            add(f"poi:{loc.value}:injuries", count, inj, kind)
        return out


def _kind(st: FieldState) -> FactKind:
    return "assumed_default" if st.status == "assumed_default" else "observed"


def safe_default(key: str, st: FieldState) -> str | None:
    """Lo que se asume si el LLM no contesta: la dirección segura, y nada si no hay
    base. Un candidato medio-confiado gana a cualquier invento.

    `people_immobile` es el único con invento, y se queda: prepararse para alguien que
    quizá no puede moverse es la dirección segura, va marcado `assumed_default` (no
    funda rescate, `tasks._rescue`) y es lo que el dashboard pinta en gris cursiva. Lo
    que NO puede hacer es salir por la boca del agente como si el vecino lo hubiera
    dicho: de eso se encarga `CallPerception.call_facts`, que solo devuelve lo
    observado."""
    if st.value and st.value != NOT_STATED:
        return st.value
    if key == "people_immobile":
        return "1"  # puede haber alguien: se prepara el rescate y se falsa después
    return None  # el resto sin candidato: no se inventa nada
