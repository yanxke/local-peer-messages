from __future__ import annotations

import json
import os
import re
import signal
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
        self._launcher_process: subprocess.Popen[str] | None = None

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

    def restart(self, artifact: Path) -> None:
        """Restart the app while keeping the device identity and test port."""
        raise NotImplementedError

    def set_bluetooth_enabled(self, enabled: bool) -> None:
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
        if self._launcher_process is not None:
            # `flutter run --machine` exits when its command stream reaches
            # EOF. Keep that stream open for the scenario, then close it
            # before terminating the process group so Flutter can shut down
            # its device session cleanly.
            if self._launcher_process.stdin is not None:
                try:
                    self._launcher_process.stdin.close()
                except OSError:
                    pass
            try:
                if os.name == "posix":
                    os.killpg(os.getpgid(self._launcher_process.pid), signal.SIGTERM)
                else:
                    self._launcher_process.terminate()
            except ProcessLookupError:
                pass
            try:
                self._launcher_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "posix":
                        os.killpg(os.getpgid(self._launcher_process.pid), signal.SIGKILL)
                    else:
                        self._launcher_process.kill()
                except ProcessLookupError:
                    pass
            self._launcher_process = None
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

    def restart(self, artifact: Path) -> None:
        self.stop()
        self.launch()
        self.start_bridge()

    def set_bluetooth_enabled(self, enabled: bool) -> None:
        command = "enable" if enabled else "disable"
        try:
            self._adb("shell", "svc", "bluetooth", command)
            return
        except DeviceError as error:
            if not enabled:
                raise
            # Recent Samsung Android builds allow shell disable but reject
            # shell enable with status=-1 while the adapter is BLE_ON. The
            # supported user-facing settings switch still works, and the lab
            # requires devices to be unlocked for Flutter anyway.
            self._adb("shell", "am", "start", "-a", "android.settings.BLUETOOTH_SETTINGS")
            self._adb("shell", "uiautomator", "dump", "/sdcard/integration-lab-ui.xml")
            xml = self._adb("shell", "cat", "/sdcard/integration-lab-ui.xml").stdout
            match = re.search(
                r'resource-id="com\.android\.settings:id/(?:sesl_)?switchbar_container"'
                r'.*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
                xml,
            )
            if match is None:
                raise DeviceError(
                    f"{self.name}: Bluetooth shell enable failed and settings switch "
                    "could not be located"
                ) from error
            left, top, right, bottom = (int(value) for value in match.groups())
            self._adb("shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self._adb("shell", "settings", "get", "global", "bluetooth_on").stdout.strip() == "1":
                    return
                time.sleep(0.5)
            raise DeviceError(
                f"{self.name}: Bluetooth settings switch did not enable the adapter"
            ) from error

    def start_bridge(self) -> None:
        # `adb forward --remove` exits non-zero when this is the first run or
        # a previous runner already cleaned up the listener.  Treat that
        # expected absence as idempotent, while preserving real ADB errors.
        try:
            self._adb("forward", "--remove", f"tcp:{self.host_port}")
        except DeviceError as error:
            if "listener 'tcp:" not in str(error) or "not found" not in str(error):
                raise
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
    def __init__(self, config: DeviceConfig, host_port: int):
        super().__init__(config, host_port)
        self._launcher_log_path: Path | None = None

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

    def _stop_orphan_flutter_launchers(self) -> None:
        """Stop device-specific helpers that outlive a Flutter tool process."""
        patterns = (
            "devicectl device process launch --device " + self.config.serial,
            "flutter_tools.snapshot run --machine --device-id " + self.config.serial,
        )
        for pattern in patterns:
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                text=True,
                capture_output=True,
                timeout=5,
            )
            for raw_pid in result.stdout.splitlines():
                try:
                    pid = int(raw_pid)
                except ValueError:
                    continue
                if pid == os.getpid():
                    continue
                try:
                    # Flutter launches each runner in its own session. Kill
                    # the session so its frontend server and USB proxies do
                    # not survive after the top-level runner exits.
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                for _ in range(10):
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.1)
                else:
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def close(self) -> None:
        # CoreDevice can orphan its process when Flutter loses the USB
        # session. Clean up this exact device before the next run so an old
        # debug process cannot keep the control port or app alive.
        self._stop_orphan_flutter_launchers()
        super().close()

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

    def prepare(self, artifact: Path) -> None:
        if not self.connected():
            raise DeviceError(f"{self.name}: device is not connected and authorized")
        # A debug Flutter app cannot be reliably installed/launched through a
        # standalone devicectl install followed by process launch. Flutter's
        # attached runner must own the install and debug-session handshake.
        self.wake()
        self.launch()
        self.start_bridge()
        try:
            self._wait_for_current_flutter_launch()
        except DeviceError as first_error:
            # CoreDevice occasionally leaves Flutter at "Installing and
            # launching..." after a USB/device-service reconnect. A fresh
            # Flutter runner clears that stale install transaction and is
            # safe because the app has not passed the current app.started
            # readiness boundary yet.
            self.close()
            try:
                self.launch()
                self.start_bridge()
                self._wait_for_current_flutter_launch()
            except DeviceError as retry_error:
                raise DeviceError(
                    f"{self.name}: iOS Flutter launch failed after retry; "
                    f"first={first_error}; retry={retry_error}"
                ) from retry_error

    def _wait_for_current_flutter_launch(self, timeout: float = 120) -> None:
        """Wait for this Flutter runner, rather than a prior app instance.

        The app control port is intentionally stable, so a previous debug
        process can answer health checks while the new runner is still
        installing. Waiting for the current runner's app.started event closes
        that race and makes an early historical "Exiting..." line irrelevant.
        """
        if self._launcher_log_path is None or self._launcher_process is None:
            raise DeviceError(f"{self.name}: Flutter launcher was not started")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            output = self._launcher_log_path.read_text(encoding="utf-8")
            if '"event":"app.started"' in output:
                return
            if self._launcher_process.poll() is not None:
                raise DeviceError(
                    f"{self.name}: current Flutter launcher exited before app.started:\n"
                    f"{output[-4000:]}"
                )
            time.sleep(0.25)
        output = self._launcher_log_path.read_text(encoding="utf-8")
        raise DeviceError(
            f"{self.name}: timed out waiting for current Flutter app.started:\n"
            f"{output[-4000:]}"
        )

    def launch(self) -> None:
        # Terminate any app instance left by a previous Flutter/Xcode session
        # before starting this launch. Otherwise the control bridge can attach
        # to an old process while the new flutter tool is still starting, and
        # its delayed "Exiting..." output is easy to misread as this launch.
        self._stop_orphan_flutter_launchers()
        try:
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
        except DeviceError:
            # The app is normally not running on the first launch. Keep the
            # actual Flutter launch error visible through the readiness wait.
            pass

        # iOS refuses to launch a debug Flutter app through devicectl; it must
        # be launched and kept attached by Flutter tooling (or Xcode). Keep
        # this process alive for the scenario so the debug VM and app control
        # server remain active while iproxy forwards the test commands.
        self._launcher_log_path = Path(
            f"/tmp/integration-lab-ios-launch-{self.config.serial}.log"
        )
        launcher_log = self._launcher_log_path.open("w", encoding="utf-8")
        self._launcher_process = subprocess.Popen(
            [
                "flutter",
                "run",
                "--machine",
                "--device-id",
                self.config.serial,
                "--debug",
                "--no-pub",
            ],
            cwd=Path(__file__).resolve().parents[1],
            stdout=launcher_log,
            stderr=subprocess.STDOUT,
        # Machine mode uses stdin as a command stream. An inherited or
        # closed stdin can look like EOF and make Flutter print
        # "Exiting..." immediately, which drops the app's control server.
        stdin=subprocess.PIPE,
        text=True,
        start_new_session=True,
        )
        launcher_log.close()
        # A launcher that exits immediately is a launch failure, not a stale
        # device-log message. Fail early with the current launch's output.
        time.sleep(0.5)
        if self._launcher_process.poll() is not None:
            details = self._launcher_log_path.read_text(encoding="utf-8")
            raise DeviceError(
                f"{self.name}: Flutter launcher exited immediately:\n{details[-4000:]}"
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

    def restart(self, artifact: Path) -> None:
        # A Flutter debug process must be relaunched through Flutter tooling;
        # devicectl can terminate the app but cannot start a debug app from the
        # home screen on iOS 14+. Reuse prepare so the new runner gets a fresh
        # app.started marker and cannot inherit stale launcher output.
        self.close()
        self.prepare(artifact)

    def set_bluetooth_enabled(self, enabled: bool) -> None:
        raise DeviceError("iOS Bluetooth power control is not available through the lab")

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
        sections: list[str] = []
        if self._launcher_log_path is not None:
            launcher_output = self._launcher_log_path.read_text(encoding="utf-8")
            sections.append(
                "=== Flutter launcher output (current runner launch) ===\n"
                + launcher_output
            )
        syslog = shutil.which("idevicesyslog")
        if syslog is None:
            sections.append(
                "=== iOS device log adapter unavailable ===\n"
                "Install libimobiledevice to collect idevicesyslog output."
            )
        else:
            process = subprocess.Popen(
                [syslog, "-u", self.config.serial, "-p", "Runner", "--no-colors"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                logs, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired as error:
                # idevicesyslog can keep a USB helper child alive after its
                # parent receives SIGTERM. Kill this capture process group so
                # cleanup cannot hang an otherwise complete test run.
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    logs, _ = process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    logs, _ = process.communicate(timeout=3)
                if not logs:
                    logs = str(error.output or "")
            sections.append("=== iOS device log capture ===\n" + (logs or ""))
        output.write_text("\n\n".join(sections) + "\n", encoding="utf-8")

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
