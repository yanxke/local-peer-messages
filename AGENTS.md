Implement the app according to lpc_demo_messenger_spec.md.
Keep a list of implemented an unimplemented features.

When launching the debug version of the app on iOS devices, it could take
a long time to load the app, and the previously loaded version of the app
might take a while to quit for the new version to launch.

Applications should not be hot restarted because it can cause duplicate endpoints.

## Testing and physical-device operations

The messenger specification in `lpc_demo_messenger_spec.md` is the source of
truth for application behavior. The LPC specification and implementation live
in the sibling `local-peer-connections` checkout. Do not make the messenger
work around LPC ownership, discovery, reconnect/RESUME, fragmentation,
backpressure, transport selection, or coordinator behavior; diagnose and fix
those concerns at the appropriate layer.

Before finishing changes, run:

```sh
flutter pub get
flutter analyze
flutter test
```

The dependency should point at `local-peer-connections` while validating a
local LPC change. Restore the normal Git dependency only when the task calls
for release/dependency verification. Keep `FEATURES.md` up to date with the
implemented and unimplemented/dependent feature list.

### Real-device test workflow

The checked-in `integration_lab` runner is the supported LPM reliability test
harness. It uses stable USB device handles from
`integration_lab/device_inventory.yaml`, never Bluetooth addresses as
identity. Confirm devices and permissions first:

```sh
flutter devices
adb devices -l
xcrun devicectl list devices
```

Run the basic authenticated pairing/chat check with:

```sh
python3 -m integration_lab.runner --scenario pairing_chat
```

Reliability coverage includes:

```sh
python3 -m integration_lab.runner --scenario fixed_rate_chat \
  --traffic-seconds 30 --message-size 64 --messages-per-second 5
python3 -m integration_lab.runner --scenario mixed_payload_chat
python3 -m integration_lab.runner --scenario restart_chat
python3 -m integration_lab.runner --scenario friend_removal_notification
python3 -m integration_lab.runner --scenario removed_friend_authorization
```

Use `burst_chat` for queue/message-stream ordering, `duplicate_link_chat` for
simultaneous Connect and duplicate physical-link ownership, and
`bluetooth_recovery_chat` for Android radio interruption/recovery. The latter
is environment-dependent. `coexistence_chat`, `group_chat`, and
`coordinator_migration` cover Group Demo ownership and coordinator recovery;
the migration scenario requires a third inventory device. Use `--skip-build`
only when the existing debug artifacts are known to match the current source.

Every run must leave its `artifacts/<run-id>/` metadata, snapshots, structured
events, JUnit result, and device logs available for diagnosis. Record the
device roles, start/end times, connection and reconnect events, sent/received
message counts, payload sizes, bandwidth/rate, and loss. Investigate unstable
connections, stalled transfers, repeated `ENDPOINT_BUSY`, or unexpected
duplicate logical peers using the captured LPC and native BLE/GATT diagnostics;
do not hide them with an infinite retry loop.

### Connect, reconnect, and cleanup rules

The app must keep one symmetric LPC HostSession and DiscoverySession active.
The application does not choose BLE central/peripheral roles, arbitrate
transport direction, maintain a reconnect scheduler, or map platform endpoint
IDs to persistent friends. Automatic known-peer probing and reconnect belong to
LPC. A manual Connect action may adopt an in-flight LPC attempt, but must not
start a competing connection.

For restart scenarios, preserve the installed app and its data so the LPC
PeerId and FriendRecords survive. The runner performs an app-process restart
and waits for the new control server/runtime; it does not hot restart Flutter.
Do not uninstall, clear application data, reboot a connected device, or start a
second launch session merely to reset a test. Reset the messenger runtime and
ephemeral Group Demo state through the harness, then call `leaveGroup` or
`resetTestState` before the next scenario. No durable message outbox is
expected.

On Android, prefer an in-place update:

```sh
adb -s <serial> install -r build/app/outputs/flutter-apk/app-debug.apk
adb -s <serial> forward tcp:<host-port> tcp:8766
```

On iOS, allow extra time for Flutter/CoreDevice. Keep `iproxy` running for the
control port, for example:

```sh
iproxy <host-port>:8766 -u <ios-udid>
```

`flutter build ios --debug --no-codesign` produces only an unsigned compile
artifact; CoreDevice rejects it for physical deployment. Use `flutter run`
with automatic development signing, an IDE with the Flutter plugin, or Xcode
to deploy the debug messenger. Keep `--no-codesign` for compile-only checks and
CI artifacts, not installation.

If deployment hangs, inspect `ps aux | rg 'flutter run|devicectl|Runner.app|iproxy'`
and terminate only the stale, device-specific Flutter/deployment/Runner
process. On iOS 14+, debug Flutter apps must be launched through `flutter run`,
an IDE with the Flutter plugin, or Xcode; direct CoreDevice/devicectl launch
is rejected. An in-place `xcrun devicectl device install app` can still be
used for installation when appropriate, but not as the debug-app launch
fallback. Do not reboot the phone, broadly kill CoreDevice, or stop a
successful deployment session just to inspect it. If macOS Keychain authorization is requested for LPC identity
storage, approve it (prefer “Always Allow”); `runtimeReady: false` while that
prompt is present is an authorization wait, not a Bluetooth failure. A
`MissingPluginException` for LPC seed loading requires a rebuild with the
platform plugin registered, never a fake seed or no-op success.
If Flutter reports an Automation permission request, approve it in macOS
Settings. Do not start a second Flutter launch session over an already-running
messenger app; clean up only the stale, device-specific wrapper or app process
before retrying.

After each physical run, release all test-created LPM state on every device:
leave the Group Demo, stop any traffic producer, and use `resetTestState` while
preserving the installation and identity. There are no game-engine rows to
delete in this project; the equivalent cleanup is removing the ephemeral
GroupSession and clearing app-level test state. Do not report a scenario as
passed until its cleanup and artifact collection have completed.
