from __future__ import annotations

from typing import Any

from integration_lab.control import AppControl, ControlError
from integration_lab.scenarios.pairing_chat import run_pairing_chat


def run_malformed_application(
    controls: dict[str, AppControl],
    primary: str,
    secondary: str,
    timeout: float,
) -> dict[str, Any]:
    pairing = run_pairing_chat(controls, primary, secondary, timeout)
    first = controls[primary]
    second = controls[secondary]
    primary_peer_id = pairing["primaryPeerId"]
    before = first.snapshot()

    # Send bytes that are not an application envelope, followed by a valid
    # envelope carrying an invalid direct-chat payload. Neither may become
    # visible chat content or alter the relationship state.
    second.command(
        "sendRawApplicationBytes",
        {"peerId": primary_peer_id, "hex": "0102"},
    )
    second.command(
        "sendRawApplicationBytes",
        {"peerId": primary_peer_id, "hex": "01200001ff"},
    )
    # This one is structurally valid but carries the wrong conversation_id;
    # the receiver must reject it even though the sender is a current friend.
    second.command(
        "sendRawApplicationBytes",
        {
            "peerId": primary_peer_id,
            "hex": "0120001601000000000000000000000000000000000003626164",
        },
    )
    second.command(
        "sendRawApplicationBytes",
        {"peerId": primary_peer_id, "hex": "0102001000000000000000000000000000000000"},
    )
    first.wait_for(
        lambda snapshot: any(
            "malformed" in log.lower() or "invalid direct" in log.lower() or "unmatched" in log.lower()
            for log in snapshot.get("logs", [])
        ),
        "malformed application diagnostics",
        timeout,
    )
    after = first.snapshot()
    if after.get("messageCount") != before.get("messageCount"):
        raise ControlError("malformed application data changed visible chat history")
    if after.get("friends") != before.get("friends"):
        raise ControlError("stale friendship response changed FriendRecords")
    return {
        "pairing": pairing,
        "malformedEnvelopeIgnored": True,
        "malformedPayloadIgnored": True,
        "wrongConversationIdIgnored": True,
        "staleResponseIgnored": True,
    }
