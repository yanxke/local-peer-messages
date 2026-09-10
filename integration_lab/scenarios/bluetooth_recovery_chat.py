from __future__ import annotations

import hashlib
import time
from typing import Any

from integration_lab.control import AppControl, ControlError
from integration_lab.device import Device
from integration_lab.scenarios.pairing_chat import run_pairing_chat


def _has_ready_peer(snapshot: dict[str, Any], peer_id: str) -> bool:
    return any(
        peer.get("peerId") == peer_id and peer.get("state") == "ready"
        for peer in snapshot.get("peers", [])
    )


def run_bluetooth_recovery_chat(
    controls: dict[str, AppControl],
    devices: dict[str, Device],
    primary: str,
    secondary: str,
    timeout: float,
) -> dict[str, Any]:
    pairing = run_pairing_chat(controls, primary, secondary, timeout)
    first = controls[primary]
    second = controls[secondary]
    primary_peer_id = pairing["primaryPeerId"]
    secondary_peer_id = pairing["secondaryPeerId"]
    interrupted = False

    if devices[primary].config.platform != "android":
        raise ControlError("bluetooth_recovery_chat requires an Android primary participant")

    # Use the host-controlled Android Bluetooth switch to create a genuine
    # platform transport interruption. Always restore it even when an
    # assertion fails, so a failed test cannot strand the development device.
    devices[primary].set_bluetooth_enabled(False)
    try:
        try:
            second.wait_for(
                lambda snapshot: not _has_ready_peer(snapshot, primary_peer_id),
                "remote peer to observe Bluetooth interruption",
                min(timeout, 30),
            )
            interrupted = True
        except ControlError:
            # Some Android stacks delay the disconnect callback until the
            # controller resumes. Re-enable below, then require successful
            # recovery; the final state remains the authoritative assertion.
            pass
    finally:
        devices[primary].set_bluetooth_enabled(True)

    if not interrupted:
        raise ControlError(
            "secondary never observed the Android Bluetooth interruption"
        )

    first.wait_for(
        lambda snapshot: snapshot.get("runtimeReady") is True
        and snapshot.get("discoveryActive") is True,
        "Android runtime/discovery after Bluetooth recovery",
        timeout,
    )
    first.wait_for(
        lambda snapshot: _has_ready_peer(snapshot, secondary_peer_id),
        "primary automatic reconnect after Bluetooth recovery",
        timeout,
    )
    second.wait_for(
        lambda snapshot: _has_ready_peer(snapshot, primary_peer_id),
        "secondary automatic reconnect after Bluetooth recovery",
        timeout,
    )
    # A ready snapshot means the logical peer is usable, but the platform
    # transport may still be completing its first post-recovery frame flush.
    # Give both runtimes a short stable interval before exercising messaging;
    # otherwise this test can race the terminal close of the old GATT session.
    time.sleep(5)

    alpha = f"bluetooth-alpha-{int(time.time())}"
    alpha_hash = hashlib.sha256(alpha.encode()).hexdigest()
    first.command(
        "sendDirectMessage",
        {"peerId": secondary_peer_id, "text": alpha},
        timeout=30,
    )
    second.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == alpha_hash
            for message in snapshot.get("messagesReceived", [])
        ),
        "message delivery after Bluetooth recovery",
        timeout,
    )
    beta = f"bluetooth-beta-{int(time.time())}"
    beta_hash = hashlib.sha256(beta.encode()).hexdigest()
    second.command(
        "sendDirectMessage",
        {"peerId": primary_peer_id, "text": beta},
        timeout=30,
    )
    first.wait_for(
        lambda snapshot: any(
            message.get("textSha256") == beta_hash
            for message in snapshot.get("messagesReceived", [])
        ),
        "reverse message delivery after Bluetooth recovery",
        timeout,
    )
    return {
        "pairing": pairing,
        "interruptionObservedBySecondary": interrupted,
        "messages": [alpha_hash, beta_hash],
    }
