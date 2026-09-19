"""Open-Meteo: el viento como hecho `inferred`. SPEC-007 · REQ-242…245.

Open-Meteo es **salida de un modelo meteorológico, no una estación**: publicarlo como
`observed` sería exactamente el disfraz que prohíbe la invariante 8. Entra como `inferred`.
El viento no funda restricciones duras, así que no pierde efecto.

La convención de rumbo coincide con el contrato: `wind_direction_10m` es de dónde **viene**
el viento (270 = del oeste), igual que `wind.bearing_deg` en `wildfire_ridge.yaml`. No se
convierte nada.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, NamedTuple

import httpx

from contracts.events import FactAsserted
from gateway.feeds import FeedContext, Observation, Parsed
from gateway.feeds.anchor import GeoAnchor

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
# El archivo llega con unos días de retraso: por debajo de esto hay que pedir al forecast.
ARCHIVE_DELAY_DAYS = 5

CONFIDENCE = 0.8
MIN_TURN_DEG = 5.0  # REQ-245: menos que esto no es un cambio
MIN_SPEED_DELTA = 0.05  # celdas por minuto
CRITICAL_TURN_DEG = 60.0  # REQ-244: un giro así es el guion del minuto 02:30


class WindSample(NamedTuple):
    t: datetime  # aware, UTC
    speed_ms: float
    bearing_deg: float


# --- E/S ------------------------------------------------------------------------------


async def fetch_hourly(client: httpx.AsyncClient, anchor: GeoAnchor, day: datetime) -> bytes:
    """La serie horaria del día de `day`, en bruto. Archivo si tiene más de 5 días; forecast si no."""
    age_days = (datetime.now(UTC) - day).days
    url = ARCHIVE_URL if age_days > ARCHIVE_DELAY_DAYS else FORECAST_URL
    params: dict[str, str | float] = {
        "latitude": anchor.lat0,
        "longitude": anchor.lon0,
        "hourly": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "ms",
        "timezone": "UTC",  # `parse_*` da por hecho UTC: sin esto las horas serían locales
        "start_date": day.date().isoformat(),
        "end_date": day.date().isoformat(),
    }
    res = await client.get(url, params=params)
    res.raise_for_status()
    return res.content


async def fetch_current(client: httpx.AsyncClient, anchor: GeoAnchor) -> bytes:
    params: dict[str, str | float] = {
        "latitude": anchor.lat0,
        "longitude": anchor.lon0,
        "current": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "ms",
        "timezone": "UTC",
    }
    res = await client.get(FORECAST_URL, params=params)
    res.raise_for_status()
    return res.content


# --- parseo (puro) ---------------------------------------------------------------------


def _utc(text: str) -> datetime:
    t = datetime.fromisoformat(text)
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def parse_hourly(data: dict[str, Any]) -> Parsed[WindSample]:
    try:
        hourly = data["hourly"]
        times, speeds, bearings = (
            hourly["time"],
            hourly["wind_speed_10m"],
            hourly["wind_direction_10m"],
        )
    except KeyError as exc:
        raise ValueError(f"respuesta de Open-Meteo sin {exc} en `hourly`") from exc

    samples: list[WindSample] = []
    malformed = 0
    for raw_t, speed, bearing in zip(times, speeds, bearings):
        # `null` es normal en la cola de un forecast: no es un dato roto, es un dato que
        # aún no existe.
        if speed is None or bearing is None:
            continue
        try:
            samples.append(WindSample(_utc(raw_t), float(speed), float(bearing)))
        except (TypeError, ValueError):
            malformed += 1
    return Parsed(samples, malformed)


def parse_current(data: dict[str, Any]) -> Parsed[WindSample]:
    try:
        cur = data["current"]
        sample = WindSample(
            _utc(cur["time"]), float(cur["wind_speed_10m"]), float(cur["wind_direction_10m"])
        )
    except KeyError as exc:
        raise ValueError(f"respuesta de Open-Meteo sin {exc} en `current`") from exc
    except (TypeError, ValueError):
        return Parsed([], 1)
    return Parsed([sample], 0)


# --- traducción a hechos (pura) -----------------------------------------------------------


def speed_cells_per_min(v_ms: float, anchor: GeoAnchor, cell_size: int) -> float:
    """Velocidad del viento en las unidades del contrato: celdas por minuto **a favor del
    viento**. El fuego no avanza a la velocidad del aire sino a una fracción
    (`spread_ratio`, la regla del 10 %): es un modelo y el ancla lo declara."""
    return v_ms * anchor.spread_ratio * 60 / (anchor.meters_per_block * cell_size)


def turn_deg(a: float, b: float) -> float:
    """El ángulo más corto entre dos rumbos, en [0, 180]."""
    return abs((a - b + 180) % 360 - 180)


def to_facts(samples: list[WindSample], anchor: GeoAnchor, ctx: FeedContext) -> list[Observation]:
    out: list[Observation] = []
    for s in samples:
        source = f"api:open-meteo:{s.t:%Y-%m-%dT%H:%M}"
        speed = round(speed_cells_per_min(s.speed_ms, anchor, ctx.cell_size), 3)
        prev_bearing = ctx.last_wind.get("bearing_deg")
        prev_speed = ctx.last_wind.get("speed")

        turn = None if prev_bearing is None else turn_deg(s.bearing_deg, prev_bearing)
        if turn is None or turn >= MIN_TURN_DEG:
            # Sin viento previo no hay «giro» que medir, así que el primero no es crítico.
            critical = turn is not None and turn >= CRITICAL_TURN_DEG
            out.append(
                Observation(
                    s.t,
                    FactAsserted(
                        key="wind:bearing_deg",
                        value=s.bearing_deg,
                        confidence=CONFIDENCE,
                        source=source,
                        severity="critical" if critical else "low",
                        kind="inferred",
                    ),
                )
            )
            ctx.last_wind["bearing_deg"] = s.bearing_deg

        if prev_speed is None or abs(speed - prev_speed) >= MIN_SPEED_DELTA:
            out.append(
                Observation(
                    s.t,
                    FactAsserted(
                        key="wind:speed",
                        value=speed,
                        confidence=CONFIDENCE,
                        source=source,
                        severity="low",
                        kind="inferred",
                    ),
                )
            )
            ctx.last_wind["speed"] = speed
    return out
