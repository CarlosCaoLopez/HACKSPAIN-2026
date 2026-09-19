"""Loader del YAML. Nada más: el modelo vive en `contracts.scenario`."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from contracts.factkeys import road_bare
from contracts.scenario import Scenario
from sim.hazard import HAZARDS, parse_cell

PREFIXES = {
    "pois": "poi_",
    "units": "unit_",
    "waypoints": "wp_",
    "civilians": "civ_",
}
"""Ids con prefijo, que es convención del proyecto y aquí se comprueba de verdad.

Las aristas no están aquí porque su id no es un prefijo más un nombre: **es su
propia dirección**, `road:wp_a-wp_b`, la misma forma con la que una llamada o un
`human.override` nombran una carretera. Que el id coincida con los extremos lo
comprueba `_check` aparte.

Ese `road:` se pone una sola vez: `factkeys.road_cut_key` normaliza con
`road_bare` antes de componer `road:<edge_id>:cut`, porque `core.divergence` parte
esa clave por `:` esperando tres segmentos. Con el prefijo dos veces la suposición
queda no evaluable y la divergencia no detecta el corte, que es el disparo del
replan de la demo."""


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
        if road_bare(road.id) != f"{road.a}-{road.b}":
            errors.append(
                f"carretera {road.id!r} debería llamarse road:{road.a}-{road.b}: el id "
                "de una arista es su propia dirección"
            )
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

    # Los alias son lo que resuelve una llamada: uno que apunte a un id mal
    # escrito falla aquí y no cuando el vecino dice "el molino" en la demo.
    for alias, poi_id in s.poi_aliases.items():
        if poi_id not in pois:
            errors.append(f"poi_aliases: {alias!r} apunta a {poi_id!r}, que no existe")
    roads = {r.id for r in s.roads}
    for alias, edge_id in s.road_aliases.items():
        if edge_id not in roads:
            errors.append(f"road_aliases: {alias!r} apunta a {edge_id!r}, que no existe")

    if errors:
        raise ScenarioError(f"{name} es incoherente:\n  - " + "\n  - ".join(errors))


def _duplicated(s: Scenario) -> list[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for field in PREFIXES:
        for item in getattr(s, field):
            (repeated if item.id in seen else seen).add(item.id)
    return sorted(repeated)
