"""Los tests nunca hablan con TypeSafe: una clave real en el `.env` de quien los corre
no debe convertir un test unitario en una llamada de red (ni en un gasto)."""

import pytest

from voice import jev


@pytest.fixture(autouse=True)
def _jev_off():
    jev.configure(jev.JevClient(api_key=""))  # sin clave: `enabled` es False
    yield
    jev.configure(None)


@pytest.fixture(autouse=True)
def _sin_token_compartido(monkeypatch):
    """El token de los webhooks tampoco sale del `.env` de quien corre los tests: los
    tests que lo necesitan lo fijan ellos."""
    from contracts.settings import settings

    monkeypatch.setattr(settings, "webhook_shared_token", "")


@pytest.fixture(autouse=True)
def _journals_fuera_de_runs(monkeypatch, tmp_path):
    """`runs/` es el dataset de los runs de verdad, y `POST /api/run` ya arranca un run
    entero (bus + journal + sim + core): cada test que lo hace escribiría ahí su
    `run_<id>.jsonl`. Van a un temporal. Un test que quiera otro directorio lo fija él
    (`test_gateway_score.py` lo hace)."""
    from gateway import main as gateway_main

    monkeypatch.setattr(gateway_main, "RUNS_DIR", tmp_path / "runs")
