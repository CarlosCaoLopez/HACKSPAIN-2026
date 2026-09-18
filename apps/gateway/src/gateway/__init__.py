"""`gateway` · P4 · monta todo en un solo proceso FastAPI.

Excepción única a la regla de imports: este paquete importa de todos los demás.
"""

from gateway.main import app

__all__ = ["app"]
