from __future__ import annotations

import uuid
from typing import Any

from integration_lab.control import AppControl
from integration_lab.scenarios.pairing_chat import run_pairing_chat


def run_rejected_group_invite(
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
    group_arguments = {
        "groupId": uuid.uuid4().hex,
        "joinToken": uuid.uuid4().hex,
        "maxPeers": 2,
    }
    first.command("createGroup", group_arguments)
    first.command(
        "sendGroupInvite",
        {"peerId": secondary_peer_id, **group_arguments},
    )
    second.wait_for(
        lambda snapshot: primary_peer_id
        in snapshot.get("pendingGroupInvites", []),
        "incoming Group Demo invite to decline",
        timeout,
    )
    second.command("declineGroupInvite", {"peerId": primary_peer_id})
    second.wait_for(
        lambda snapshot: not snapshot.get("pendingGroupInvites")
        and snapshot.get("group") is None,
        "declined Group Demo invite remains inactive",
        timeout,
    )
    return {
        "pairing": pairing,
        "declined": True,
        "creatorGroupRemainsActive": first.snapshot().get("group") is not None,
    }
