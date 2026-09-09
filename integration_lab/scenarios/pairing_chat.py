from __future__ import annotations

import hashlib
import time
from typing import Any

from integration_lab.control import AppControl, ControlError


def _friend(snapshot: dict[str, Any], peer_id: str) -> bool:
    return any(friend.get("peerId") == peer_id for friend in snapshot.get("friends", []))


def run_pairing_chat(
    controls: dict[str, AppControl],
    primary: str,
    secondary: str,
    timeout: float,
) -> dict[str, Any]:
    first = controls[primary]
    second = controls[secondary]
    first.command("resetTestState")
    second.command("resetTestState")
    first.command("setDisplayName", {"name": "Integration Alpha"})
    second.command("setDisplayName", {"name": "Integration Beta"})
    first.command("startNearby")
    second.command("startNearby")
    first.wait_for(
        lambda s: s.get("runtimeReady") and s.get("discoveryActive"),
        "runtime/discovery readiness",
        timeout,
    )
    second.wait_for(
        lambda s: s.get("runtimeReady") and s.get("discoveryActive"),
        "runtime/discovery readiness",
        timeout,
    )

    deadline = time.monotonic() + timeout
    endpoint: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        snapshot = first.snapshot()
        endpoint = next(
            (
                item
                for item in snapshot.get("nearbyEndpoints", [])
                if item.get("name") == "Integration Beta"
            ),
            None,
        )
        if endpoint:
            break
        time.sleep(0.25)
    if endpoint is None:
        raise ControlError(f"{primary}: did not discover authenticated secondary endpoint")

    first.command("connect", {"endpointId": endpoint["endpointId"]})
    pending = second.wait_for(
        lambda s: bool(s.get("pendingFriendRequests")),
        "incoming friendship prompt",
        timeout,
    )
    secondary_peer_id = pending["pendingFriendRequests"][0]
    second.command("acceptFriend", {"peerId": secondary_peer_id})
    first_snapshot = first.wait_for(
        lambda s: bool(s.get("friends")) and bool(s.get("localPeerId")),
        "primary friendship record",
        timeout,
    )
    primary_peer_id = first_snapshot["localPeerId"]
    second.wait_for(
        lambda s: _friend(s, primary_peer_id),
        "secondary friendship record",
        timeout,
    )
    first.wait_for(
        lambda s: _friend(s, secondary_peer_id),
        "primary friendship record",
        timeout,
    )

    alpha_text = f"integration-alpha-{int(time.time())}"
    alpha_hash = hashlib.sha256(alpha_text.encode()).hexdigest()
    first.command(
        "sendDirectMessage",
        {"peerId": secondary_peer_id, "text": alpha_text},
    )
    second.wait_for(
        lambda s: any(m.get("textSha256") == alpha_hash for m in s.get("messagesReceived", [])),
        "primary-to-secondary message delivery",
        timeout,
    )

    beta_text = f"integration-beta-{int(time.time())}"
    beta_hash = hashlib.sha256(beta_text.encode()).hexdigest()
    second.command(
        "sendDirectMessage",
        {"peerId": primary_peer_id, "text": beta_text},
    )
    first.wait_for(
        lambda s: any(m.get("textSha256") == beta_hash for m in s.get("messagesReceived", [])),
        "secondary-to-primary message delivery",
        timeout,
    )
    return {
        "primaryPeerId": primary_peer_id,
        "secondaryPeerId": secondary_peer_id,
        "messages": [alpha_hash, beta_hash],
    }
