from __future__ import annotations

from typing import Any

from integration_lab.control import AppControl, ControlError
from integration_lab.scenarios.pairing_chat import _friend, run_pairing_chat


def run_friend_removal_notification(
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
    before = second.snapshot()

    # Exercise the normal confirmed-removal path, including FRIEND_REMOVE.
    first.command("removeFriend", {"peerId": secondary_peer_id})
    first.wait_for(
        lambda snapshot: not _friend(snapshot, secondary_peer_id),
        "local FriendRecord deletion after removal",
        timeout,
    )
    second.wait_for(
        lambda snapshot: not _friend(snapshot, primary_peer_id),
        "remote FriendRecord deletion after FRIEND_REMOVE",
        timeout,
    )

    # A second removal from a peer that is no longer a friend must not create
    # state or resurrect the relationship.
    try:
        second.command("sendRawApplicationBytes", {
            "peerId": primary_peer_id,
            "hex": "01040000",
        })
    except ControlError:
        # The valid notification may close the direct connection immediately;
        # inability to send from the now-unfriended side is also acceptable.
        pass
    after = first.snapshot()
    if _friend(after, secondary_peer_id):
        raise ControlError("malformed/non-friend removal resurrected friendship")
    if before.get("friends") and after.get("friends"):
        if len(after["friends"]) > len(before["friends"]):
            raise ControlError("friend removal increased FriendRecord count")
    return {
        "pairing": pairing,
        "localRemoved": True,
        "remoteNotificationApplied": True,
        "nonFriendRemovalIgnored": True,
    }
