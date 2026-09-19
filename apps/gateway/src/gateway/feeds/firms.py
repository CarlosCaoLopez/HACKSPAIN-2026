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
# El procesado estándar solo existe para SNPP y NOAA-20: `VIIRS_NOAA21_SP` no existe y FIRMS
# contesta 400 (comprobado contra la API real). NOAA-21 solo tiene NRT.
SP_FALLBACK = {
    "VIIRS_NOAA20_NRT": "VIIRS_NOAA20_SP",
    "VIIRS_SNPP_NRT": "VIIRS_SNPP_SP",
}

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
) -> bytes:
    """El CSV de una fuente sobre el cuadro del ancla. Con `date` (YYYY-MM-DD) pide ese día
    y `days` a partir de él; sin ella, los últimos `days`."""
    west, south, east, north = bbox(anchor)
    area = f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}"
    url = f"{AREA_URL}/{map_key}/{source}/{area}/{days}" + (f"/{date}" if date else "")
    res = await client.get(url)
    res.raise_for_status()
    # FIRMS contesta 200 con un texto de error cuando la clave no vale o se agota el cupo.
    # Aquí no hay cabecera de CSV: `parse_csv` lo convierte en un error legible.
    return res.content


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


def _in_valley(ctx: FeedContext, x: float, z: float) -> bool:
    """El cuadro del ancla (10 km de radio) es mucho más grande que el valle: sobre el
    ancla real, la sonda publicaba focos a 5-10 km de distancia (`cell_97_10`, fuera de la
    rejilla real pero dentro del cuadro). `ctx.world_box` es la caja del escenario; sin ella
    (un adaptador probado suelto) no se filtra."""
    if ctx.world_box is None:
        return True
    x0, x1, z0, z1 = ctx.world_box
    return x0 <= x <= x1 and z0 <= z <= z1


def _severity(rec: FirmsRecord, ctx: FeedContext) -> str:
    """`critical` solo para el primer foco n/h de cada pase de satélite.

    El core replanifica ante CADA hecho crítico y no los agrupa: contra la API real, un solo
    día de incendio grande dio ~390 focos n/h en el valle, y con un crítico por foco eso son
    ~390 llamadas al modelo por un solo suceso (invariante 7). Un pase de satélite es un
    suceso: el primer foco avisa, el resto es detalle y entra como `medium`.
    """
    if rec.confidence == "l":
        return "low"
    pass_key = f"firms-pass:{rec.satellite}:{rec.t:%Y-%m-%dT%H%M}"
    if pass_key in ctx.seen:
        return "medium"
    ctx.seen.add(pass_key)
    return "critical"


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
        if not _in_valley(ctx, x, z):  # a varios km del valle no es un hecho de este mundo
            continue
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
                    severity=_severity(rec, ctx),
                    kind="observed",
                ),
            )
        )
        ctx.seen.add(dedup)
    return out


def detections(records: list[FirmsRecord], anchor: GeoAnchor) -> list[dict[str, Any]]:
    """Para el mapa real (`/api/feeds`): la posición geográfica de cada foco del cuadro,
    esté o no dentro de la rejilla del valle (a diferencia de `to_facts`, que solo publica
    los de dentro). Sin huella de píxel: Minecraft no tiene un equivalente de 375 m que
    mostrar, y el mapa real dibuja el cuadrado con su propia constante (SPEC-008 REQ-288)."""
    out = []
    for rec in records:
        if not _inside(anchor, rec):
            continue
        x, z = to_world(anchor, rec.lat, rec.lon)
        out.append(
            {
                "x": round(x, 2),
                "z": round(z, 2),
                # La coordenada real, para el mapa de OpenStreetMap (SPEC-008 REQ-293).
                "lat": rec.lat,
                "lon": rec.lon,
                "t_real": rec.t.isoformat(),
                "satellite": rec.satellite,
                "confidence": rec.confidence,
            }
        )
    return out
