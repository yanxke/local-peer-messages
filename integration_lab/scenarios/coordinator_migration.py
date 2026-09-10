from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

from integration_lab.control import AppControl, ControlError
from integration_lab.device import Device
from integration_lab.scenarios.pairing_chat import run_pairing_chat


def _group(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    value = snapshot.get("group")
    return value if isinstance(value, dict) else None


def _three_way_ready(snapshots: list[dict[str, Any]]) -> bool:
    groups = [_group(snapshot) for snapshot in snapshots]
    if any(group is None or group.get("state") != "ready" for group in groups):
        return False
    group_ids = {group["lpcGroupId"] for group in groups if group is not None}
    coordinators = {group["coordinatorPeerId"] for group in groups if group is not None}
    return (
        len(group_ids) == 1
        and len(coordinators) == 1
        and None not in coordinators
        and all(len(group["members"]) == 3 for group in groups if group is not None)
    )


def run_coordinator_migration(
    controls: dict[str, AppControl],
    devices: dict[str, Device],
    artifacts: dict[str, Any],
    primary: str,
    secondary: str,
    tertiary: str,
    timeout: float,
) -> dict[str, Any]:
    first_pair = run_pairing_chat(controls, primary, secondary, timeout)
    second_pair = run_pairing_chat(
        controls,
        primary,
        tertiary,
        timeout,
        reset_state=False,
        secondary_name="Integration Gamma",
    )
    first = controls[primary]
    second = controls[secondary]
    third = controls[tertiary]
    peer_ids = {
        primary: first_pair["primaryPeerId"],
        secondary: first_pair["secondaryPeerId"],
        tertiary: second_pair["secondaryPeerId"],
    }
    group_arguments = {
        "groupId": uuid.uuid4().hex,
        "joinToken": uuid.uuid4().hex,
        "maxPeers": 3,
    }
    first.command("createGroup", group_arguments)
    for participant in (second, third):
        participant_id = next(
            peer_id for name, peer_id in peer_ids.items() if controls[name] is participant
        )
        first.command(
            "sendGroupInvite",
            {"peerId": participant_id, **group_arguments},
        )
        participant.wait_for(
            lambda snapshot: peer_ids[primary]
            in snapshot.get("pendingGroupInvites", []),
            "incoming three-device Group Demo invite",
            timeout,
        )
        participant.command("acceptGroupInvite", {"peerId": peer_ids[primary]})

    snapshots = [first.snapshot(), second.snapshot(), third.snapshot()]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not _three_way_ready(snapshots):
        time.sleep(0.25)
        snapshots = [first.snapshot(), second.snapshot(), third.snapshot()]
    if not _three_way_ready(snapshots):
        raise ControlError(f"three-device group did not converge: {snapshots}")
    initial_group = _group(snapshots[0])
    if initial_group is None:
        raise ControlError("initial three-device group disappeared before migration")
    old_coordinator = initial_group["coordinatorPeerId"]
    coordinator_name = next(
        name for name, peer_id in peer_ids.items() if peer_id == old_coordinator
    )

    # Stop the elected coordinator process. The two surviving GroupSessions
    # must elect a new coordinator and keep the same logical group.
    devices[coordinator_name].stop()
    survivors = [
        name for name in (primary, secondary, tertiary) if name != coordinator_name
    ]
    survivor_groups: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        survivor_groups = [_group(controls[name].snapshot()) for name in survivors]
        if (
            all(group is not None and group.get("state") == "ready" for group in survivor_groups)
            and len({group.get("coordinatorPeerId") for group in survivor_groups}) == 1
            and survivor_groups[0].get("coordinatorPeerId") != old_coordinator
            and all(len(group.get("members", [])) == 2 for group in survivor_groups)
        ):
            break
        time.sleep(0.5)
    if not survivor_groups or any(group is None for group in survivor_groups):
        raise ControlError("surviving GroupSessions did not remain ready")
    new_coordinator = survivor_groups[0]["coordinatorPeerId"]
    if new_coordinator == old_coordinator:
        raise ControlError("GroupSession did not migrate after coordinator failure")

    sender_name, receiver_name = survivors
    sender = controls[sender_name]
    receiver = controls[receiver_name]
    text = f"migration-{int(time.time())}"
    digest = hashlib.sha256(text.encode()).hexdigest()
    sender.command("sendGroupMessage", {"text": text})
    receiver.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == digest
            for message in snapshot.get("groupMessagesReceived", [])
        ),
        "group delivery after coordinator migration",
        timeout,
    )
    return {
        "pairing": [first_pair, second_pair],
        "coordinatorBefore": old_coordinator,
        "coordinatorAfter": new_coordinator,
        "failedDevice": coordinator_name,
        "survivors": survivors,
        "message": digest,
    }
