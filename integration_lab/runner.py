from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from xml.sax.saxutils import escape

from integration_lab.control import AppControl, ControlError
from integration_lab.device import DeviceConfig, DeviceError, make_device
from integration_lab.scenarios.pairing_chat import run_pairing_chat


ROOT = Path(__file__).resolve().parents[1]
APP_ID = "com.example.local_peer_messages"


def _parse_inventory(path: Path) -> dict[str, DeviceConfig]:
    # Keep the inventory dependency-free. This deliberately supports the small
    # scalar YAML subset used by the checked-in lab inventory.
    devices: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line or line.strip() == "devices:":
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 2 and line.endswith(":"):
            current = line.strip()[:-1]
            devices[current] = {}
            continue
        if indent == 4 and current and ":" in line:
            key, value = line.strip().split(":", 1)
            devices[current][key] = value.strip().strip("'\"")
    return {
        name: DeviceConfig(
            name=name,
            platform=values["platform"],
            serial=values["serial"],
            role=values.get("role", "participant"),
        )
        for name, values in devices.items()
    }


def _command(args: list[str], timeout: float = 600) -> None:
    print(f"$ {' '.join(args)}", flush=True)
    try:
        subprocess.run(args, cwd=ROOT, check=True, timeout=timeout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"build command failed: {error}") from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _build(platforms: set[str]) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    if "android" in platforms:
        _command(["flutter", "build", "apk", "--debug"])
        artifacts["android"] = ROOT / "build/app/outputs/flutter-apk/app-debug.apk"
    if "ios" in platforms:
        _command(["flutter", "build", "ios", "--debug"])
        artifacts["ios"] = ROOT / "build/ios/iphoneos/Runner.app"
    for platform, artifact in artifacts.items():
        if not artifact.exists():
            raise RuntimeError(f"expected {platform} artifact missing: {artifact}")
    return artifacts


@contextmanager
def _device_lock(path: Path, name: str) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RuntimeError(f"device {name} is locked by another integration run: {path}") from error
    try:
        os.write(descriptor, f"pid={os.getpid()} time={time.time()}\n".encode())
        os.close(descriptor)
        yield
    finally:
        path.unlink(missing_ok=True)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_junit(path: Path, result: dict[str, Any], elapsed: float) -> None:
    failure = result.get("error")
    body = ""
    if failure:
        body = f'<failure message="{escape(str(failure))}">{escape(str(failure))}</failure>'
    path.write_text(
        '<testsuite name="local-peer-messages.integration" tests="1" '
        f'failures="{1 if failure else 0}" time="{elapsed:.3f}">'
        f'<testcase classname="{escape(str(result.get("scenario", "unknown")))}" '
        f'name="{escape(str(result.get("runId", "run")))}" time="{elapsed:.3f}">' 
        f"{body}</testcase></testsuite>\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LPC Demo Messenger real-device tests")
    parser.add_argument("--inventory", type=Path, default=ROOT / "integration_lab/device_inventory.yaml")
    parser.add_argument("--scenario", choices=["pairing_chat"], default="pairing_chat")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--artifacts-dir", type=Path, default=ROOT / "artifacts")
    parser.add_argument("--skip-build", action="store_true")
    options = parser.parse_args()

    configs = _parse_inventory(options.inventory)
    selected = [config for config in configs.values() if config.role in {"primary", "secondary"}]
    if len(selected) != 2:
        raise RuntimeError("inventory must contain exactly one primary and one secondary device")
    if {config.platform for config in selected} != {"android", "ios"}:
        raise RuntimeError("the smoke scenario requires one Android and one iOS participant")
    primary = next(config for config in selected if config.role == "primary")
    secondary = next(config for config in selected if config.role == "secondary")
    run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{options.scenario}-{uuid.uuid4().hex[:8]}"
    artifact_dir = options.artifacts_dir / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    devices = {
        primary.name: make_device(primary, 18765),
        secondary.name: make_device(secondary, 18766),
    }
    controls: dict[str, AppControl] = {}
    result: dict[str, Any] = {"runId": run_id, "scenario": options.scenario, "status": "running"}
    started_at = time.monotonic()
    _write_json(artifact_dir / "scenario.json", result)
    locks = [options.artifacts_dir / ".locks" / f"{config.serial}.lock" for config in selected]
    try:
        with _device_lock(locks[0], selected[0].name), _device_lock(locks[1], selected[1].name):
            artifacts = _build({config.platform for config in selected}) if not options.skip_build else {
                "android": ROOT / "build/app/outputs/flutter-apk/app-debug.apk",
                "ios": ROOT / "build/ios/iphoneos/Runner.app",
            }
            for device in devices.values():
                device.prepare(artifacts[device.config.platform])
                controls[device.name] = AppControl(
                    f"http://127.0.0.1:{device.host_port}", device.name
                )
            deadline = time.monotonic() + 30
            for name, control in controls.items():
                while True:
                    try:
                        control.health()
                        break
                    except ControlError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.5)
                control.wait_for(
                    lambda snapshot: snapshot.get("runtimeReady") is True
                    and snapshot.get("discoveryActive") is True,
                    "runtime and discovery readiness",
                    30,
                )
            result["devices"] = [device.metadata() for device in devices.values()]
            result["artifacts"] = {
                platform: {"path": str(path), "sha256": _sha256(path)}
                for platform, path in artifacts.items()
            }
            if options.scenario == "pairing_chat":
                outcome = run_pairing_chat(controls, primary.name, secondary.name, options.timeout)
            else:
                raise RuntimeError(f"unsupported scenario {options.scenario}")
            result.update({"status": "passed", "outcome": outcome})
            return_code = 0
    except (ControlError, DeviceError, RuntimeError) as error:
        result.update({"status": "failed", "error": str(error)})
        print(f"Scenario failed: {error}", file=sys.stderr, flush=True)
        return_code = 1
    finally:
        for name, control in controls.items():
            try:
                _write_json(artifact_dir / f"{name}.snapshot.json", control.snapshot())
                _write_json(artifact_dir / f"{name}.events.json", control.events())
            except Exception as error:  # artifacts should not mask the scenario result
                (artifact_dir / f"{name}.artifact-error.txt").write_text(str(error), encoding="utf-8")
        for name, device in devices.items():
            try:
                device.collect_logs(artifact_dir / f"{name}.log")
            except Exception as error:
                (artifact_dir / f"{name}.log-error.txt").write_text(str(error), encoding="utf-8")
            device.close()
        _write_json(artifact_dir / "scenario.json", result)
        _write_junit(artifact_dir / "result.xml", result, time.monotonic() - started_at)
        print(f"Artifacts: {artifact_dir}", flush=True)
    return return_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"integration lab error: {error}", file=sys.stderr)
        raise SystemExit(2)
