from __future__ import annotations

import hashlib
import time
from typing import Any

from integration_lab.control import AppControl, ControlError


def _friend(snapshot: dict[str, Any], peer_id: str) -> bool:
    return any(friend.get("peerId") == peer_id for friend in snapshot.get("friends", []))


def _has_ready_peer(snapshot: dict[str, Any], peer_id: str) -> bool:
    return any(
        peer.get("peerId") == peer_id and peer.get("state") == "ready"
        for peer in snapshot.get("peers", [])
    )


def run_pairing_chat(
    controls: dict[str, AppControl],
    primary: str,
    secondary: str,
    timeout: float,
    *,
    accept_friend: bool = True,
    reset_state: bool = True,
    primary_name: str = "Integration Alpha",
    secondary_name: str = "Integration Beta",
) -> dict[str, Any]:
    first = controls[primary]
    second = controls[secondary]
    if reset_state:
        first.command("resetTestState")
        second.command("resetTestState")
    first.command("setDisplayName", {"name": primary_name})
    second.command("setDisplayName", {"name": secondary_name})
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

    first_snapshot = first.snapshot()
    second_snapshot = second.snapshot()
    primary_peer_id = first_snapshot["localPeerId"]
    secondary_peer_id = second_snapshot["localPeerId"]

    # Symmetric discovery can complete an authenticated connection before the
    # user-directed Connect action is processed. Reuse that logical peer when
    # possible; opening another GATT link creates an endpoint-busy race on
    # Android and is especially common when iOS uses different central and
    # peripheral endpoint identifiers.
    requester = None
    responder = None
    if _has_ready_peer(first_snapshot, secondary_peer_id):
        requester, responder = first, second
    elif _has_ready_peer(second_snapshot, primary_peer_id):
        requester, responder = second, first
    reused_ready_connection = requester is not None

    deadline = time.monotonic() + timeout
    endpoint: dict[str, Any] | None = None
    if requester is None:
        while time.monotonic() < deadline:
            snapshot = first.snapshot()
            endpoint = next(
                (
                    item
                    for item in snapshot.get("nearbyEndpoints", [])
                    if item.get("name") == secondary_name
                ),
                None,
            )
            if endpoint:
                break
            time.sleep(0.25)
        if endpoint is None:
            raise ControlError(f"{primary}: did not discover authenticated secondary endpoint")
        first.command("connect", {"endpointId": endpoint["endpointId"]})
        requester, responder = first, second

    # The requester/responder roles are now fixed, including the fallback
    # explicit Connect path above.
    requester_peer_id = (
        primary_peer_id if requester is first else secondary_peer_id
    )
    responder_peer_id = (
        secondary_peer_id if requester is first else primary_peer_id
    )
    if reused_ready_connection:
        requester.command("requestFriendship", {"peerId": responder_peer_id})

    pending = responder.wait_for(
        lambda s: bool(s.get("pendingFriendRequests")),
        "incoming friendship prompt",
        timeout,
    )
    pending_peer_id = pending["pendingFriendRequests"][0]
    if pending_peer_id != requester_peer_id:
        raise ControlError(
            f"{responder.device_name}: friendship request came from unexpected peer "
            f"{pending_peer_id}, expected {requester_peer_id}"
        )
    responder.command(
        "acceptFriend" if accept_friend else "declineFriend",
        {"peerId": pending_peer_id},
    )
    if not accept_friend:
        requester.wait_for(
            lambda s: not s.get("pendingFriendResponses")
            and not s.get("friends"),
            "requester friendship rejection",
            timeout,
        )
        responder.wait_for(
            lambda s: not s.get("pendingFriendRequests")
            and not s.get("friends"),
            "rejected friendship remains absent",
            timeout,
        )
        return {
            "primaryPeerId": primary_peer_id,
            "secondaryPeerId": secondary_peer_id,
            "rejected": True,
        }
    first_snapshot = requester.wait_for(
        lambda s: bool(s.get("friends")) and bool(s.get("localPeerId")),
        "requester friendship record",
        timeout,
    )
    responder.wait_for(
        lambda s: _friend(s, requester_peer_id),
        "responder friendship record",
        timeout,
    )
    requester.wait_for(
        lambda s: _friend(s, responder_peer_id),
        "requester friendship record",
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
