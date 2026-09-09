from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


APP_CONTROL_PORT = 8765
ANDROID_PACKAGE = "com.example.local_peer_messages"
IOS_BUNDLE_ID = "com.example.localPeerMessages"


class DeviceError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceConfig:
    name: str
    platform: str
    serial: str
    role: str


class Device:
    def __init__(self, config: DeviceConfig, host_port: int):
        self.config = config
        self.host_port = host_port
        self._bridge_process: subprocess.Popen[str] | None = None

    @property
    def name(self) -> str:
        return self.config.name

    def run(self, args: list[str], timeout: float = 30) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                args,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DeviceError(f"{self.name}: command failed: {' '.join(args)}: {error}") from error
        if result.returncode:
            raise DeviceError(
                f"{self.name}: {' '.join(args)} exited {result.returncode}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    def connected(self) -> bool:
        raise NotImplementedError

    def install(self, artifact: Path) -> None:
        raise NotImplementedError

    def launch(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def prepare(self, artifact: Path) -> None:
        if not self.connected():
            raise DeviceError(f"{self.name}: device is not connected and authorized")
        self.install(artifact)
        self.wake()
        self.launch()
        self.start_bridge()

    def wake(self) -> None:
        """Wake/unlock controls are platform-specific best effort actions."""

    def start_bridge(self) -> None:
        raise NotImplementedError

    def collect_logs(self, output: Path) -> None:
        output.write_text(
            f"No platform log adapter is available for {self.name} ({self.config.platform}).\n",
            encoding="utf-8",
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "platform": self.config.platform,
            "serial": self.config.serial,
            "role": self.config.role,
            "hostPort": self.host_port,
        }

    def close(self) -> None:
        if self._bridge_process is not None:
            self._bridge_process.terminate()
            try:
                self._bridge_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._bridge_process.kill()
            self._bridge_process = None


class AndroidDevice(Device):
    def _adb(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        return self.run(["adb", "-s", self.config.serial, *args], timeout=timeout)

    def connected(self) -> bool:
        result = self._adb("get-state")
        return result.stdout.strip() == "device"

    def install(self, artifact: Path) -> None:
        self._adb("install", "-r", str(artifact), timeout=120)

    def launch(self) -> None:
        self._adb("shell", "am", "force-stop", ANDROID_PACKAGE)
        self._adb("shell", "am", "start", "-n", f"{ANDROID_PACKAGE}/.MainActivity")

    def wake(self) -> None:
        self._adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")

    def stop(self) -> None:
        self._adb("shell", "am", "force-stop", ANDROID_PACKAGE)

    def start_bridge(self) -> None:
        self._adb("forward", "--remove", f"tcp:{self.host_port}")
        self._adb("forward", f"tcp:{self.host_port}", f"tcp:{APP_CONTROL_PORT}")

    def collect_logs(self, output: Path) -> None:
        result = self._adb("logcat", "-d", "-v", "threadtime", timeout=30)
        output.write_text(result.stdout, encoding="utf-8")

    def metadata(self) -> dict[str, Any]:
        result = self._adb("shell", "getprop")
        props: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if ":[" not in line or "]: [" not in line:
                continue
            key, value = line.split("]: [", 1)
            props[key.lstrip("[")] = value.rstrip("]")
        data = super().metadata()
        data.update(
            {
                "model": props.get("ro.product.model"),
                "osVersion": props.get("ro.build.version.release"),
                "apiLevel": props.get("ro.build.version.sdk"),
            }
        )
        return data


class IosDevice(Device):
    def connected(self) -> bool:
        temp = Path(f"/tmp/integration-lab-{self.config.serial}.json")
        try:
            self.run(
                [
                    "xcrun",
                    "devicectl",
                    "list",
                    "devices",
                    "--json-output",
                    str(temp),
                ],
                timeout=30,
            )
            data = json.loads(temp.read_text(encoding="utf-8"))
            return any(
                d.get("hardwareProperties", {}).get("udid") == self.config.serial
                and d.get("connectionProperties", {}).get("pairingState") == "paired"
                and d.get("deviceProperties", {}).get("developerModeStatus") == "enabled"
                for d in data.get("result", {}).get("devices", [])
            )
        finally:
            temp.unlink(missing_ok=True)

    def install(self, artifact: Path) -> None:
        self.run(
            [
                "xcrun",
                "devicectl",
                "device",
                "install",
                "app",
                "--device",
                self.config.serial,
                str(artifact),
            ],
            timeout=180,
        )

    def launch(self) -> None:
        self.run(
            [
                "xcrun",
                "devicectl",
                "device",
                "process",
                "launch",
                "--device",
                self.config.serial,
                "--terminate-existing",
                IOS_BUNDLE_ID,
            ],
            timeout=60,
        )

    def stop(self) -> None:
        self.run(
            [
                "xcrun",
                "devicectl",
                "device",
                "process",
                "terminate",
                "--device",
                self.config.serial,
                IOS_BUNDLE_ID,
            ],
            timeout=30,
        )

    def wake(self) -> None:
        # devicectl can launch an app on a paired device, but does not expose a
        # portable lock-screen dismissal operation. The runner reports a clear
        # launch failure if the phone is locked instead of trying UI guesses.
        return

    def start_bridge(self) -> None:
        iproxy = shutil.which("iproxy")
        if iproxy is None:
            raise DeviceError(
                "ios device: iproxy is required for the USB control channel; install "
                "libimobiledevice with `brew install libimobiledevice`"
            )
        self._bridge_process = subprocess.Popen(
            [iproxy, f"{self.host_port}:{APP_CONTROL_PORT}", "-u", self.config.serial],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.5)
        if self._bridge_process.poll() is not None:
            error = self._bridge_process.stderr.read() if self._bridge_process.stderr else ""
            raise DeviceError(f"ios device: iproxy failed to start: {error}")

    def collect_logs(self, output: Path) -> None:
        syslog = shutil.which("idevicesyslog")
        if syslog is None:
            return super().collect_logs(output)
        process = subprocess.Popen(
            [syslog, "-u", self.config.serial, "-p", "Runner", "--no-colors"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            logs, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired as error:
            process.terminate()
            logs, _ = process.communicate(timeout=3)
            if not logs:
                logs = str(error.output or "")
        output.write_text(logs or "", encoding="utf-8")

    def metadata(self) -> dict[str, Any]:
        data = super().metadata()
        data.update({"bundleId": IOS_BUNDLE_ID})
        return data


def make_device(config: DeviceConfig, host_port: int) -> Device:
    if config.platform == "android":
        return AndroidDevice(config, host_port)
    if config.platform == "ios":
        return IosDevice(config, host_port)
    raise DeviceError(f"{config.name}: unsupported platform {config.platform}")
