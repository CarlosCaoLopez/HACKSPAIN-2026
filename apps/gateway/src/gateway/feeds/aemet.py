"""AEMET: avisos Meteoalerta en CAP 1.2. SPEC-007 · REQ-253…255.

Un aviso rojo oficial es el hecho `severity: critical` que la invariante 7 exige para
replanificar. El formato es el del anexo 3 del Plan Meteoalerta (METEOALERTA_ANX3_CAP):

- el **nivel** es el parámetro `AEMET-Meteoalerta nivel` (`verde` = sin aviso, `amarillo`,
  `naranja`, `rojo`);
- el **fenómeno** va en `eventCode` como `FF;Nombre` (`VI` vientos, `AT` temperaturas
  máximas, `TO` tormentas…). **No existe un fenómeno de incendio** en Meteoalerta: lo que
  aporta a un incendio es el contexto (viento, calor), y por eso `aemet_events` lo declara
  el ancla;
- la **zona** es el `geocode` `AEMET-Meteoalerta zona`;
- cada `<alert>` trae un `<info>` por idioma (es-ES y en-GB): se lee **uno** o cada aviso
  saldría duplicado;
- un mensaje de **cancelación** llega con `expires == effective` y no es un aviso.
"""

from __future__ import annotations

import io
import tarfile
import xml.etree.ElementTree as ET  # CAP de un organismo oficial: sin dependencia nueva
from datetime import UTC, datetime
from typing import NamedTuple

import httpx

from contracts.events import FactAsserted
from gateway.feeds import FeedContext, Observation, Parsed
from gateway.feeds.anchor import GeoAnchor

BASE_URL = "https://opendata.aemet.es/opendata"
CAP = "urn:oasis:names:tc:emergency:cap:1.2"

LEVEL_KEY = "alert:aemet:level"
EVENT_KEY = "alert:aemet:event"

SEVERITY = {"rojo": "critical", "naranja": "medium", "amarillo": "low"}
# El estándar CAP: `Observed` es un aviso observado; `Likely` ~>50 %; `Possible` <50 %.
CONFIDENCE = {"Observed": 1.0, "Likely": 0.8, "Possible": 0.5}
UNKNOWN_CERTAINTY_CONFIDENCE = 0.5


class CapAlert(NamedTuple):
    identifier: str
    event: str
    event_code: str  # `FF` de `FF;Nombre`: VI, AT, TO…
    level: str  # verde | amarillo | naranja | rojo
    certainty: str
    zones: tuple[str, ...]
    effective: datetime | None
    expires: datetime | None


# --- E/S ------------------------------------------------------------------------------


async def fetch(client: httpx.AsyncClient, api_key: str, area: str) -> bytes:
    """Los avisos vigentes del área."""
    return await _two_steps(client, api_key, f"/api/avisos_cap/ultimoelaborado/area/{area}")


async def fetch_archive(
    client: httpx.AsyncClient, api_key: str, start: datetime, end: datetime
) -> bytes:
    """Los avisos emitidos entre dos instantes (modo fechado). AEMET pide las fechas como
    `AAAA-MM-DDTHH:MM:SSUTC`."""
    fmt = "%Y-%m-%dT%H:%M:%SUTC"
    path = (
        f"/api/avisos_cap/archivo/fechaini/{start.astimezone(UTC):{fmt}}"
        f"/fechafin/{end.astimezone(UTC):{fmt}}"
    )
    return await _two_steps(client, api_key, path)


async def _two_steps(client: httpx.AsyncClient, api_key: str, path: str) -> bytes:
    """El endpoint devuelve `{estado, datos}` y `datos` es la URL del CAP."""
    headers = {"api_key": api_key}
    res = await client.get(f"{BASE_URL}{path}", headers=headers)
    res.raise_for_status()
    body = res.json()
    if body.get("estado") != 200 or "datos" not in body:
        # AEMET contesta 200 HTTP con un `estado` de error dentro (clave mala, cupo).
        raise ValueError(f"AEMET: {body.get('estado')} {body.get('descripcion', '')}".strip())
    data = await client.get(body["datos"])
    data.raise_for_status()
    return data.content


# --- parseo (puro) ---------------------------------------------------------------------


def _q(tag: str) -> str:
    return f"{{{CAP}}}{tag}"


def _when(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _params(el: ET.Element, container: str) -> list[tuple[str, str]]:
    return [
        (p.findtext(_q("valueName")) or "", p.findtext(_q("value")) or "")
        for p in el.findall(_q(container))
    ]


def _xml_documents(payload: bytes) -> list[bytes]:
    """Un `.xml` suelto, o el `.tar.gz` con todos los avisos del área."""
    if not tarfile.is_tarfile(io.BytesIO(payload)):
        return [payload]
    docs: list[bytes] = []
    with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name.endswith(".xml"):
                fh = tar.extractfile(member)
                if fh is not None:
                    docs.append(fh.read())
    return docs


def _parse_alert(root: ET.Element) -> CapAlert:
    infos = root.findall(_q("info"))
    if not infos:
        raise ValueError("alert sin info")
    # Un <info> por idioma: se lee el español; si no lo hay, el primero.
    info = next((i for i in infos if (i.findtext(_q("language")) or "").startswith("es")), infos[0])

    level = ""
    for name, value in _params(info, "parameter"):
        if name == "AEMET-Meteoalerta nivel":
            level = value.strip().lower()
    code = ""
    for name, value in _params(info, "eventCode"):
        # El anexo escribe «fenómeno» con y sin tilde según el ejemplo: se casa por raíz.
        if name.startswith("AEMET-Meteoalerta fen"):
            code = value.split(";")[0].strip().upper()
    zones = tuple(
        value.strip()
        for area in info.findall(_q("area"))
        for name, value in _params(area, "geocode")
        if name == "AEMET-Meteoalerta zona"
    )
    identifier = (root.findtext(_q("identifier")) or "").strip()
    if not identifier or not level:
        raise ValueError(f"aviso sin identifier o sin nivel: {identifier!r}")
    return CapAlert(
        identifier=identifier,
        event=(info.findtext(_q("event")) or "").strip(),
        event_code=code,
        level=level,
        certainty=(info.findtext(_q("certainty")) or "").strip(),
        zones=zones,
        effective=_when(info.findtext(_q("effective"))),
        expires=_when(info.findtext(_q("expires"))),
    )


def parse_cap(payload: bytes) -> Parsed[CapAlert]:
    alerts: list[CapAlert] = []
    malformed = 0
    for doc in _xml_documents(payload):
        try:
            alerts.append(_parse_alert(ET.fromstring(doc)))
        except (ET.ParseError, ValueError):
            # Un aviso roto no debe impedir leer los demás del tar.
            malformed += 1
    return Parsed(alerts, malformed)


# --- traducción a hechos (pura) -----------------------------------------------------------


def _event_matches(alert: CapAlert, wanted: list[str]) -> bool:
    """`aemet_events` casa con el código de fenómeno (`VI`) o con el texto del evento.

    Un código son dos letras y **solo casa con el código**: buscado como subcadena, `VI`
    (vientos) casaría con «a**vi**so de nevadas de ni**vel** naranja» y cada aviso pasaría
    por viento. El casado por texto queda para entradas más largas («nevadas»).
    """
    text = alert.event.lower()
    for entry in (w.strip() for w in wanted):
        if not entry:
            continue
        if len(entry) == 2:
            if entry.upper() == alert.event_code:
                return True
        elif entry.lower() in text:
            return True
    return False


def _is_cancellation(alert: CapAlert) -> bool:
    """El anexo 3 lo define: un aviso retirado sin sustituto llega como un mensaje que
    expira en el mismo instante en que se emite."""
    return (
        alert.effective is not None
        and alert.expires is not None
        and alert.expires <= alert.effective
    )


def to_facts(alerts: list[CapAlert], anchor: GeoAnchor, ctx: FeedContext) -> list[Observation]:
    out: list[Observation] = []
    for a in alerts:
        if a.level not in SEVERITY or _is_cancellation(a):  # `verde` = sin aviso
            continue
        if not set(a.zones) & set(anchor.aemet_zones):
            continue
        if not _event_matches(a, anchor.aemet_events):
            continue
        dedup = f"aemet:{a.identifier}"
        if dedup in ctx.seen:
            continue

        source = f"api:aemet:{a.identifier}"
        confidence = CONFIDENCE.get(a.certainty, UNKNOWN_CERTAINTY_CONFIDENCE)
        severity = SEVERITY[a.level]
        common = {"confidence": confidence, "source": source, "severity": severity, "kind": "observed"}
        out.append(Observation(a.effective, FactAsserted(key=LEVEL_KEY, value=a.level, **common)))
        # El nivel solo no dice de qué es el aviso. El banner de replan necesita «aviso
        # rojo AEMET: <evento>», y `FactAsserted` no tiene otro sitio donde llevarlo.
        out.append(Observation(a.effective, FactAsserted(key=EVENT_KEY, value=a.event, **common)))
        ctx.seen.add(dedup)
    return out
