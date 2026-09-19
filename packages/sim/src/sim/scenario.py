"""Loader del YAML. Nada más: el modelo vive en `contracts.scenario`."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from contracts.scenario import Scenario
from sim.hazard import HAZARDS, parse_cell

PREFIXES = {
    "pois": "poi_",
    "units": "unit_",
    "waypoints": "wp_",
    "roads": "road:",
    "civilians": "civ_",
}
"""Ids con prefijo, que es convención del proyecto y aquí se comprueba de verdad.

Las aristas llevan `road:` y no `rd_` porque su id **es también su dirección**:
`interfaces.md` nombra las carreteras como `road:wp_a-wp_b` en las claves de hecho
(`"road:wp_sur_03-wp_sur_04:cut"`) y en el `target` de `human.override`. Una
llamada de teléfono o un humano en el dashboard no conocen los ids de este
fichero, así que nombran la carretera por sus extremos; usando esa misma forma
como id, no hay dos espacios de nombres que traducir."""


class ScenarioError(ValueError):
    """Un escenario incoherente. Falla al cargar y no a los tres minutos de demo."""


def load(path: Path) -> Scenario:
    """Parsea `scenarios/*.yaml`. Valida con Pydantic: un YAML malo falla aquí y
    no a los tres minutos de demo."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path.name} no es YAML válido: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScenarioError(f"{path.name} no contiene un escenario")

    try:
        scenario = Scenario.model_validate(raw)
    except ValidationError as exc:
        raise ScenarioError(f"{path.name} no cumple el esquema:\n{exc}") from exc

    _check(scenario, path.name)
    return scenario


def list_scenarios(directory: Path) -> list[str]:
    """Los ids disponibles, para `GET /api/scenarios`."""
    return sorted(p.stem for p in directory.glob("*.yaml"))


def _check(s: Scenario, name: str) -> None:
    """Lo que Pydantic no puede saber: que las referencias cruzadas existan.

    Es el equivalente barato de un `mypy` sobre el escenario, y evita el fallo más
    tonto posible: una carretera que apunta a un waypoint mal escrito y un camión
    que no puede salir de la base delante del jurado.
    """
    errors: list[str] = []

    for field, prefix in PREFIXES.items():
        for item in getattr(s, field):
            if not item.id.startswith(prefix):
                errors.append(f"{field}: {item.id!r} debería empezar por {prefix!r}")

    waypoints = {w.id for w in s.waypoints}
    for road in s.roads:
        for end in (road.a, road.b):
            if end not in waypoints:
                errors.append(f"carretera {road.id!r} apunta a {end!r}, que no existe")
        if road.a == road.b:
            errors.append(f"carretera {road.id!r} empieza y acaba en {road.a!r}")

    pois = {p.id for p in s.pois}
    for poi in s.pois:
        if poi.waypoint_id not in waypoints:
            errors.append(
                f"POI {poi.id!r} cuelga de {poi.waypoint_id!r}, que no es un waypoint"
            )
    for group in s.civilians:
        if group.poi_id not in pois:
            errors.append(f"civiles {group.id!r} viven en {group.poi_id!r}, que no existe")
        if group.immobile > group.count:
            errors.append(
                f"civiles {group.id!r}: {group.immobile} inmóviles de {group.count}"
            )

    if s.hazard.kind not in HAZARDS:
        errors.append(f"peligro {s.hazard.kind!r} desconocido; hay {sorted(HAZARDS)}")
    try:
        parse_cell(s.hazard.origin_cell)
    except ValueError as exc:
        errors.append(str(exc))

    duplicates = _duplicated(s)
    errors.extend(f"id repetido: {d!r}" for d in duplicates)

    if errors:
        raise ScenarioError(f"{name} es incoherente:\n  - " + "\n  - ".join(errors))


def _duplicated(s: Scenario) -> list[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for field in PREFIXES:
        for item in getattr(s, field):
            (repeated if item.id in seen else seen).add(item.id)
    return sorted(repeated)
