"""El guion de la demo. P4.

Seis minutos, un solo clímax: una llamada real cuelga y las unidades giran en
pantalla en menos de 3 segundos.

`--mock-calls` es el plan B nivel 2: reproduce un audio grabado y publica los
mismos eventos. Probadlo de verdad.
"""

import argparse

SCENARIO_DEFAULT = "wildfire_ridge"


def parse_args() -> argparse.Namespace:
    """`--scenario`, `--mock-calls`, `--speed`, `--no-minecraft` (plan B nivel 3)."""
    raise NotImplementedError


async def run_demo(scenario_id: str, mock_calls: bool) -> None:
    """Arranca el gateway, lanza el run y sigue la línea temporal del backbone."""
    raise NotImplementedError


def main() -> None:
    raise NotImplementedError


if __name__ == "__main__":
    main()
