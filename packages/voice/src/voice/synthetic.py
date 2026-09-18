"""Generador de ruido de llamadas.

Veinte llamadas entrantes sintéticas mientras la demo corre: el dashboard muestra
20 conversaciones y el sistema descarta 17. Ese contraste es la demostración
visual de *qué información importa*.
"""

from contracts.calls import CallResult


def generate(n: int, t_sim: float, seed: int = 0) -> list[CallResult]:
    """n llamadas con información parcial, desordenada y a veces contradictoria.
    Solo unas pocas llevan un hecho que mueve el plan."""
    raise NotImplementedError


async def burst(n: int, over_s: float = 30.0) -> None:
    """Las publica repartidas en el tiempo, como llegarían de verdad."""
    raise NotImplementedError
