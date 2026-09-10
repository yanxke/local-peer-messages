from __future__ import annotations

import hashlib
import time
from typing import Any

from integration_lab.control import AppControl, ControlError


def _friend(snapshot: dict[str, Any], peer_id: str) -> bool:
    return any(friend.get("peerId") == peer_id for friend in snapshot.get("friends", []))


def _ready_count(snapshot: dict[str, Any], peer_id: str) -> int:
    return sum(
        peer.get("peerId") == peer_id and peer.get("state") == "ready"
        for peer in snapshot.get("peers", [])
    )


def run_duplicate_link_chat(
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
        lambda snapshot: snapshot.get("runtimeReady")
        and snapshot.get("discoveryActive"),
        "runtime/discovery readiness",
        timeout,
    )
    second.wait_for(
        lambda snapshot: snapshot.get("runtimeReady")
        and snapshot.get("discoveryActive"),
        "runtime/discovery readiness",
        timeout,
    )
    primary_peer_id = first.snapshot()["localPeerId"]
    secondary_peer_id = second.snapshot()["localPeerId"]

    endpoint: dict[str, Any] | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        endpoint = next(
            (
                item
                for item in first.snapshot().get("nearbyEndpoints", [])
                if item.get("name") == "Integration Beta"
            ),
            None,
        )
        if endpoint is not None:
            break
        time.sleep(0.25)
    if endpoint is None:
        raise ControlError(f"{primary}: did not discover the duplicate-link target")

    # Submit two user Connect actions before the first handshake completes.
    # Runtime.connect must adopt the in-flight attempt instead of opening a
    # second physical link, while the inbound path may also arrive
    # concurrently from symmetric discovery.
    first.command("connect", {"endpointId": endpoint["endpointId"]})
    first.command("connect", {"endpointId": endpoint["endpointId"]})

    pending = second.wait_for(
        lambda snapshot: bool(snapshot.get("pendingFriendRequests")),
        "friendship prompt after duplicate Connect",
        timeout,
    )
    for peer_id in pending["pendingFriendRequests"]:
        second.command("acceptFriend", {"peerId": peer_id})
    first.wait_for(
        lambda snapshot: _friend(snapshot, secondary_peer_id),
        "primary friendship after duplicate Connect",
        timeout,
    )
    second.wait_for(
        lambda snapshot: _friend(snapshot, primary_peer_id),
        "secondary friendship after duplicate Connect",
        timeout,
    )

    first_snapshot = first.wait_for(
        lambda snapshot: _ready_count(snapshot, secondary_peer_id) == 1,
        "one logical primary peer after duplicate Connect",
        timeout,
    )
    second_snapshot = second.wait_for(
        lambda snapshot: _ready_count(snapshot, primary_peer_id) == 1,
        "one logical secondary peer after duplicate Connect",
        timeout,
    )

    alpha = f"duplicate-alpha-{int(time.time())}"
    alpha_hash = hashlib.sha256(alpha.encode()).hexdigest()
    first.command("sendDirectMessage", {"peerId": secondary_peer_id, "text": alpha})
    second.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == alpha_hash
            for message in snapshot.get("messagesReceived", [])
        ),
        "message delivery after duplicate Connect",
        timeout,
    )
    beta = f"duplicate-beta-{int(time.time())}"
    beta_hash = hashlib.sha256(beta.encode()).hexdigest()
    second.command("sendDirectMessage", {"peerId": primary_peer_id, "text": beta})
    first.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == beta_hash
            for message in snapshot.get("messagesReceived", [])
        ),
        "reverse message delivery after duplicate Connect",
        timeout,
    )
    return {
        "primaryPeerId": primary_peer_id,
        "secondaryPeerId": secondary_peer_id,
        "endpoint": endpoint["endpointId"],
        "primaryReadyPeers": len(first_snapshot.get("peers", [])),
        "secondaryReadyPeers": len(second_snapshot.get("peers", [])),
        "messages": [alpha_hash, beta_hash],
    }
