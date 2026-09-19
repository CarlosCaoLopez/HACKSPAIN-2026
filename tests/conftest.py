"""Los tests nunca hablan con TypeSafe: una clave real en el `.env` de quien los corre
no debe convertir un test unitario en una llamada de red (ni en un gasto)."""

import pytest

from voice import jev


@pytest.fixture(autouse=True)
def _jev_off():
    jev.configure(jev.JevClient(api_key=""))  # sin clave: `enabled` es False
    yield
    jev.configure(None)
