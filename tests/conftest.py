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
