"""Generador de ruido de llamadas.

Veinte llamadas entrantes sintéticas mientras la demo corre: el dashboard muestra
20 conversaciones y el sistema descarta 17. Ese contraste es la demostración
visual de *qué información importa*.

La población de vecinos la genera Humalike una vez
(`python -m voice.synthetic --personas`, `POST /v1/personas/actions/generate`) y
se guarda en `fixtures/transcripts/personas.json`. Sin ese fichero, hay veinte
vecinos de plantilla. Las transcripciones son deterministas por semilla: solo
tres llevan un hecho que mueve el plan. No pasan por HappyRobot.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import time
from pathlib import Path

import httpx

from contracts.bus import make_event, publish
from contracts.calls import CallFacts, CallResult
from contracts.events import EventType
from contracts.settings import settings

log = logging.getLogger("voice.synthetic")

PERSONAS_PATH = Path("fixtures/transcripts/personas.json")
PERSONAS_PROMPT = (
    "20 vecinos de dos pueblos pequeños de un valle de Castilla durante un incendio "
    "forestal: edades variadas, algunos con movilidad reducida, distintos niveles de "
    "nervios, algunos con información de segunda mano y contradictoria"
)

DEFAULT_PERSONAS = [
    {"name": n, "age": a, "village": v, "mobility": m, "nerves": s}
    for n, a, v, m, s in [
        ("Carmen", 71, "Pueblo A", "reducida", "alto"),
        ("Julián", 45, "Pueblo B", "normal", "medio"),
        ("Rosa", 63, "Pueblo A", "normal", "alto"),
        ("Andrés", 29, "Pueblo B", "normal", "bajo"),
        ("Pilar", 80, "Pueblo B", "reducida", "alto"),
        ("Tomás", 52, "Pueblo A", "normal", "medio"),
        ("Lucía", 34, "Pueblo A", "normal", "bajo"),
        ("Manuel", 68, "Pueblo B", "reducida", "medio"),
        ("Ana", 41, "Pueblo B", "normal", "alto"),
        ("Félix", 58, "Pueblo A", "normal", "medio"),
        ("Isabel", 75, "Pueblo A", "reducida", "alto"),
        ("Diego", 22, "Pueblo B", "normal", "bajo"),
        ("Marta", 47, "Pueblo B", "normal", "medio"),
        ("Ramón", 66, "Pueblo A", "normal", "alto"),
        ("Elena", 38, "Pueblo A", "normal", "bajo"),
        ("Jesús", 55, "Pueblo B", "normal", "medio"),
        ("Dolores", 83, "Pueblo B", "reducida", "alto"),
        ("Sergio", 31, "Pueblo A", "normal", "medio"),
        ("Teresa", 49, "Pueblo B", "normal", "bajo"),
        ("Paco", 60, "Pueblo A", "normal", "alto"),
    ]
]

NOISE = [
    "hay mucho humo por aquí pero no veo fuego, ¿tenemos que irnos?",
    "mi vecina dice que han cortado la carretera pero no sé cuál",
    "¿el colegio está abierto? tengo a los críos en casa",
    "oigo helicópteros, ¿eso es bueno o malo?",
    "no sé si llevarme al perro, ¿qué hago?",
    "en el grupo de whatsapp dicen que el fuego viene para acá",
    "¿hay que cerrar las ventanas o abrirlas?",
    "he visto pasar dos camiones hacia el norte, ¿van bien?",
    "mi hijo está en el pueblo de al lado y no me coge el teléfono",
    "¿la gasolinera sigue abierta? tengo el depósito vacío",
    "me han dicho que evacuan pero yo no he oído nada oficial",
    "el viento ha cambiado, ahora sopla hacia el pueblo",
    "estoy bien, solo quería saber si esto va en serio",
    "¿pueden mandar a alguien a mirar la casa de mis padres? no contestan",
    "se ha ido la luz un momento y ha vuelto, ¿es por el incendio?",
    "hay una vaca suelta en la carretera del norte",
    "estamos en el coche saliendo por el sur, ¿es la dirección correcta?",
]

SIGNAL = [
    (
        "estoy en el molino viejo, la pista del sur está cortada por un árbol y hay tres personas en la casa de al lado que no pueden andar",
        {
            "location_hint": "el molino viejo",
            "road_blocked": "pista del sur",
            "people_immobile": 3,
            "urgency": "critical",
            "confidence": 0.9,
        },
    ),
    (
        "en el pueblo B hay dos heridos con quemaduras, están en la plaza",
        {
            "location_hint": "pueblo B",
            "injuries": 2,
            "urgency": "critical",
            "confidence": 0.85,
        },
    ),
    (
        "el fuego ha saltado a la ladera este, se ve desde el hospital",
        {"location_hint": "hospital", "urgency": "critical", "confidence": 0.7},
    ),
]


def load_personas(path: Path = PERSONAS_PATH) -> list[dict]:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        personas = data.get("personas") if isinstance(data, dict) else data
        if isinstance(personas, list) and personas:
            return personas
    return DEFAULT_PERSONAS


def generate(n: int, t_sim: float, seed: int = 0) -> list[CallResult]:
    """n llamadas con información parcial, desordenada y a veces contradictoria.
    Solo unas pocas llevan un hecho que mueve el plan."""
    rng = random.Random(seed)
    personas = load_personas()
    signal_slots = set(rng.sample(range(n), k=min(len(SIGNAL), n)))
    signal_iter = iter(SIGNAL)
    out: list[CallResult] = []
    for i in range(n):
        persona = personas[i % len(personas)]
        name = str(persona.get("name") or f"vecino{i}")
        if i in signal_slots:
            text, facts = next(signal_iter)
            cf: CallFacts | None = CallFacts.model_validate(facts)
        else:
            text = rng.choice(NOISE)
            cf = CallFacts(urgency="low", confidence=0.3)
        opener = rng.choice(["hola, ", "oiga, ", "perdone, ", "", "mire, "])
        transcript = f"vecino ({name}): {opener}{text}\noperador: entendido, dígame dónde está exactamente."
        started = time.time() - rng.uniform(20, 90)
        out.append(
            CallResult(
                call_id=f"syn_{seed}_{i:02d}",
                task_id=None,
                direction="inbound",
                started_t=started,
                ended_t=started + rng.uniform(15, 60),
                outcome="answered",
                transcript=transcript,
                facts=cf,
            )
        )
    return out


async def burst(n: int, over_s: float = 30.0, seed: int = 0) -> None:
    """Las publica repartidas en el tiempo, como llegarían de verdad."""
    from contracts.bus import current_t_sim

    calls = generate(n, current_t_sim(), seed)
    gap = over_s / max(n, 1)
    for c in calls:
        await publish(
            make_event(
                EventType.CALL_STARTED,
                {
                    "call_id": c.call_id,
                    "task_id": None,
                    "to": "112",
                    "direction": "inbound",
                },
                source="voice",
            )
        )
        await publish(
            make_event(EventType.CALL_ENDED, c.model_dump(mode="json"), source="voice")
        )
        await asyncio.sleep(gap)


# --- Humalike personas, una vez ---------------------------------------------------


async def generate_personas(
    count: int = 20,
    prompt: str = PERSONAS_PROMPT,
    path: Path = PERSONAS_PATH,
    grounding: str = "off",
) -> list[dict]:
    """`POST /v1/personas/actions/generate` y polling hasta `succeeded`. Guarda el
    resultado en `path`. Tarda minutos: nunca en el camino de una petición."""
    if not settings.humalike_api_key:
        raise RuntimeError("HUMALIKE_API_KEY vacío")
    headers = {"Authorization": f"Bearer {settings.humalike_api_key}"}
    async with httpx.AsyncClient(
        base_url="https://api.humalike.com", headers=headers, timeout=30
    ) as c:
        r = await c.post(
            "/v1/personas/actions/generate",
            json={"prompt": prompt, "count": count, "grounding": grounding},
        )
        r.raise_for_status()
        pid = r.json()["id"]
        while True:
            await asyncio.sleep(4)
            g = await c.get(f"/v1/personas/repositories/Population/by-id/{pid}")
            g.raise_for_status()
            data = g.json()
            status = data.get("status")
            log.info("personas %s: %s %s", pid, status, data.get("progress") or "")
            if status == "succeeded":
                result = data.get("result") or {}
                break
            if status == "failed":
                raise RuntimeError(f"personas failed: {data.get('error')}")
    personas = result.get("personas") or result.get("members") or result
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"personas": personas, "raw": result}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return personas if isinstance(personas, list) else []


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser()
    p.add_argument(
        "--personas", action="store_true", help="genera la población con Humalike"
    )
    p.add_argument("--count", type=int, default=20)
    p.add_argument(
        "--print", type=int, default=0, help="imprime n transcripciones sintéticas"
    )
    args = p.parse_args()
    if args.personas:
        asyncio.run(generate_personas(args.count))
    if args.print:
        for c in generate(args.print, 0.0):
            print(
                f"--- {c.call_id} facts={c.facts.model_dump(exclude_none=True) if c.facts else None}"
            )
            print(c.transcript)


if __name__ == "__main__":
    main()
