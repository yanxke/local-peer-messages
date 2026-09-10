from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from integration_lab.control import AppControl, ControlError
from integration_lab.device import Device
from integration_lab.scenarios.pairing_chat import _has_ready_peer, run_pairing_chat
from integration_lab.scenarios.restart_chat import _wait_for_runtime


def run_offline_send_failure(
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
    before = first.snapshot()

    # Keep the target genuinely offline long enough to distinguish an
    # immediate visible failure from a hidden durable application outbox.
    devices[secondary].stop()
    first.wait_for(
        lambda snapshot: not _has_ready_peer(snapshot, secondary_peer_id),
        "target becomes offline",
        timeout,
    )
    text = f"offline-{int(time.time())}"
    digest = hashlib.sha256(text.encode()).hexdigest()
    try:
        first.command(
            "sendDirectMessage",
            {"peerId": secondary_peer_id, "text": text},
        )
    except ControlError:
        pass
    else:
        raise ControlError("offline direct send unexpectedly succeeded")
    after = first.snapshot()
    if any(
        message.get("textSha256") == digest
        for message in after.get("messagesReceived", [])
    ):
        raise ControlError("offline message appeared in local received history")
    if after.get("messageCount", 0) != before.get("messageCount", 0):
        raise ControlError("offline send changed visible chat history")
    if not any(
        "offline" in str(log).lower() or "not sent" in str(log).lower()
        for log in after.get("logs", [])
    ):
        raise ControlError("offline send produced no visible diagnostic failure")

    devices[secondary].restart(artifacts[devices[secondary].config.platform])
    _wait_for_runtime(second, max(120, timeout))
    first.wait_for(
        lambda snapshot: _has_ready_peer(snapshot, secondary_peer_id),
        "automatic reconnect after offline send",
        timeout,
    )
    second.wait_for(
        lambda snapshot: _has_ready_peer(snapshot, primary_peer_id),
        "target reconnect after offline send",
        timeout,
    )
    recovery_text = f"recovered-{int(time.time())}"
    recovery_digest = hashlib.sha256(recovery_text.encode()).hexdigest()
    first.command(
        "sendDirectMessage",
        {"peerId": secondary_peer_id, "text": recovery_text},
    )
    second.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == recovery_digest
            for message in snapshot.get("messagesReceived", [])
        ),
        "post-reconnect direct delivery",
        timeout,
    )
    return {
        "pairing": pairing,
        "offlineSendRejected": True,
        "noDurableOutboxEvidence": True,
        "reconnected": True,
        "recoveryMessage": recovery_digest,
    }
