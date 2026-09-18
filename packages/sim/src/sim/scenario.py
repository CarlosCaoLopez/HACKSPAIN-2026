"""Loader del YAML. Nada más: el modelo vive en `contracts.scenario`."""

from pathlib import Path

from contracts.scenario import Scenario


def load(path: Path) -> Scenario:
    """Parsea `scenarios/*.yaml`. Valida con Pydantic: un YAML malo falla aquí y
    no a los tres minutos de demo."""
    raise NotImplementedError


def list_scenarios(directory: Path) -> list[str]:
    """Los ids disponibles, para `GET /api/scenarios`."""
    raise NotImplementedError
