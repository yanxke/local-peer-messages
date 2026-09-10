from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from integration_lab.control import AppControl, ControlError
from integration_lab.scenarios.pairing_chat import run_pairing_chat


def _group(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    value = snapshot.get("group")
    return value if isinstance(value, dict) else None


def _converged(first: dict[str, Any], second: dict[str, Any]) -> bool:
    left, right = _group(first), _group(second)
    if left is None or right is None:
        return False
    return (
        left.get("state") == "ready"
        and right.get("state") == "ready"
        and left.get("applicationGroupId") == right.get("applicationGroupId")
        and left.get("lpcGroupId") == right.get("lpcGroupId")
        and left.get("coordinatorPeerId") is not None
        and left.get("coordinatorPeerId") == right.get("coordinatorPeerId")
        and len(left.get("members", [])) == 2
        and len(right.get("members", [])) == 2
        and left.get("isCoordinator") != right.get("isCoordinator")
    )


def run_group_chat(
    controls: dict[str, AppControl],
    primary: str,
    secondary: str,
    timeout: float,
) -> dict[str, Any]:
    pairing = run_pairing_chat(controls, primary, secondary, timeout)
    first = controls[primary]
    second = controls[secondary]
    primary_peer_id = first.snapshot()["localPeerId"]
    secondary_peer_id = second.snapshot()["localPeerId"]

    # Create the group only on the creator, then use the actual application
    # invite path. This exercises FRIEND-record admission, the visible
    # Join/Decline decision, and GroupSession's authenticated merge together.
    application_group_id = uuid.uuid4().hex
    join_token = uuid.uuid4().hex
    group_arguments = {
        "groupId": application_group_id,
        "joinToken": join_token,
        "maxPeers": 2,
    }
    first.command("createGroup", group_arguments)
    first.command(
        "sendGroupInvite",
        {"peerId": secondary_peer_id, **group_arguments},
    )
    second.wait_for(
        lambda snapshot: primary_peer_id in snapshot.get("pendingGroupInvites", []),
        "incoming Group Demo invite",
        timeout,
    )
    second.command("acceptGroupInvite", {"peerId": primary_peer_id})

    first_group = first.wait_for(
        lambda snapshot: _converged(snapshot, second.snapshot()),
        "one shared group coordinator",
        timeout,
    )
    second_group = second.snapshot()
    left = _group(first_group)
    right = _group(second_group)
    if left is None or right is None:
        raise ControlError("group snapshots disappeared after convergence")
    coordinator = left["coordinatorPeerId"]

    first_text = f"group-alpha-{int(time.time())}"
    first_hash = hashlib.sha256(first_text.encode()).hexdigest()
    first.command("sendGroupMessage", {"text": first_text})
    second.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == first_hash
            for message in snapshot.get("groupMessagesReceived", [])
        ),
        "primary-to-secondary group message delivery",
        timeout,
    )

    second_text = f"group-beta-{int(time.time())}"
    second_hash = hashlib.sha256(second_text.encode()).hexdigest()
    second.command("sendGroupMessage", {"text": second_text})
    first.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == second_hash
            for message in snapshot.get("groupMessagesReceived", [])
        ),
        "secondary-to-primary group message delivery",
        timeout,
    )
    return {
        "pairing": pairing,
        "applicationGroupId": application_group_id,
        "lpcGroupId": left["lpcGroupId"],
        "coordinatorPeerId": coordinator,
        "inviteAccepted": True,
        "messages": [first_hash, second_hash],
    }
