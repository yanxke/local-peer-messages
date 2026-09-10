from __future__ import annotations

from typing import Any

from integration_lab.control import AppControl
from integration_lab.scenarios.pairing_chat import run_pairing_chat


def run_rejected_pairing(
    controls: dict[str, AppControl],
    primary: str,
    secondary: str,
    timeout: float,
) -> dict[str, Any]:
    """Verify a direct user pairing rejection has no relationship side effect."""
    return run_pairing_chat(
        controls,
        primary,
        secondary,
        timeout,
        accept_friend=False,
    )
