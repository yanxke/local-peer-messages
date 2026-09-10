from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from integration_lab.control import AppControl, ControlError
from integration_lab.device import Device
from integration_lab.scenarios.pairing_chat import _friend, _has_ready_peer
from integration_lab.scenarios.group_chat import run_group_chat
from integration_lab.scenarios.restart_chat import _wait_for_runtime


def run_group_restart_chat(
    controls: dict[str, AppControl],
    devices: dict[str, Device],
    artifacts: dict[str, Path],
    primary: str,
    secondary: str,
    timeout: float,
) -> dict[str, Any]:
    group = run_group_chat(controls, primary, secondary, timeout)
    first = controls[primary]
    second = controls[secondary]
    primary_peer_id = group["pairing"]["primaryPeerId"]
    secondary_peer_id = group["pairing"]["secondaryPeerId"]

    # Group Demo is intentionally ephemeral, while FriendRecords are
    # persistent. Restart the participant and verify those two lifetimes stay
    # separate while LPC reconnects the known friend.
    devices[secondary].restart(artifacts[devices[secondary].config.platform])
    _wait_for_runtime(second, max(120, timeout))
    restarted = second.wait_for(
        lambda snapshot: _friend(snapshot, primary_peer_id)
        and snapshot.get("group") is None,
        "persisted friendship and non-persisted Group Demo after restart",
        timeout,
    )
    first.wait_for(
        lambda snapshot: _has_ready_peer(snapshot, secondary_peer_id),
        "primary reconnect after Group Demo participant restart",
        timeout,
    )
    second.wait_for(
        lambda snapshot: _has_ready_peer(snapshot, primary_peer_id),
        "secondary reconnect after Group Demo participant restart",
        timeout,
    )

    text = f"group-restart-direct-{int(time.time())}"
    digest = hashlib.sha256(text.encode()).hexdigest()
    first.command(
        "sendDirectMessage",
        {"peerId": secondary_peer_id, "text": text},
    )
    second.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == digest
            for message in snapshot.get("messagesReceived", [])
        ),
        "direct delivery after ephemeral group restart",
        timeout,
    )
    if restarted.get("group") is not None:
        raise ControlError("Group Demo state was restored across app restart")
    return {
        "group": group,
        "restarted": secondary,
        "groupEphemeral": True,
        "friendshipPersisted": True,
        "message": digest,
    }
