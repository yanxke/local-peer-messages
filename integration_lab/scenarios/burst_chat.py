from __future__ import annotations

import hashlib
import time
from typing import Any

from integration_lab.control import AppControl
from integration_lab.scenarios.pairing_chat import run_pairing_chat


def _received_hashes(snapshot: dict[str, Any]) -> set[str]:
    return {
        str(message.get("textSha256"))
        for message in snapshot.get("messagesReceived", [])
        if message.get("textSha256")
    }


def run_burst_chat(
    controls: dict[str, AppControl],
    primary: str,
    secondary: str,
    timeout: float,
    count: int = 5,
) -> dict[str, Any]:
    pairing = run_pairing_chat(controls, primary, secondary, timeout)
    first = controls[primary]
    second = controls[secondary]
    primary_peer_id = pairing["primaryPeerId"]
    secondary_peer_id = pairing["secondaryPeerId"]
    sent: dict[str, list[str]] = {primary: [], secondary: []}

    # Do not wait for each SendHandle here. Submit a short burst to exercise
    # LPC's ordered queue and the app's message-stream serialization. The
    # receiver predicate still waits for every exact payload hash.
    for index in range(count):
        first_text = f"burst-alpha-{int(time.time())}-{index}"
        first_hash = hashlib.sha256(first_text.encode()).hexdigest()
        first.command(
            "sendDirectMessage", {"peerId": secondary_peer_id, "text": first_text}
        )
        sent[primary].append(first_hash)

        second_text = f"burst-beta-{int(time.time())}-{index}"
        second_hash = hashlib.sha256(second_text.encode()).hexdigest()
        second.command(
            "sendDirectMessage", {"peerId": primary_peer_id, "text": second_text}
        )
        sent[secondary].append(second_hash)

    second.wait_for(
        lambda snapshot: set(sent[primary]).issubset(_received_hashes(snapshot)),
        "all primary burst messages at secondary",
        timeout,
    )
    first.wait_for(
        lambda snapshot: set(sent[secondary]).issubset(_received_hashes(snapshot)),
        "all secondary burst messages at primary",
        timeout,
    )
    return {"pairing": pairing, "countPerDirection": count, "messages": sent}
