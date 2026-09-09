# Real-device integration lab

This runner exercises the same debug messenger build on one Android and one
iOS device. It controls the app through a loopback HTTP server exposed only in
debug mode, using a USB port forward:

- Android: `adb forward`
- iOS: `iproxy` from `libimobiledevice`

The app never exposes this control server in a release build. The control API
does not bypass LPC authentication; friendship and chat actions still use the
normal messenger protocol.

## Setup

```sh
brew install libimobiledevice
flutter pub get
```

Edit [device_inventory.yaml](device_inventory.yaml) when the connected device
serials change. Android uses its ADB serial; iOS uses the phone UDID shown by
`flutter devices` or `xcrun devicectl list devices`.

## Run

From the repository root:

```sh
python3 -m integration_lab.runner --scenario pairing_chat
```

The runner builds the Android APK and signed iOS debug app, installs and
launches both, resets only messenger test state, and runs authenticated pairing
followed by bidirectional direct chat. `--skip-build` reuses existing artifacts.

Each run creates `artifacts/<run-id>/` with scenario metadata, artifact hashes,
snapshots, structured events, and platform logs. Device lock files under
`artifacts/.locks/` prevent two runs from using the same physical device.

The app control API is intentionally small and is useful for adding scenarios:

```text
GET  /health
GET  /snapshot
GET  /events?after=<sequence>
POST /command {"action": "...", "arguments": {...}}
```

Supported commands are `getSnapshot`, `resetTestState`, `setDisplayName`,
`startNearby`, `connect`, `acceptFriend`, `declineFriend`,
`sendDirectMessage`, and `disconnectPeer`.

The smoke runner requires the devices to be unlocked, paired with the host,
have Bluetooth permissions granted, and be close enough for discovery. A
failure is reported with the last snapshots and collected logs rather than
being retried as if it were an infrastructure failure.
