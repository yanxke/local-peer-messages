from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable


class ControlError(RuntimeError):
    pass


class AppControl:
    def __init__(self, base_url: str, device_name: str):
        self.base_url = base_url.rstrip("/")
        self.device_name = device_name
        self.event_seq = 0

    def request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method="POST" if data is not None else "GET",
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ControlError(f"{self.device_name}: {path}: {error}") from error
        if not isinstance(result, dict) or result.get("ok") is False:
            raise ControlError(f"{self.device_name}: {path}: {result}")
        return result

    def health(self) -> dict[str, Any]:
        return self.request("/health")

    def snapshot(self) -> dict[str, Any]:
        return self.request("/snapshot")

    def command(self, action: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request(
            "/command",
            {"action": action, "arguments": arguments or {}},
        )

    def events(self) -> list[dict[str, Any]]:
        result = self.request(f"/events?after={self.event_seq}")
        events = result.get("events", [])
        if events:
            self.event_seq = max(int(event["seq"]) for event in events)
        return events

    def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        description: str,
        timeout: float,
        interval: float = 0.25,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.snapshot()
            if predicate(last):
                return last
            time.sleep(interval)
        raise ControlError(
            f"{self.device_name}: timed out waiting for {description}; last snapshot={last}"
        )
