from __future__ import annotations

from typing import Any

from integration_lab.control import AppControl
from integration_lab.scenarios.pairing_chat import _friend, run_pairing_chat


def run_repeated_friend_request(
    controls: dict[str, AppControl],
    primary: str,
    secondary: str,
    timeout: float,
) -> dict[str, Any]:
    pairing = run_pairing_chat(controls, primary, secondary, timeout)
    first = controls[primary]
    second = controls[secondary]
    primary_peer_id = pairing["primaryPeerId"]
    secondary_peer_id = pairing["secondaryPeerId"]

    # Send a second request over the existing authenticated connection. Alice
    # remains a friend and must automatically accept without another prompt.
    second.command(
        "requestFriendship",
        {"peerId": primary_peer_id, "force": True},
    )

    first.wait_for(
        lambda snapshot: _friend(snapshot, secondary_peer_id),
        "existing friendship remains after repeated request",
        timeout,
    )
    second.wait_for(
        lambda snapshot: _friend(snapshot, primary_peer_id),
        "repeated request is idempotently accepted",
        timeout,
    )
    if first.snapshot().get("pendingFriendRequests"):
        raise RuntimeError("idempotent repeated request left a friendship prompt")
    return {
        "pairing": pairing,
        "receiverFriendshipPreserved": True,
        "requesterFriendshipRestored": True,
        "promptRequired": False,
    }
