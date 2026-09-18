"""El mock de telefonía. Lo escribe P3 el viernes.

Acepta `CallRequest`, espera 8 segundos y publica un `CallResult` con una
transcripción de fichero (`fixtures/transcripts/`). Es también la base del
`--mock-calls` del plan B, así que probadlo de verdad: el domingo puede ser lo
único que suene.
"""

from pathlib import Path

from contracts.calls import CallRequest, CallResult

FAKE_DELAY_S = 8.0
TRANSCRIPTS_DIR = Path("fixtures/transcripts")


class FakeVoice:
    """Misma superficie que `VoiceGateway`. P2 y P4 no notan la diferencia."""

    def __init__(self, transcripts_dir: Path = TRANSCRIPTS_DIR) -> None:
        raise NotImplementedError

    async def place_call(self, req: CallRequest) -> str:
        """Devuelve un `call_id` sintético al instante y publica `call.ended` a
        los 8 segundos."""
        raise NotImplementedError

    def canned_result(self, req: CallRequest) -> CallResult:
        """La transcripción de fichero que corresponde a ese `intent`."""
        raise NotImplementedError
