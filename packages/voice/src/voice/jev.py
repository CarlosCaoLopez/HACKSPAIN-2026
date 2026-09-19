"""Percepción en llamada: TypeSafe `jev-1.13` elige entre las opciones del escenario.

Jev no genera texto. Evalúa preguntas tipadas (`Choice` / `Score` / `Noul`) contra un
estado y devuelve la opción elegida con la distribución y una confianza calibrada.
Como cada `Choice` ofrece solo ids del YAML (más `not_stated`), el modelo no puede
nombrar una carretera que no existe: el espacio de salida es el del escenario.

Una sola petición por tick con todas las preguntas (se evalúan en paralelo y en
aislamiento). El tick nunca lanza ni bloquea el replan: si Jev no contesta a tiempo,
`tick` devuelve `None` y la llamada sigue (degradación explícita, se anota una vez).

`state` es la transcripción más el contexto mínimo (los hechos vigentes sobre esa
arista) y nada más: la precisión cae cuando el estado crece con detalle irrelevante.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from contracts.questions import NOT_STATED, URGENCY_LEVELS, QuestionSpec, call_questions
from contracts.settings import settings

log = logging.getLogger("voice.jev")

TICK_TIMEOUT_S = 1.5
"""Un tick sirve para el siguiente hueco; si Jev tarda más, se pierde este y se sigue."""
TICK_HARD_S = 2.5
"""Tope duro por encima de los reintentos del SDK: nada de un tick colgado."""
GATE_TRANSCRIPTS = Path("fixtures/transcripts/jev_gate.jsonl")


@dataclass(frozen=True)
class Answer:
    value: str | bool
    confidence: float

    @property
    def stated(self) -> bool:
        """`not_stated` no es un fallo: es que quien llama no lo ha dicho."""
        return self.value != NOT_STATED


@dataclass
class Perception:
    """Lo que Jev ha resuelto en un tick, ya sin tipos del SDK."""

    answers: dict[str, Answer] = field(default_factory=dict)
    latency_ms: float = 0.0
    tokens_in: int = 0

    def get(self, key: str) -> Answer | None:
        return self.answers.get(key)


def _urgency_level(score: float) -> str:
    """El score de Jev es el índice (con decimales) del nivel en `criteria`."""
    idx = min(max(round(score), 0), len(URGENCY_LEVELS) - 1)
    return URGENCY_LEVELS[idx]


def build_state(
    turns: list[dict[str, str]], known_facts: list[str] | None = None
) -> dict:
    """`turns` = [{"speaker": "caller"|"operator", "text": ...}]. Las etiquetas son las
    que nombra la instrucción de cada pregunta ("CALLER"): el operador puede decir
    "¿está cortada la pista del sur?" y esa frase no es un hecho."""
    state: dict = {"transcript": turns}
    if known_facts:
        state["known_facts"] = known_facts
    return state


def _to_sdk(spec: QuestionSpec):
    from typesafe_sdk import Choice, Noul, Score

    if spec.kind == "choice":
        assert isinstance(spec.criteria, dict)
        return Choice(instructions=spec.instructions, criteria=spec.criteria)
    if spec.kind == "score":
        assert isinstance(spec.criteria, list)
        return Score(instructions=spec.instructions, criteria=spec.criteria)
    return Noul(instructions=spec.instructions)


def _read(spec: QuestionSpec, ans) -> Answer | None:
    """Del tipo del SDK a `Answer`. Noul no trae `confidence`: se deriva de la
    distancia a 0,5 (documentado; con 0,5 no hay señal)."""
    if spec.kind == "choice":
        return Answer(str(ans.choice), float(ans.confidence))
    if spec.kind == "score":
        return Answer(_urgency_level(float(ans.score)), float(ans.confidence))
    p = float(ans.noul)
    return Answer(p >= 0.5, max(p, 1.0 - p))


class JevClient:
    """Cliente de TypeSafe. `enabled` es False sin clave: el que lo use cae a
    `--no-jev` y lo dice."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client=None,
    ) -> None:
        self._key = api_key if api_key is not None else settings.typesafe_api_key
        self._model = model or settings.typesafe_model
        self._client = client
        self._warned: set[str] = set()
        self.failed_reason: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._key) and not settings.vela_no_jev and self.failed_reason is None

    def _sdk(self):
        if self._client is None:
            from typesafe_sdk import AsyncTypeSafeClient

            self._client = AsyncTypeSafeClient(api_key=self._key, model=self._model)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _warn(self, tag: str, msg: str) -> None:
        if tag not in self._warned:
            self._warned.add(tag)
            log.warning("jev %s: %s", tag, msg)

    async def tick(
        self, state: dict, questions: dict[str, QuestionSpec]
    ) -> Perception | None:
        """Una petición, todas las preguntas. None si Jev no está, no contesta a
        tiempo o falla; nunca lanza. Un fallo de autenticación desactiva el cliente
        para el resto del run (no se reintenta a ciegas cada 5 s)."""
        if not self.enabled or not questions:
            return None
        from typesafe_sdk import RetryPolicy, TypeSafeAuthenticationError, TypeSafeError

        t0 = time.perf_counter()
        try:
            res = await asyncio.wait_for(
                self._sdk().system_one(
                    state,
                    {k: _to_sdk(q) for k, q in questions.items()},
                    timeout=TICK_TIMEOUT_S,
                    retry=RetryPolicy(max_retries=1),
                ),
                TICK_HARD_S,
            )
        except TypeSafeAuthenticationError as exc:
            self.failed_reason = f"clave rechazada: {exc}"
            log.warning("jev desactivado, se cae a --no-jev: %s", self.failed_reason)
            return None
        except (TimeoutError, TypeSafeError) as exc:
            self._warn(type(exc).__name__, str(exc)[:200])
            return None
        answers: dict[str, Answer] = {}
        for key, spec in questions.items():
            raw = res.answers.get(key)
            if raw is None:
                continue
            got = _read(spec, raw)
            if got is not None:
                answers[key] = got
        return Perception(
            answers=answers,
            latency_ms=(time.perf_counter() - t0) * 1000,
            tokens_in=int(getattr(res.usage, "input_tokens", 0) or 0),
        )


_jev: JevClient | None = None


def configure(jev: JevClient | None) -> None:
    """Inyección para tests y `--mock-calls`: `voice.fake.FakeJev` cumple la misma
    superficie (`enabled`, `tick`, `aclose`)."""
    global _jev
    _jev = jev


def get_jev() -> JevClient:
    global _jev
    if _jev is None:
        _jev = JevClient()
    return _jev


# --- La puerta del viernes -------------------------------------------------------


def _accepts(expected, got: str) -> bool:
    return got in expected if isinstance(expected, list) else got == expected


async def gate(path: Path, scenario: Path) -> int:
    """Diez transcripciones en español con etiqueta a mano contra la clave real.
    Aceptación: `location_hint` ≥ 9/10 y ninguna arista fuera del escenario. Si falla,
    `--no-jev` y no se vuelve a hablar del tema."""
    import yaml  # voice no importa de sim (invariante 4): el YAML entra por contracts

    from contracts.scenario import Scenario

    scn = Scenario.model_validate(yaml.safe_load(scenario.read_text(encoding="utf-8")))
    questions = call_questions(scn)
    known_roads = {r.id for r in scn.roads} | {NOT_STATED}
    jev = JevClient()
    if not jev.enabled:
        print("Sin TYPESAFE_API_KEY (o VELA_NO_JEV): no hay puerta que pasar.")
        return 2
    cases = [
        json.loads(ln)
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    hits: dict[str, int] = dict.fromkeys(questions, 0)
    total: dict[str, int] = dict.fromkeys(questions, 0)
    latencies: list[float] = []
    invented = 0
    try:
        for case in cases:
            turns = [{"speaker": s, "text": t} for s, t in case["turns"]]
            perc = await jev.tick(build_state(turns), questions)
            if perc is None:
                print(
                    f"{case['id']:>3}  SIN RESPUESTA ({jev.failed_reason or 'timeout/red'})"
                )
                continue
            latencies.append(perc.latency_ms)
            line = [f"{case['id']:>3} {perc.latency_ms:5.0f} ms"]
            for key, expected in case["expect"].items():
                got = perc.get(key)
                val = str(got.value) if got else "-"
                ok = got is not None and _accepts(expected, val)
                total[key] += 1
                hits[key] += ok
                line.append(
                    f"{key}={val}({got.confidence:.2f}){'' if ok else ' ✗'}"
                    if got
                    else f"{key}=- ✗"
                )
            if (rb := perc.get("road_blocked")) and rb.value not in known_roads:
                invented += 1
            print("  ".join(line))
    finally:
        await jev.aclose()
    print()
    for key, n in total.items():
        if n:
            print(f"  {key:<16} {hits[key]}/{n}")
    if latencies:
        lat = sorted(latencies)
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
        print(f"  latencia p50 {statistics.median(lat):.0f} ms · p95 {p95:.0f} ms")
    print(f"  aristas inventadas: {invented}")
    ok = total["location_hint"] and hits["location_hint"] >= 9 and invented == 0
    print("PUERTA:", "superada" if ok else "NO superada → --no-jev")
    return 0 if ok else 1


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(
        description="Puerta de Jev sobre transcripciones en español"
    )
    p.add_argument("--gate", action="store_true")
    p.add_argument("--file", type=Path, default=GATE_TRANSCRIPTS)
    p.add_argument("--scenario", type=Path, default=Path("scenarios/wildfire_ridge.yaml"))
    args = p.parse_args()
    if not args.gate:
        p.error("usa --gate")
    raise SystemExit(asyncio.run(gate(args.file, args.scenario)))


if __name__ == "__main__":
    main()
