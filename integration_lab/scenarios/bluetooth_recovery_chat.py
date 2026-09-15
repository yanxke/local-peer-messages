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


def _wait_for_stable_ready(
    first: Any,
    second: Any,
    first_peer_id: str,
    second_peer_id: str,
    timeout: float,
    stable_seconds: float = 5.0,
) -> None:
    """Wait until both sides stay READY across the post-toggle handoff."""
    deadline = time.monotonic() + timeout
    stable_since: float | None = None
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        left = first.snapshot()
        right = second.snapshot()
        last = {"primary": left, "secondary": right}
        ready = _has_ready_peer(left, second_peer_id) and _has_ready_peer(
            right, first_peer_id
        )
        if ready:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= stable_seconds:
                return
        else:
            stable_since = None
        time.sleep(0.25)
    raise ControlError(
        "both peers did not remain READY after Bluetooth recovery; "
        f"last snapshot={last}"
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
    _wait_for_stable_ready(
        first,
        second,
        primary_peer_id,
        secondary_peer_id,
        timeout,
    )

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
