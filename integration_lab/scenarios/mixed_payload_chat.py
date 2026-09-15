from __future__ import annotations

import hashlib
import time
from typing import Any

from integration_lab.control import AppControl
from integration_lab.scenarios.pairing_chat import run_pairing_chat


def run_mixed_payload_chat(
    controls: dict[str, AppControl],
    primary: str,
    secondary: str,
    timeout: float,
) -> dict[str, Any]:
    """Send small and maximum-size normative direct messages in both directions.

    The scenario has a hard five-minute wall clock bound independent of the
    caller's polling timeout. Payload contents are represented by hashes in the
    result; message bodies are never written to artifacts.
    """
    pairing = run_pairing_chat(controls, primary, secondary, timeout)
    first, second = controls[primary], controls[secondary]
    deadline = time.monotonic() + 300
    # ChatMessageV1 uses a uint16 text length but the messenger spec limits the
    # text to 4096 UTF-8 bytes. Exercise that application boundary while still
    # forcing LPC to fragment the payload on small GATT MTUs.
    payloads = (32, 4096, 32, 4096)
    sent: dict[str, list[str]] = {primary: [], secondary: []}
    for index, size in enumerate(payloads):
        if time.monotonic() >= deadline:
            raise RuntimeError("mixed payload scenario exceeded five-minute bound")
        source, target, peer, label = (
            (first, second, pairing["secondaryPeerId"], primary)
            if index % 2 == 0
            else (second, first, pairing["primaryPeerId"], secondary)
        )
        marker = f"lpge-mixed-{index}-"
        fill = "x" if index % 2 == 0 else "y"
        text = (marker + fill * size)[:size]
        digest = hashlib.sha256(text.encode()).hexdigest()
        source.command("sendDirectMessage", {"peerId": peer, "text": text}, timeout=30)
        sent[label].append(digest)
        target.wait_for(
            lambda snapshot, digest=digest: any(
                message.get("textSha256") == digest
                for message in snapshot.get("messagesReceived", [])
            ),
            f"{size}-byte message delivery",
            min(timeout, max(1, deadline - time.monotonic())),
        )
    for control in (first, second):
        if any(peer.get("state") != "ready" for peer in control.snapshot().get("peers", [])):
            raise RuntimeError(f"{control.device_name}: connection left ready state")
    return {"pairing": pairing, "payloadSizes": list(payloads), "hashes": sent}
