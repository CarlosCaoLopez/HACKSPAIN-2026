"""NASA FIRMS: focos activos por satélite. SPEC-007 · REQ-250…252.

Es la fuente que pone a prueba una hipótesis: si alguien dice por teléfono «el fuego ya
está en el molino», un foco VIIRS en esa celda lo corrobora. Pero **la ausencia de foco no
se publica jamás** como «no hay fuego» (REQ-251): un pase de satélite cada pocas horas, con
nubes o humo denso, no observa la ausencia de nada.

En España FIRMS no es tiempo real (el ultra real-time solo cubre Norteamérica): son pases
NRT con retraso de horas. Por eso el modo fechado es donde luce.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, NamedTuple

import httpx

from contracts.events import FactAsserted
from gateway.feeds import FeedContext, Observation, Parsed
from gateway.feeds.anchor import GeoAnchor, bbox, cell_id_at, to_world

AREA_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
# Del más reciente al más antiguo: cada satélite pasa por la zona a horas distintas.
SOURCES = ("VIIRS_NOAA21_NRT", "VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT")
SP_FALLBACK = {
    "VIIRS_NOAA21_NRT": "VIIRS_NOAA21_SP",
    "VIIRS_NOAA20_NRT": "VIIRS_NOAA20_SP",
    "VIIRS_SNPP_NRT": "VIIRS_SNPP_SP",
}

VIIRS_PIXEL_M = 375  # resolución nominal de VIIRS, para dibujar la incertidumbre espacial
CONFIDENCE = {"l": 0.4, "n": 0.7, "h": 0.9}
REQUIRED = ("latitude", "longitude", "acq_date", "acq_time", "confidence")


class FirmsRecord(NamedTuple):
    lat: float
    lon: float
    t: datetime  # aware, UTC
    satellite: str
    confidence: str  # l | n | h


# --- E/S ------------------------------------------------------------------------------


async def fetch(
    client: httpx.AsyncClient,
    map_key: str,
    anchor: GeoAnchor,
    source: str,
    days: int = 1,
    date: str | None = None,
) -> str:
    """El CSV de una fuente sobre el cuadro del ancla. Con `date` (YYYY-MM-DD) pide ese día
    y `days` a partir de él; sin ella, los últimos `days`."""
    west, south, east, north = bbox(anchor)
    area = f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}"
    url = f"{AREA_URL}/{map_key}/{source}/{area}/{days}" + (f"/{date}" if date else "")
    res = await client.get(url)
    res.raise_for_status()
    # FIRMS contesta 200 con un texto de error cuando la clave no vale o se agota el cupo.
    # Aquí no hay cabecera de CSV: `parse_csv` lo convierte en un error legible.
    return res.text


# --- parseo (puro) ---------------------------------------------------------------------


def parse_csv(text: str) -> Parsed[FirmsRecord]:
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    missing = [c for c in REQUIRED if c not in fields]
    if missing:
        # Un CSV sin cabecera de FIRMS es casi siempre un mensaje de error con status 200.
        raise ValueError(f"FIRMS: faltan columnas {missing}; respuesta: {text[:120]!r}")

    records: list[FirmsRecord] = []
    malformed = 0
    for row in reader:
        try:
            conf = row["confidence"].strip().lower()
            if conf not in CONFIDENCE:
                raise ValueError(f"confianza VIIRS desconocida: {conf!r}")
            hhmm = row["acq_time"].strip().zfill(4)
            # FIRMS da UTC sin zona: se declara aquí, en el parseo, y no se deja ingenuo.
            t = datetime.strptime(f"{row['acq_date'].strip()} {hhmm}+0000", "%Y-%m-%d %H%M%z")
            records.append(
                FirmsRecord(
                    lat=float(row["latitude"]),
                    lon=float(row["longitude"]),
                    t=t,
                    satellite=(row.get("satellite") or "?").strip(),
                    confidence=conf,
                )
            )
        except (KeyError, ValueError, AttributeError):
            malformed += 1
    return Parsed(records, malformed)


# --- traducción a hechos (pura) -----------------------------------------------------------


def _inside(anchor: GeoAnchor, rec: FirmsRecord) -> bool:
    west, south, east, north = bbox(anchor)
    return west <= rec.lon <= east and south <= rec.lat <= north


def source_of(rec: FirmsRecord) -> str:
    return f"api:firms:{rec.satellite}:{rec.t:%Y-%m-%dT%H%M}:{rec.lat:.4f},{rec.lon:.4f}"


def to_facts(records: list[FirmsRecord], anchor: GeoAnchor, ctx: FeedContext) -> list[Observation]:
    out: list[Observation] = []
    for rec in records:
        if not _inside(anchor, rec):
            continue
        source = source_of(rec)
        dedup = f"firms:{source}"
        if dedup in ctx.seen:
            continue
        x, z = to_world(anchor, rec.lat, rec.lon)
        cell = cell_id_at(x, z, ctx.cell_size, ctx.cells, ctx.origin_cell)
        if cell is None:  # cae fuera de la rejilla del valle: no hay celda que encender
            continue
        out.append(
            Observation(
                rec.t,
                FactAsserted(
                    key=f"cell:{cell}:state",
                    value="burning",
                    confidence=CONFIDENCE[rec.confidence],
                    source=source,
                    severity="low" if rec.confidence == "l" else "critical",
                    kind="observed",
                ),
            )
        )
        ctx.seen.add(dedup)
    return out


def detections(records: list[FirmsRecord], anchor: GeoAnchor) -> list[dict[str, Any]]:
    """Para el mapa (`/api/feeds`): el foco con el tamaño de su píxel.

    No entra en el hecho porque `FactAsserted` no tiene dónde llevarlo. Un satélite no
    localiza un foco a un bloque: enseñar un punto sería una precisión falsa, así que se
    enseña el cuadrado de 375 m con su incertidumbre.
    """
    footprint = VIIRS_PIXEL_M / anchor.meters_per_block
    out = []
    for rec in records:
        if not _inside(anchor, rec):
            continue
        x, z = to_world(anchor, rec.lat, rec.lon)
        out.append(
            {
                "x": round(x, 2),
                "z": round(z, 2),
                "footprint_blocks": round(footprint, 2),
                "t_real": rec.t.isoformat(),
                "satellite": rec.satellite,
                "confidence": rec.confidence,
            }
        )
    return out
