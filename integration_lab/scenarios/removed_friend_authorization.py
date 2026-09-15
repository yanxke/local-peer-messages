from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from integration_lab.control import AppControl, ControlError
from integration_lab.scenarios.pairing_chat import (
    _friend,
    _has_ready_peer,
    run_pairing_chat,
)


def _has_pending_group_invite(snapshot: dict[str, Any], peer_id: str) -> bool:
    return peer_id in snapshot.get("pendingGroupInvites", [])


def _wait_for_stable_ready(
    first: AppControl,
    second: AppControl,
    first_peer_id: str,
    second_peer_id: str,
    timeout: float,
    stable_seconds: float = 5.0,
) -> None:
    """Wait for a usable transport, not only a stale READY snapshot.

    A former friend is allowed to reconnect at the LPC transport layer while
    its application authorization is absent.  During that handoff the
    control snapshot can briefly report READY after the underlying GATT link
    has already gone away.  Sending FRIEND_REQUEST in that window makes the
    command fail with ``ready peer is not connected`` and makes this test
    measure timing rather than the re-friend contract.
    """
    deadline = time.monotonic() + timeout
    stable_since: float | None = None
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        left = first.snapshot()
        right = second.snapshot()
        last = {"primary": left, "secondary": right}
        ready = any(
            peer.get("peerId") == second_peer_id and peer.get("state") == "ready"
            for peer in left.get("peers", [])
        ) and any(
            peer.get("peerId") == first_peer_id and peer.get("state") == "ready"
            for peer in right.get("peers", [])
        )
        if ready:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= stable_seconds:
                return
        else:
            stable_since = None
        time.sleep(0.25)
    raise ControlError(
        "former friends did not remain READY before re-friend; "
        f"last snapshot={last}"
    )


def run_removed_friend_authorization(
    controls: dict[str, AppControl],
    primary: str,
    secondary: str,
    timeout: float,
) -> dict[str, Any]:
    # Start from a real, mutually accepted friendship and READY connection.
    pairing = run_pairing_chat(controls, primary, secondary, timeout)
    first = controls[primary]
    second = controls[secondary]
    primary_peer_id = pairing["primaryPeerId"]
    secondary_peer_id = pairing["secondaryPeerId"]

    first.command("removeFriendLocalOnly", {"peerId": secondary_peer_id})
    first.wait_for(
        lambda snapshot: not _friend(snapshot, secondary_peer_id),
        "local FriendRecord removal",
        timeout,
    )
    second.wait_for(
        lambda snapshot: _friend(snapshot, primary_peer_id),
        "remote stale FriendRecord remains",
        timeout,
    )

    # Force the old physical/logical connection down. The second app still
    # knows Alice, so its automatic known-peer path must be allowed to
    # reconnect through Alice's always-on HostSession even though Alice no
    # longer authorizes application traffic from that peer.
    first.command("disconnectPeer", {"peerId": secondary_peer_id})
    first.wait_for(
        lambda snapshot: not _has_ready_peer(snapshot, secondary_peer_id),
        "removed peer disconnects before reconnect",
        timeout,
    )
    second.wait_for(
        lambda snapshot: not _has_ready_peer(snapshot, primary_peer_id),
        "stale peer disconnects before reconnect",
        timeout,
    )
    first.wait_for(
        lambda snapshot: _has_ready_peer(snapshot, secondary_peer_id),
        "removed peer reconnects at Alice",
        timeout,
    )
    second.wait_for(
        lambda snapshot: _has_ready_peer(snapshot, primary_peer_id),
        "removed peer reconnects at Bob",
        timeout,
    )

    blocked_direct = f"blocked-direct-{int(time.time())}"
    blocked_direct_hash = hashlib.sha256(blocked_direct.encode()).hexdigest()
    try:
        second.command(
            "sendDirectMessage",
            {"peerId": primary_peer_id, "text": blocked_direct},
        )
    except ControlError as error:
        # LPM rejects an unauthorized direct send at the control boundary;
        # that is the expected result, and is stronger than accepting a send
        # that is later observed to be absent. Keep this assertion independent
        # of the Dart control-server's exact error wording.
        if "direct message was not delivered" not in str(error):
            raise
    time.sleep(2)
    if any(
        message.get("textSha256") == blocked_direct_hash
        for message in first.snapshot().get("messagesReceived", [])
    ):
        raise ControlError("removed friend direct message was admitted")

    group_id = uuid.uuid4().hex
    join_token = uuid.uuid4().hex
    try:
        second.command(
            "sendGroupInvite",
            {
                "peerId": primary_peer_id,
                "groupId": group_id,
                "joinToken": join_token,
                "maxPeers": 2,
            },
        )
    except ControlError as error:
        # The removed friend must be rejected before any Group Demo invite is
        # admitted; the absence check below is the protocol-level assertion.
        if "Group Demo invite was not delivered" not in str(error):
            raise
    time.sleep(2)
    first_snapshot = first.snapshot()
    if _has_pending_group_invite(first_snapshot, secondary_peer_id) or first_snapshot.get(
        "group"
    ) is not None:
        raise ControlError("removed friend Group Demo invite was admitted")

    # A new FRIEND_REQUEST is the only route back to application authorization.
    # The stale side still has a FriendRecord by design, so use the
    # integration-only force flag to exercise the protocol's allowed request
    # path without changing the normal UI rule that an existing friend is not
    # offered another Connect/friend action.
    _wait_for_stable_ready(
        first,
        second,
        primary_peer_id,
        secondary_peer_id,
        timeout,
    )
    second.command(
        "requestFriendship",
        {"peerId": primary_peer_id, "force": True},
    )
    pending = first.wait_for(
        lambda snapshot: primary_peer_id not in snapshot.get("pendingFriendRequests", [])
        and bool(snapshot.get("pendingFriendRequests")),
        "re-friend request after removal",
        timeout,
    )
    pending_peer_id = pending["pendingFriendRequests"][0]
    if pending_peer_id != secondary_peer_id:
        raise ControlError(
            f"unexpected re-friend requester {pending_peer_id}, expected {secondary_peer_id}"
        )
    first.command("acceptFriend", {"peerId": secondary_peer_id})
    first.wait_for(
        lambda snapshot: _friend(snapshot, secondary_peer_id),
        "Alice re-friend record",
        timeout,
    )
    second.wait_for(
        lambda snapshot: _friend(snapshot, primary_peer_id),
        "Bob re-friend record",
        timeout,
    )

    # Accepting the request can coincide with LPC replacing the authenticated
    # transport. Wait for the replacement connection to remain usable before
    # measuring post-authorization delivery.
    _wait_for_stable_ready(
        first,
        second,
        primary_peer_id,
        secondary_peer_id,
        timeout,
    )

    allowed_direct = f"allowed-direct-{int(time.time())}"
    allowed_direct_hash = hashlib.sha256(allowed_direct.encode()).hexdigest()
    second.command(
        "sendDirectMessage",
        {"peerId": primary_peer_id, "text": allowed_direct},
    )
    first.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == allowed_direct_hash
            for message in snapshot.get("messagesReceived", [])
        ),
        "direct chat after re-friend",
        timeout,
    )

    group_id = uuid.uuid4().hex
    join_token = uuid.uuid4().hex
    second.command(
        "createGroup",
        {"groupId": group_id, "joinToken": join_token, "maxPeers": 2},
    )
    second.command(
        "sendGroupInvite",
        {
            "peerId": primary_peer_id,
            "groupId": group_id,
            "joinToken": join_token,
            "maxPeers": 2,
        },
    )
    first.wait_for(
        lambda snapshot: _has_pending_group_invite(snapshot, secondary_peer_id),
        "authorized Group Demo invite after re-friend",
        timeout,
    )
    first.command("acceptGroupInvite", {"peerId": secondary_peer_id})
    first.wait_for(
        lambda snapshot: snapshot.get("group", {}).get("state") == "ready",
        "Group Demo after re-friend",
        timeout,
    )

    return {
        "pairing": pairing,
        "blockedDirect": blocked_direct_hash,
        "blockedGroupInvite": True,
        "reconnected": True,
        "refriended": True,
        "allowedDirect": allowed_direct_hash,
        "groupInviteAccepted": True,
    }
