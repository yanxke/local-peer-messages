from __future__ import annotations

import uuid
from typing import Any

from integration_lab.control import AppControl
from integration_lab.scenarios.group_chat import _group, _converged
from integration_lab.scenarios.pairing_chat import run_pairing_chat


def run_duplicate_group_invite(
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
    arguments = {
        "groupId": uuid.uuid4().hex,
        "joinToken": uuid.uuid4().hex,
        "maxPeers": 2,
    }
    first.command("createGroup", arguments)
    # Duplicate delivery before the recipient decides must produce one
    # pending prompt, not two concurrent GroupSessions or dialogs.
    for _ in range(2):
        first.command(
            "sendGroupInvite",
            {"peerId": secondary_peer_id, **arguments},
        )
    second.wait_for(
        lambda snapshot: snapshot.get("pendingGroupInvites", []).count(primary_peer_id) == 1,
        "one coalesced Group Demo invite",
        timeout,
    )
    second.command("acceptGroupInvite", {"peerId": primary_peer_id})
    first_group = first.wait_for(
        lambda snapshot: _converged(snapshot, second.snapshot()),
        "Group Demo convergence after duplicate invite",
        timeout,
    )
    second_group = second.snapshot()
    first.command("sendGroupInvite", {"peerId": secondary_peer_id, **arguments})
    second.wait_for(
        lambda snapshot: not snapshot.get("pendingGroupInvites")
        and _group(snapshot) is not None,
        "active Group Demo ignores duplicate invite",
        timeout,
    )
    return {
        "pairing": pairing,
        "pendingInviteCount": 1,
        "groupId": _group(first_group)["lpcGroupId"],
        "sameGroupAfterDuplicate": _group(second_group)["lpcGroupId"]
        == _group(first_group)["lpcGroupId"],
    }
