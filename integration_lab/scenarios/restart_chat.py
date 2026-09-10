from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from integration_lab.control import AppControl, ControlError
from integration_lab.device import Device
from integration_lab.scenarios.pairing_chat import run_pairing_chat


def _has_friend(snapshot: dict[str, Any], peer_id: str) -> bool:
    return any(friend.get("peerId") == peer_id for friend in snapshot.get("friends", []))


def _has_ready_peer(snapshot: dict[str, Any], peer_id: str) -> bool:
    return any(
        peer.get("peerId") == peer_id and peer.get("state") == "ready"
        for peer in snapshot.get("peers", [])
    )


def _wait_for_runtime(control: AppControl, timeout: float) -> dict[str, Any]:
    return control.wait_for(
        lambda snapshot: snapshot.get("runtimeReady") is True
        and snapshot.get("discoveryActive") is True,
        "restarted runtime and discovery readiness",
        timeout,
    )


def run_restart_chat(
    controls: dict[str, AppControl],
    devices: dict[str, Device],
    artifacts: dict[str, Path],
    primary: str,
    secondary: str,
    timeout: float,
) -> dict[str, Any]:
    pairing = run_pairing_chat(controls, primary, secondary, timeout)
    first = controls[primary]
    second = controls[secondary]
    primary_peer_id = pairing["primaryPeerId"]
    secondary_peer_id = pairing["secondaryPeerId"]

    # Restart the secondary participant as a whole app process. This preserves
    # its LPC identity and persisted friend record, while exercising the same
    # startup path that previously produced misleading iOS "Exiting..." logs.
    devices[secondary].restart(artifacts[devices[secondary].config.platform])
    _wait_for_runtime(second, max(120, timeout))
    second.wait_for(
        lambda snapshot: _has_friend(snapshot, primary_peer_id),
        "persisted friendship after app restart",
        timeout,
    )

    first.wait_for(
        lambda snapshot: _has_ready_peer(snapshot, secondary_peer_id),
        "primary automatic reconnect after secondary app restart",
        timeout,
    )
    second.wait_for(
        lambda snapshot: _has_ready_peer(snapshot, primary_peer_id),
        "secondary automatic reconnect after app restart",
        timeout,
    )

    first_text = f"restart-alpha-{int(time.time())}"
    first_hash = hashlib.sha256(first_text.encode()).hexdigest()
    first.command(
        "sendDirectMessage", {"peerId": secondary_peer_id, "text": first_text}
    )
    second.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == first_hash
            for message in snapshot.get("messagesReceived", [])
        ),
        "message delivery after app restart reconnect",
        timeout,
    )

    second_text = f"restart-beta-{int(time.time())}"
    second_hash = hashlib.sha256(second_text.encode()).hexdigest()
    second.command(
        "sendDirectMessage", {"peerId": primary_peer_id, "text": second_text}
    )
    first.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == second_hash
            for message in snapshot.get("messagesReceived", [])
        ),
        "reverse message delivery after app restart reconnect",
        timeout,
    )

    final_first = first.snapshot()
    final_second = second.snapshot()
    if not _has_ready_peer(final_first, secondary_peer_id) or not _has_ready_peer(
        final_second, primary_peer_id
    ):
        raise ControlError("peer was not ready after restart-chat message exchange")
    return {
        "pairing": pairing,
        "restarted": secondary,
        "messages": [first_hash, second_hash],
    }
