"""`voice` · P3 · telefonía. HappyRobot hacia fuera, Humanlike hacia dentro.

P3 nunca toca el estado: solo emite `world.fact.asserted`. La traducción de
`CallFacts` a `Fact` es suya, con el mapa de claves que le da P1
(`contracts.factkeys`).
"""

from contracts.calls import CallFacts, CallRequest, Fact
from voice.webhooks import router

__all__ = ["VoiceGateway", "router"]


class VoiceGateway:
    """La superficie pública del paquete. El core no sabe que existe HappyRobot y
    no debe saberlo."""

    async def place_call(self, req: CallRequest) -> str:
        """Devuelve el `call_id` en cuanto la plataforma acepta, no cuando la
        llamada termina. El resultado llega por evento: nadie espera a una llamada
        de forma bloqueante."""
        raise NotImplementedError

    async def extract(self, transcript: str) -> CallFacts | None:
        """`fenic.semantic.extract` sobre un DataFrame de una fila. None si falla o
        si tarda más de 4 s: la transcripción cruda va al dashboard sin extraer y
        la demo continúa."""
        raise NotImplementedError

    def to_facts(self, cf: CallFacts, t_sim: float, call_id: str) -> list[Fact]:
        """Un `CallFacts` produce de 0 a N `Fact`. Claves de
        `contracts.factkeys`, `source="call:<call_id>"`.

        Ningún hecho aparece en pantalla sin decir de qué llamada viene."""
        raise NotImplementedError
