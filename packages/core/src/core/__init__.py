"""`core` · P1 · belief · planner · solver · verifiers · divergence.

Las cinco funciones públicas son puras a propósito: se desarrollan contra
`fixtures/run_golden.jsonl` sin que exista ni el sim ni la voz.
"""

from core.belief import apply
from core.divergence import divergence
from core.loop import Core
from core.planner import plan
from core.solver import solve
from core.verifiers import verify

__all__ = ["Core", "apply", "divergence", "plan", "solve", "verify"]
