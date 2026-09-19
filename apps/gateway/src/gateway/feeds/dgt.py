"""DGT: incidencias DATEX II v3.7. SPEC-007 · REQ-246…249.

Una incidencia oficial sobre la misma carretera que una llamada dice cortada es la
segunda fuente que convierte un hueco gris en uno sólido. Por eso solo se traduce lo que
**corta** una arista: un cierre de un carril no es un corte.

Regla que no se negocia (invariante 8, REQ-249): que un registro **desaparezca** del feed no
publica `cut=false`. Que ya no esté no es haber observado la carretera abierta; reabrir es
de una observación.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # feed público de un organismo oficial: sin dependencia nueva
from typing import NamedTuple

import httpx

from contracts.events import FactAsserted
from contracts.factkeys import road_bare, road_cause_key, road_cut_key
from gateway.feeds import FeedContext, Observation, Parsed
from gateway.feeds.anchor import EdgeRef, GeoAnchor

# El de versión anterior (v3) está marcado «a extinguir 12/01/2026»: se pide el v3.6, que
# el propio NAP redirige al 3.7.
FEED_URL = "https://nap.dgt.es/datex2/v3/dgt/SituationPublication/datex2_v36.xml"
TIMEOUT_S = 20.0  # el feed pesa ~3,4 MB: el timeout general de 10 s se queda corto

SIT = "http://levelC/schema/3/situation"
LOC = "http://levelC/schema/3/locationReferencing"
LSE = "http://levelC/schema/3/locationReferencingSpanishExtension"
COM = "http://levelC/schema/3/common"

CUT_MANAGEMENT = {"roadClosed", "carriagewayClosures"}
CUT_CAUSES = {"forestFire", "flooding", "rockfalls", "avalanches"}
# Con una causa de corte y toda la calzada afectada, solo estas gestiones dejan la arista
# cortada: ninguna (la incidencia no dice qué se hace) o «no usar estos carriles».
CUT_BY_CAUSE_ALONE = {"", "doNotUseSpecifiedLanesOrCarriageways"}
CRITICAL_CAUSES = {"forestFire", "flooding"}
ALL_LANES = "allLanesCompleteCarriageway"

CONFIDENCE = {"certain": 0.9, "probable": 0.7, "riskOf": 0.4}
# Un valor de `probabilityOfOccurrence` que no conocemos: ni se descarta ni se le da la
# confianza de un `certain`.
UNKNOWN_PROBABILITY_CONFIDENCE = 0.5


class SituationRecord(NamedTuple):
    id: str
    version: str
    validity: str
    probability: str
    road_name: str
    pks: tuple[float, ...]  # un punto trae uno; un tramo, dos (desde y hasta)
    cause_type: str
    detailed_cause: str
    management: str
    lane_usages: tuple[str, ...]


# --- E/S ------------------------------------------------------------------------------


async def fetch(client: httpx.AsyncClient, etag: str | None = None) -> tuple[bytes | None, str | None]:
    """El feed completo (~3,4 MB), o `(None, etag)` si no ha cambiado.

    `If-None-Match` solo sirve si el servidor lo honra; si no, devuelve siempre 200 y aquí
    no cuesta nada más que el ancho de banda.
    """
    headers = {"If-None-Match": etag} if etag else {}
    res = await client.get(FEED_URL, headers=headers, follow_redirects=True, timeout=TIMEOUT_S)
    if res.status_code == 304:
        return None, etag
    res.raise_for_status()
    return res.content, res.headers.get("etag")


# --- parseo (puro) ---------------------------------------------------------------------


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def parse_situations(xml: bytes) -> Parsed[SituationRecord]:
    root = ET.fromstring(xml)  # un XML que no parsea lanza: el feed entero es inválido
    records: list[SituationRecord] = []
    malformed = 0
    for el in root.iter(_q(SIT, "situationRecord")):
        rid = el.get("id")
        if not rid:
            malformed += 1
            continue
        pks: list[float] = []
        for pk in el.iter(_q(LSE, "kilometerPoint")):
            try:
                pks.append(float(pk.text or ""))
            except ValueError:
                # Un PK ilegible se cuenta. El registro sigue con los PK que sí se leen y,
                # sin ninguno, no casa con ninguna arista.
                malformed += 1
        records.append(
            SituationRecord(
                id=rid,
                version=el.get("version", "0"),
                validity=el.findtext(f".//{_q(COM, 'validityStatus')}") or "",
                probability=el.findtext(_q(SIT, "probabilityOfOccurrence")) or "",
                road_name=el.findtext(f".//{_q(LOC, 'roadName')}") or "",
                pks=tuple(pks),
                cause_type=el.findtext(f"{_q(SIT, 'cause')}/{_q(SIT, 'causeType')}") or "",
                detailed_cause=el.findtext(
                    f"{_q(SIT, 'cause')}/{_q(SIT, 'detailedCauseType')}/*"
                )
                or "",
                management=el.findtext(_q(SIT, "roadOrCarriagewayOrLaneManagementType")) or "",
                lane_usages=tuple(
                    u.text for u in el.iter(_q(LOC, "laneUsage")) if u.text
                ),
            )
        )
    return Parsed(records, malformed)


# --- traducción a hechos (pura) -----------------------------------------------------------


def norm_road(name: str) -> str:
    """`N-400`, `n 400` y `N400` son la misma carretera."""
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _overlaps(pks: tuple[float, ...], ref: EdgeRef) -> bool:
    """El tramo del registro y el declarado se tocan. Un registro lineal cubre de un PK a
    otro; exigir que sus dos extremos caigan dentro dejaría fuera un corte que empieza
    antes de la arista y la atraviesa."""
    if not pks:
        return False
    lo, hi = sorted((ref.pk_from, ref.pk_to))
    return min(pks) <= hi and max(pks) >= lo


def is_cut(rec: SituationRecord) -> bool:
    """¿Este registro corta la carretera? Un cierre de carril, un carril estrecho, un paso
    alterno o un desvío **no**: la arista sigue siendo transitable.

    Una causa de corte (inundación, desprendimiento…) sobre toda la calzada solo corta si la
    gestión de la calzada **no dice lo contrario**. Contra el feed real (811 registros), 8 de
    los 9 registros con esa causa traían `singleAlternateLineTraffic`, `lanesDeviated`,
    `narrowLanes` o `useOfSpecifiedLanesOrCarriagewaysAllowed`: hay roca en la calzada y se
    circula. Contarlos como corte le quitaría al solver una carretera que funciona.
    """
    if rec.management in CUT_MANAGEMENT:
        return True
    return (
        rec.detailed_cause in CUT_CAUSES
        and ALL_LANES in rec.lane_usages
        and rec.management in CUT_BY_CAUSE_ALONE
    )


def to_facts(
    records: list[SituationRecord], anchor: GeoAnchor, ctx: FeedContext
) -> list[Observation]:
    out: list[Observation] = []
    route_edges = {road_bare(e) for e in ctx.route_edges}
    for rec in records:
        if rec.validity != "active" or not is_cut(rec):
            continue
        dedup = f"dgt:{rec.id}v{rec.version}"
        if dedup in ctx.seen:
            continue
        edge_id = next(
            (
                eid
                for eid, ref in anchor.edges.items()
                if norm_road(rec.road_name) == norm_road(ref.road_name) and _overlaps(rec.pks, ref)
            ),
            None,
        )
        if edge_id is None:
            continue

        source = f"api:dgt:{rec.id}v{rec.version}"
        confidence = CONFIDENCE.get(rec.probability, UNKNOWN_PROBABILITY_CONFIDENCE)
        # Crítico si el corte es un incendio o una inundación, o si le quita la carretera a
        # una unidad que el plan ya está usando: ahí sí obliga a replanificar.
        critical = rec.detailed_cause in CRITICAL_CAUSES or road_bare(edge_id) in route_edges
        severity = "critical" if critical else "medium"
        cause = f"{rec.detailed_cause or rec.cause_type} · DGT {rec.id}"

        out.append(
            Observation(
                None,
                FactAsserted(
                    key=road_cut_key(edge_id),
                    value=True,
                    confidence=confidence,
                    source=source,
                    severity=severity,
                    kind="observed",
                ),
            )
        )
        out.append(
            Observation(
                None,
                FactAsserted(
                    key=road_cause_key(edge_id),
                    value=cause,
                    confidence=confidence,
                    source=source,
                    severity=severity,
                    kind="observed",
                ),
            )
        )
        ctx.seen.add(dedup)
    return out
