from __future__ import annotations

import concurrent.futures
import time
from typing import Any

from integration_lab.control import AppControl, ControlError
from integration_lab.scenarios.pairing_chat import run_pairing_chat


def _traffic_text(size: int, direction: str, sequence: int) -> str:
    """Return an ASCII payload with exactly ``size`` UTF-8 bytes."""
    prefix = f"lpm-fixed-{direction}-{sequence}-"
    if len(prefix) > size:
        raise ValueError("traffic marker is larger than the requested payload")
    return prefix + ("x" if direction == "out" else "y") * (size - len(prefix))


def _send_one(
    control: AppControl,
    peer_id: str,
    text: str,
    sequence: int,
) -> bool:
    try:
        result = control.command(
            "sendDirectMessage",
            {"peerId": peer_id, "text": text},
            timeout=30,
        )
        response = result.get("result")
        state = response.get("sendState") if isinstance(response, dict) else None
        if state not in ("remoteAcknowledged", "sentToTransport"):
            raise ControlError(
                f"{control.device_name}: traffic message {sequence} returned {state}"
            )
        return True
    except (ControlError, OSError, TimeoutError):
        return False


def _run_direction(
    control: AppControl,
    peer_id: str,
    direction: str,
    message_size: int,
    messages_per_second: float,
    duration_seconds: int,
    max_pending: int = 4,
) -> dict[str, Any]:
    """Drive one bounded fixed-rate stream from the host-side test harness.

    The executor bounds application sends in flight. The schedule is based on
    monotonic host time, not on completion of the previous send, so a slow
    transport is recorded as loss/backpressure rather than silently lowering
    the requested rate.
    """
    interval = 1.0 / messages_per_second
    end = time.monotonic() + duration_seconds
    next_send = time.monotonic()
    submitted = 0
    acked = 0
    failed = 0
    futures: dict[concurrent.futures.Future[bool], int] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_pending) as pool:
        while futures or time.monotonic() < end:
            now = time.monotonic()
            while now < end and len(futures) < max_pending and now >= next_send:
                sequence = submitted
                future = pool.submit(
                    _send_one,
                    control,
                    peer_id,
                    _traffic_text(message_size, direction, sequence),
                    sequence,
                )
                futures[future] = sequence
                submitted += 1
                next_send += interval
                now = time.monotonic()

            if not futures:
                time.sleep(min(0.05, max(0.001, end - now)))
                continue

            done, _ = concurrent.futures.wait(
                tuple(futures),
                timeout=min(0.05, max(0.001, next_send - now))
                if now < end
                else 0.05,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                futures.pop(future, None)
                try:
                    if future.result():
                        acked += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1

    return {
        "messageSize": message_size,
        "messagesPerSecond": messages_per_second,
        "durationSeconds": duration_seconds,
        "maxPending": max_pending,
        "sent": submitted,
        "acked": acked,
        "failed": failed,
        "pending": 0,
        "bytesSent": submitted * message_size,
        "bytesAcked": acked * message_size,
    }


def run_fixed_rate_chat(
    controls: dict[str, AppControl],
    primary: str,
    secondary: str,
    timeout: float,
    *,
    message_size: int = 64,
    messages_per_second: float = 5,
    duration_seconds: int = 30,
) -> dict[str, Any]:
    """Run simultaneous bounded reliable traffic in both directions."""
    if message_size < 32 or message_size > 4096:
        raise ValueError("message_size must be between 32 and 4096 bytes")
    if messages_per_second <= 0 or messages_per_second > 20:
        raise ValueError("messages_per_second must be between 0 and 20")
    if duration_seconds < 1:
        raise ValueError("duration_seconds must be positive")

    pairing = run_pairing_chat(controls, primary, secondary, timeout)
    first, second = controls[primary], controls[secondary]
    primary_peer_id = pairing["primaryPeerId"]
    secondary_peer_id = pairing["secondaryPeerId"]
    # Symmetric discovery can briefly keep a losing physical GATT link alive
    # after the authenticated logical peer is READY. Let LPC finish its
    # bounded duplicate-link arbitration before introducing sustained traffic.
    settle_deadline = time.monotonic() + max(45, timeout)
    stable_since: float | None = None
    while time.monotonic() < settle_deadline:
        left, right = first.snapshot(), second.snapshot()
        ready = any(
            peer.get("peerId") == secondary_peer_id and peer.get("state") == "ready"
            for peer in left.get("peers", [])
        ) and any(
            peer.get("peerId") == primary_peer_id and peer.get("state") == "ready"
            for peer in right.get("peers", [])
        )
        if ready:
            if stable_since is None:
                stable_since = time.monotonic()
            if time.monotonic() - stable_since >= 5:
                break
        else:
            stable_since = None
        time.sleep(0.5)
    else:
        raise ControlError("peer did not stabilize after pairing before traffic")

    # The scheduler and all traffic accounting intentionally live here. The
    # app exposes only sendDirectMessage, so this profile can be reused by
    # LPM, LPGE, and an LPC-only fixture without app-specific timer behavior.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        primary_future = pool.submit(
            _run_direction,
            first,
            secondary_peer_id,
            "out",
            message_size,
            messages_per_second,
            duration_seconds,
        )
        secondary_future = pool.submit(
            _run_direction,
            second,
            primary_peer_id,
            "in",
            message_size,
            messages_per_second,
            duration_seconds,
        )
        traffic = {
            primary: primary_future.result(),
            secondary: secondary_future.result(),
        }

    expected = int(duration_seconds * messages_per_second)
    minimum = max(1, expected - 2)
    for name, stats in traffic.items():
        sent = int(stats["sent"])
        acked = int(stats["acked"])
        failed = int(stats["failed"])
        if sent < minimum:
            raise ControlError(f"{name}: sent only {sent}/{expected} messages: {stats}")
        if acked + failed != sent or (failed / sent if sent else 1) >= 0.10:
            raise ControlError(f"{name}: traffic loss too high: {stats}")

    left, right = first.snapshot(), second.snapshot()
    if not any(
        peer.get("peerId") == secondary_peer_id and peer.get("state") == "ready"
        for peer in left.get("peers", [])
    ) or not any(
        peer.get("peerId") == primary_peer_id and peer.get("state") == "ready"
        for peer in right.get("peers", [])
    ):
        raise ControlError(f"peer left READY during fixed-rate traffic: {traffic}")

    return {
        "pairing": pairing,
        "durationSeconds": duration_seconds,
        "messageSize": message_size,
        "messagesPerSecond": messages_per_second,
        "traffic": traffic,
    }
