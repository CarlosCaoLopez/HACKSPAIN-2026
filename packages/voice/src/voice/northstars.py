"""Northstars de HappyRobot: las reglas del agente, como código.

Una Northstar es un guardarraíl evaluable: una regla binaria sobre cómo debe
comportarse el agente —qué dice, qué no dice nunca, qué herramienta usa y en qué
orden— que un juez automático puntúa en **cada** sesión, con prioridad según el
impacto y calibración a partir del pulgar humano.

**Por qué aquí y no clicadas en la UI.** Una regla escrita en un formulario no se
revisa en un PR, no se recrea si alguien toca el agente y no se puede contrastar
con los invariantes del proyecto. Definidas como datos, `sync()` las empuja y el
fichero es la fuente de verdad.

**Qué cubren que nuestro código no puede.** El invariante 8 dice que un hecho
asumido nunca se disfraza de observado, y lo aplicamos en `contracts` con el
campo `kind`. Pero eso solo vigila lo que pasa **después** de que el agente hable:
si el agente le dice al vecino «confirmado, la pista sur está cortada» cuando el
vecino no lo dijo, el hecho entra como `observed` porque el agente lo afirmó, y
nuestro `kind` no se entera. Esa mitad —la conversación— solo la audita esto.

API: https://platform.eu.happyrobot.ai/api/v2 · `Authorization: Bearer ...`
con la misma `HAPPYROBOT_API_KEY` que ya usa `voice/happyrobot.py`.

    uv run python -m voice.northstars --workflows          # descubrir ids
    uv run python -m voice.northstars --sync NODE VERSION  # crear las reglas
    uv run python -m voice.northstars --audits RUN_ID      # veredictos de un run
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from contracts.settings import settings

TIMEOUT_S = 20.0

Category = Literal["notes", "style", "tool", "sequential"]
"""Las cuatro de la API. `tool` es sobre qué herramienta invoca, `sequential`
sobre el orden, `notes` sobre el contenido de lo que dice, `style` sobre cómo."""

Priority = Literal["low", "medium", "high"]


def _rich(texto: str) -> dict[str, Any]:
    """Un bloque de `description` / `*_examples`.

    La forma es la de Slate —`children`, no `content`—, copiada de las Northstars
    que genera la propia plataforma. La API declara el campo como registro libre y
    acepta cualquier cosa (un `{"text": ...}` suelto entra con 201), así que esto
    no lo dice el esquema: se sabe leyendo una suya.
    """
    return {"type": "paragraph", "children": [{"text": texto}]}


@dataclass(frozen=True)
class Northstar:
    """Una regla. `description` va en lista porque la API la espera así: cada
    entrada es una condición que el juez evalúa por separado."""

    name: str
    description: list[str]
    category: Category
    priority: Priority = "medium"
    positive_examples: list[str] = field(default_factory=list)
    negative_examples: list[str] = field(default_factory=list)

    def payload(self, version_id: str) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": [_rich(d) for d in self.description],
            "category": self.category,
            "priority": self.priority,
            "version_id": version_id,
            "positive_examples": [_rich(e) for e in self.positive_examples],
            "negative_examples": [_rich(e) for e in self.negative_examples],
        }


ENTRANTE: list[Northstar] = [
    Northstar(
        name="Invoca report_fact antes de colgar",
        description=[
            (
                "Si el vecino nombra un lugar, una carretera o personas que no "
                "pueden moverse, el agente debe invocar el tool `report_fact` "
                "antes de que termine la llamada."
            ),
        ],
        category="tool",
        priority="high",
        positive_examples=[
            (
                "El vecino dice «la pista del sur está cortada» y el agente "
                "invoca report_fact con road_blocked antes de despedirse."
            ),
        ],
        negative_examples=[
            (
                "El vecino describe el corte, el agente responde «tomo nota» y "
                "cuelga sin invocar el tool."
            ),
        ],
    ),
    Northstar(
        name="No afirma como confirmado lo que nadie ha dicho",
        description=[
            (
                "El agente no puede presentar como hecho nada que el "
                "interlocutor no haya dicho. Puede preguntar o proponer; no "
                "puede dar por confirmado."
            ),
            (
                "Si rellena un hueco por su cuenta, tiene que decir que lo está "
                "suponiendo."
            ),
        ],
        category="notes",
        priority="high",
        positive_examples=[
            "«¿Serían unas tres personas?» cuando el vecino no dio el número.",
        ],
        negative_examples=[
            (
                "«Confirmado, hay tres personas que no pueden andar» sin que el "
                "vecino haya mencionado cuántas."
            ),
        ],
    ),
    Northstar(
        name="Una repregunta cada vez",
        description=[
            (
                "Como mucho una pregunta por turno. Nada de encadenar dos o tres "
                "en la misma intervención."
            ),
        ],
        category="style",
        priority="medium",
        negative_examples=[
            "«¿Dónde está, cuántos son y puede salir por el norte?»",
        ],
    ),
    Northstar(
        name="Devuelve el lugar entendido antes de cerrar",
        description=[
            (
                "Antes de terminar, el agente repite el nombre del sitio que ha "
                "entendido, para que el vecino pueda corregirlo."
            ),
        ],
        category="sequential",
        priority="medium",
    ),
]
"""Agente entrante (`citizen_report`): el vecino llama.

La primera es la que más pesa: **el clímax de la demo depende de que el tool se
invoque durante la llamada**. Si el agente se lo salta, el corte de carretera no
llega al core, la divergencia no ve nada y el replan no salta. Hoy no hay nada en
nuestro lado que compruebe eso.

La segunda es el invariante 8 visto desde la conversación."""


SALIENTE: list[Northstar] = [
    Northstar(
        name="Dice el pueblo y la ruta del payload, sin inventar",
        description=[
            (
                "Los nombres del pueblo y de la ruta salen de las variables del "
                "workflow. El agente no puede sustituirlos ni improvisar otros."
            ),
        ],
        category="notes",
        priority="high",
        negative_examples=[
            (
                "Decir «salgan por la carretera general» cuando el payload dice "
                "«desvío norte»."
            ),
        ],
    ),
    Northstar(
        name="Obtiene confirmación explícita",
        description=[
            (
                "No cuelga sin que el interlocutor haya confirmado que ha "
                "entendido la orden."
            ),
        ],
        category="sequential",
        priority="high",
    ),
    Northstar(
        name="Suena el aviso de grabación",
        description=[
            (
                "El aviso de IA y grabación se emite al descolgar. Es obligatorio "
                "en la UE y se cuenta en el presupuesto de tiempo del guion."
            ),
        ],
        category="sequential",
        priority="high",
    ),
]
"""Agente saliente (`evacuation_order`): el sistema llama y dicta la orden."""


def _client() -> httpx.AsyncClient:
    """La clave de deployment (`sk_live_`) vale para la API de plataforma; está
    comprobado contra `GET /workflows/`. `HAPPYROBOT_ORG_KEY` existe por si algún
    día hace falta una de organización con más alcance, y gana si está puesta."""
    key = settings.happyrobot_org_key or settings.happyrobot_api_key
    if not key:
        raise ValueError(
            "no hay clave: pon HAPPYROBOT_API_KEY (o HAPPYROBOT_ORG_KEY) en el .env"
        )
    return httpx.AsyncClient(
        base_url=settings.happyrobot_api_base,
        headers={"Authorization": f"Bearer {key}"},
        timeout=TIMEOUT_S,
    )


async def workflows() -> list[dict[str, Any]]:
    """Los workflows de la organización. De aquí salen los ids para `sync`."""
    async with _client() as c:
        r = await c.get("/workflows/")
        r.raise_for_status()
        data = r.json()
        return data.get("data", data) if isinstance(data, dict) else data


async def nodes(version_id: str) -> list[dict[str, Any]]:
    """Los nodos de una versión. El `node_id` del agente es el que lleva prompt."""
    async with _client() as c:
        r = await c.get(f"/versions/{version_id}/nodes")
        r.raise_for_status()
        data = r.json()
        return data.get("data", data) if isinstance(data, dict) else data


async def existing(node_id: str) -> list[dict[str, Any]]:
    async with _client() as c:
        r = await c.get(f"/nodes/{node_id}/northstars")
        r.raise_for_status()
        data = r.json()
        return data.get("data", data) if isinstance(data, dict) else data


async def sync(
    node_id: str, version_id: str, reglas: list[Northstar]
) -> list[dict[str, Any]]:
    """Crea las que falten. Idempotente por nombre: no duplica al repetir.

    No borra las que ya haya y no estén aquí — puede haberlas puesto otro a mano,
    y perder una regla de gobernanza en silencio es peor que dejarla de más.
    """
    ya = {n.get("name") for n in await existing(node_id)}
    creadas = []
    async with _client() as c:
        for regla in reglas:
            if regla.name in ya:
                continue
            r = await c.post(
                f"/nodes/{node_id}/northstars", json=regla.payload(version_id)
            )
            if r.status_code >= 400:
                raise RuntimeError(f"{regla.name}: HTTP {r.status_code} · {r.text[:400]}")
            # La API envuelve: {"northstar": {...}}.
            cuerpo = r.json()
            creadas.append(cuerpo.get("northstar", cuerpo))
    return creadas


async def audits(run_id: str) -> list[dict[str, Any]]:
    """Los veredictos de una sesión. Es lo que traeríamos a nuestro journal."""
    async with _client() as c:
        r = await c.get(f"/runs/{run_id}/audits")
        r.raise_for_status()
        data = r.json()
        return data.get("data", data) if isinstance(data, dict) else data


async def workflow_audits(workflow_id: str) -> list[dict[str, Any]]:
    """Veredictos de todas las sesiones del workflow, no de una sola."""
    async with _client() as c:
        r = await c.get(f"/workflows/{workflow_id}/audits/northstars")
        r.raise_for_status()
        return r.json().get("data", [])


async def stats(workflow_id: str) -> dict[str, Any]:
    """Resumen de 24 h: `pass_rate_24h`, `average_run_score`, cuántas se auditaron.

    Es el número que enseñar en la demo: no «el agente se portó bien» sino qué
    porcentaje de reglas pasó, contadas por un juez que no somos nosotros."""
    async with _client() as c:
        r = await c.get(f"/workflows/{workflow_id}/audits/stats")
        r.raise_for_status()
        return r.json()


async def feedback(
    northstar_id: str, correctness: int, nota: str = "", regenerar: bool = False
) -> dict[str, Any]:
    """Calibra una Northstar con un veredicto sobre el juez, no sobre el agente.

    `correctness` va de **-2 a +2** (-2 = el juez se equivocó de lleno, +2 = acertó
    de lleno). No es un booleano: un 0 es «ni una cosa ni otra», que es justo lo que
    no quieres decirle. Una entrada por clave de API.

    `regenerar=True` hace que la plataforma **reescriba la regla** a partir del
    comentario. Por defecto no, porque entonces el texto deja de ser el que hay en
    este fichero y el `sync` ya no manda.

    Aquí está lo interesante: **nosotros sabemos a posteriori** si un hecho que el
    agente afirmó resultó falso, porque el journal registra cuándo otra llamada o un
    `human.override` lo contradice. Eso es un pulgar objetivo, no una opinión.
    """
    if not -2 <= correctness <= 2:
        raise ValueError(f"correctness va de -2 a +2, no {correctness}")
    async with _client() as c:
        r = await c.post(
            f"/northstars/{northstar_id}/feedback",
            json={
                "correctness": correctness,
                "feedback": nota,
                "trigger_regeneration": regenerar,
            },
        )
        r.raise_for_status()
        return r.json()


def main() -> None:
    ap = argparse.ArgumentParser(description="Northstars de HappyRobot · vela")
    ap.add_argument("--workflows", action="store_true", help="lista workflows e ids")
    ap.add_argument("--nodes", metavar="VERSION_ID", help="lista nodos de una versión")
    ap.add_argument("--existing", metavar="NODE_ID", help="northstars ya creadas")
    ap.add_argument(
        "--sync", nargs=2, metavar=("NODE_ID", "VERSION_ID"), help="crear las que falten"
    )
    ap.add_argument(
        "--agent",
        choices=("entrante", "saliente"),
        default="entrante",
        help="qué juego de reglas sincronizar",
    )
    ap.add_argument("--audits", metavar="RUN_ID", help="veredictos de una sesión")
    ap.add_argument("--stats", metavar="WORKFLOW_ID", help="resumen de 24 h")
    ap.add_argument("--dry-run", action="store_true", help="enseña el payload y no llama")
    args = ap.parse_args()

    reglas = ENTRANTE if args.agent == "entrante" else SALIENTE

    if args.dry_run:
        print(
            json.dumps(
                [r.payload("<version_id>") for r in reglas], indent=2, ensure_ascii=False
            )
        )
        return

    async def run() -> None:
        if args.workflows:
            for w in await workflows():
                print(f"  {w.get('id')}  {w.get('name', '')}")
        elif args.nodes:
            for n in await nodes(args.nodes):
                print(f"  {n.get('id')}  {n.get('type', '')}  {n.get('name', '')}")
        elif args.existing:
            for n in await existing(args.existing):
                print(f"  {n.get('id')}  [{n.get('priority')}] {n.get('name')}")
        elif args.sync:
            creadas = await sync(args.sync[0], args.sync[1], reglas)
            print(f"creadas {len(creadas)} de {len(reglas)} ({args.agent})")
            for n in creadas:
                print(f"  {n.get('id')}  {n.get('name')}")
        elif args.audits:
            for a in await audits(args.audits):
                print(
                    f"  {a.get('northstar_name', a.get('northstar_id'))}: "
                    f"{a.get('verdict', a.get('result'))}  {a.get('remark', '')}"
                )
        elif args.stats:
            for k, v in (await stats(args.stats)).items():
                print(f"  {k:24} {v}")
        else:
            ap.print_help()

    asyncio.run(run())


if __name__ == "__main__":
    main()
