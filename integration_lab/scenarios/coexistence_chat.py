from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from integration_lab.control import AppControl, ControlError
from integration_lab.scenarios.group_chat import run_group_chat
from integration_lab.scenarios.pairing_chat import _friend, _has_ready_peer


def run_coexistence_chat(
    controls: dict[str, AppControl],
    primary: str,
    secondary: str,
    timeout: float,
) -> dict[str, Any]:
    # group_chat establishes the friendship and the real invite/accept path,
    # then leaves both Host/Discovery and GroupSession logically active.
    group = run_group_chat(controls, primary, secondary, timeout)
    first = controls[primary]
    second = controls[secondary]
    primary_peer_id = group["pairing"]["primaryPeerId"]
    secondary_peer_id = group["pairing"]["secondaryPeerId"]

    direct_text = f"coexistence-direct-{int(time.time())}"
    direct_hash = hashlib.sha256(direct_text.encode()).hexdigest()
    first.command(
        "sendDirectMessage",
        {"peerId": secondary_peer_id, "text": direct_text},
    )
    second.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == direct_hash
            for message in snapshot.get("messagesReceived", [])
        ),
        "direct delivery while Group Demo is active",
        timeout,
    )

    # Releasing GroupSession must not release the direct friendship's logical
    # ownership of the shared physical connection.
    first.command("leaveGroup")
    first.wait_for(
        lambda snapshot: snapshot.get("group") is None
        and _friend(snapshot, secondary_peer_id)
        and _has_ready_peer(snapshot, secondary_peer_id),
        "direct friendship survives Group Demo leave",
        timeout,
    )
    second.command("leaveGroup")

    after_leave_text = f"coexistence-after-leave-{int(time.time())}"
    after_leave_hash = hashlib.sha256(after_leave_text.encode()).hexdigest()
    second.command(
        "sendDirectMessage",
        {"peerId": primary_peer_id, "text": after_leave_text},
    )
    first.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == after_leave_hash
            for message in snapshot.get("messagesReceived", [])
        ),
        "direct delivery after Group Demo leave",
        timeout,
    )

    # Start a fresh ephemeral group, then remove the direct friendship while
    # GroupSession is still an owner. Group traffic must remain usable even
    # though direct application authorization has been removed.
    # Use distinct valid 16-byte values for the second invite.
    second_group_id = uuid.uuid4().hex
    second_join_token = uuid.uuid4().hex
    second.command(
        "createGroup",
        {
            "groupId": second_group_id,
            "joinToken": second_join_token,
            "maxPeers": 2,
        },
    )
    second.command(
        "sendGroupInvite",
        {
            "peerId": primary_peer_id,
            "groupId": second_group_id,
            "joinToken": second_join_token,
            "maxPeers": 2,
        },
    )
    first.wait_for(
        lambda snapshot: secondary_peer_id
        in snapshot.get("pendingGroupInvites", []),
        "second Group Demo invite for ownership test",
        timeout,
    )
    first.command("acceptGroupInvite", {"peerId": secondary_peer_id})
    first.wait_for(
        lambda snapshot: snapshot.get("group", {}).get("state") == "ready",
        "second Group Demo ready before friendship removal",
        timeout,
    )
    first.command("removeFriendLocalOnly", {"peerId": secondary_peer_id})
    first.wait_for(
        lambda snapshot: not _friend(snapshot, secondary_peer_id),
        "friendship removal while Group Demo owns the link",
        timeout,
    )
    group_text = f"coexistence-group-after-remove-{int(time.time())}"
    group_hash = hashlib.sha256(group_text.encode()).hexdigest()
    second.command("sendGroupMessage", {"text": group_text})
    first.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == group_hash
            for message in snapshot.get("groupMessagesReceived", [])
        ),
        "Group Demo delivery after friendship retention release",
        timeout,
    )

    first_peers = first.snapshot().get("peers", [])
    second_peers = second.snapshot().get("peers", [])
    if len(first_peers) != 1 or len(second_peers) != 1:
        raise ControlError(
            "coexistence created duplicate logical peer connections: "
            f"primary={len(first_peers)} secondary={len(second_peers)}"
        )
    return {
        "group": group,
        "directWhileGroup": direct_hash,
        "directAfterLeave": after_leave_hash,
        "groupAfterFriendRemoval": group_hash,
        "singleLogicalPeer": True,
    }
