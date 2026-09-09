# Real-Device Integration Test Plan

## Purpose

Create a repeatable test framework for validating LPC Demo Messenger across
multiple physical Android and iOS devices. The framework should make pairing,
messaging, duplicate-link handling, process failures, Bluetooth failures, and
automatic reconnects observable and reproducible without relying on manual UI
taps or ad-hoc log inspection.

The framework is a test and diagnostics tool. It must not change production
identity, friendship, transport, or reconnect semantics.

## Goals

- Run a named scenario against two or more real devices from one host command.
- Identify devices by stable host-side device IDs such as ADB serials, never by
  Bluetooth addresses.
- Reset application state between scenarios.
- Synchronize actions and assertions across devices.
- Assert structured LPC/application events instead of scraping UI text.
- Capture enough information to reproduce and diagnose failures.
- Keep most coverage in unit and simulated multi-runtime tests, reserving real
  devices for platform and lifecycle behavior.

## Non-goals

- Replacing LPC unit tests or fake-backend tests.
- Making Bluetooth behavior perfectly deterministic in an uncontrolled office
  environment.
- Automating physical actions that the host cannot reliably control, such as
  removing a device battery or changing radio conditions.
- Exposing private keys, PSKs, raw application payloads, or friendship secrets
  in test logs.

## Proposed architecture

```text
Host test runner
    |
    +-- device inventory and exclusive device locks
    +-- build/install/reset/launch commands
    +-- per-device test-control connection
    +-- synchronized scenario state machine
    +-- assertions and timeout handling
    +-- artifact collection and JUnit output
    |
    +-- Android participant A
    +-- iOS participant B
    +-- additional real devices when required
```

Use a Python host runner initially. Python has straightforward process control,
ADB support, timeout handling, parallel device operations, and JUnit/reporting
libraries. Keep the scenario model independent of Python-specific device
commands so Android and iOS adapters can share the same scenario logic.

Suggested layout:

```text
integration_lab/
  README.md
  device_inventory.yaml
  requirements.txt
  runner.py
  adb.py
  device.py
  app_control.py
  assertions.py
  artifacts.py
  scenarios/
    pairing.py
    direct_chat.py
    duplicate_links.py
    reconnect.py
    stale_presence.py
    bluetooth_interruption.py
```

## Device inventory

Store only lab metadata in `device_inventory.yaml`:

```yaml
devices:
  peer_a:
    platform: android
    serial: <android-adb-serial>
    role: primary
  peer_b:
    platform: ios
    serial: <ios-device-udid>
    role: secondary
```

The runner must verify before every run:

- the configured device is connected and authorized;
- the reported model and OS version match the inventory expectations;
- Bluetooth is enabled;
- required permissions are granted;
- the device is not already locked by another test run;
- the screen is awake and the app can be launched.

For Android, the adapter uses ADB for installation, launch, log capture, and
the control-channel forward. For iOS, it uses a paired device-control tool for
installation/launch and `iproxy` from libimobiledevice for the control-channel
forward. iOS device builds also require a usable development signing identity
and private key.

ADB serials are stable test-lab handles. Bluetooth addresses and discovery
endpoint IDs may rotate and are only diagnostic correlation fields.

## App test-control channel

Add a debug/integration build-only control surface to the messenger app. It
should be disabled from release builds and should not bypass LPC authentication
or friendship rules.

Preferred transport:

```text
app-local JSON/WebSocket or HTTP server
        ^
        |
adb forward tcp:<host-port> tcp:<app-port>
        ^
        |
host runner
```

The control surface should expose request/response operations such as:

```text
getSnapshot()
resetTestState()
setDisplayName(name)
startNearby()
acceptFriend(peerId)
sendDirectMessage(peerId, text)
leaveGroup()
disconnectPeer(peerId)
```

Every request should return an operation ID and a structured result. The app
should also provide a structured event stream containing at least:

```text
runtime_state
discovery_endpoint
friendship_prompt
peer_state
transport_state
probe_state
reconnect_state
message_sent
message_received
group_state
diagnostic
```

Events must include timestamps, authenticated `PeerId` where available, and
non-secret platform endpoint IDs where useful. They must not include private
keys, PSKs, raw message bodies, or authentication secrets.

The app snapshot should make assertions simple and explicit:

```json
{
  "localPeerId": "...",
  "friends": [{"peerId": "...", "name": "..."}],
  "peers": [{"peerId": "...", "state": "ready"}],
  "receivedMessageIds": ["..."],
  "nearbyEndpoints": 1
}
```

## Scenario execution model

Each scenario is a state machine with explicit barriers:

```text
prepare -> launch -> observe -> act -> await condition -> assert -> collect
```

The runner must:

- assign a unique run and scenario ID;
- clear logs before the scenario;
- reset app state or install a fresh test profile;
- launch all participants;
- wait for readiness events rather than fixed sleeps where possible;
- use bounded waits with useful timeout diagnostics;
- execute independent device actions concurrently when required;
- record the exact action timeline;
- always collect artifacts in a `finally`/cleanup path.

Fixed sleeps may be used only as settling windows around known platform
operations, and their reason should be documented in code comments.

## Initial real-device scenarios

### Pairing and friendship

1. Reset both apps.
2. Launch both runtimes and verify discovery is active.
3. Wait for authenticated nearby identification.
4. Accept the friendship on one side.
5. Assert the same authenticated `PeerId` and friend record appear on both
   sides.
6. Assert no retry prompt is shown for an authenticated app peer.

### Bidirectional direct chat

1. Establish friendship.
2. Send a reliable acknowledged message from A to B.
3. Assert delivery and acknowledgement on both sides.
4. Repeat from B to A.
5. Assert exactly one application delivery per message ID.

### Duplicate physical links

1. Start symmetric advertising and discovery on both devices.
2. Allow simultaneous opposite-direction GATT connections.
3. Assert no more than one logical READY peer per compatible security profile.
4. Assert redundant physical candidates are closed according to LPC rank.
5. Assert the surviving chat connection remains usable.

### Process restart and automatic recovery

1. Establish a READY friend connection.
2. Force-stop one app with `am force-stop`.
3. Assert the other device leaves stale ONLINE state within the documented
   liveness bound.
4. Relaunch the stopped app.
5. Assert automatic known-peer probing or RESUME restores connectivity without
   manual Connect/friendship actions.
6. Send a message after recovery.

### Bluetooth interruption

Where device permissions allow it:

1. Establish a connection.
2. Disable Bluetooth on one device.
3. Assert transport loss, reconnecting state, and bounded retry behavior.
4. Re-enable Bluetooth.
5. Assert automatic recovery or terminal timeout according to the configured
   reconnect window.

Physical radio operations should be marked as environment-dependent and should
not be part of the fastest presubmit suite.

### Endpoint and address rotation

1. Restart Bluetooth or otherwise cause Android to expose a new platform
   endpoint when possible.
2. Assert the authenticated `PeerId` remains the same friend.
3. Assert no friendship is duplicated or lost.
4. Assert stale endpoint records do not remain selectable after their loss
   timeout.

## Environment preparation

Before each scenario, the device adapter should provide best-effort controls
for:

- app data reset;
- app install and launch;
- screen wake/unlock;
- Bluetooth state;
- location and nearby-device permissions;
- battery optimization exemptions;
- Wi-Fi state where the scenario needs to isolate BLE;
- logcat clearing and capture.

The lab should keep unrelated BLE advertisers away from the test area. A small
RF-controlled or physically isolated test area is preferable for failure
reproduction. The runner must report environmental limitations rather than
silently treating them as application failures.

## Build and version capture

Every run must record:

- app git commit and dirty-worktree status;
- LPC git commit and dirty-worktree status;
- APK/IPA hash;
- Flutter and Dart versions;
- device serial, model, OS/API level;
- scenario configuration;
- test-runner version.

The runner should build once per run, install the same artifact on all Android
participants, and fail early if installation or launch fails.

## Diagnostics and artifacts

Create one directory per scenario:

```text
artifacts/
  2026-09-09T091300Z-reconnect-abc123/
    scenario.json
    result.xml
    host.log
    peer_a.log
    peer_b.log
    peer_a.snapshot.json
    peer_b.snapshot.json
    peer_a.events.jsonl
    peer_b.events.jsonl
    screenshots/
```

The runner should collect artifacts on success and failure. On failure it
should include a compact summary of:

- last known state per device;
- last authenticated PeerId per device;
- endpoint and transport lifecycle;
- reconnect attempts and deadlines;
- queue/backpressure outcomes;
- the first error and the final error;
- action/barrier timeline.

## Reliability rules

- Prefer event-based barriers over arbitrary sleeps.
- Use monotonic deadlines for waits and report elapsed time.
- Make every setup and cleanup operation idempotent.
- Lock devices exclusively.
- Do not reuse app data between scenarios unless the scenario explicitly tests
  persistence.
- Use deterministic test identities only in a dedicated test profile; never
  overwrite normal user identity data.
- Treat Bluetooth addresses, endpoint IDs, and local names as diagnostics, not
  identity.
- Retry infrastructure failures separately from application assertion failures.
- Never automatically retry a failed assertion without preserving the first
  failure artifacts.

## Test tiers and CI policy

### Presubmit

- LPC unit tests.
- In-process two-runtime/fake-transport tests.
- App widget and integration tests that do not require radios.

### Device smoke

- One stable Android/iOS pairing and chat test.
- Run after APK build on a connected lab workstation.

### Device reliability

- Restart, Bluetooth interruption, duplicate-link, and endpoint-rotation
  scenarios.
- Run repeatedly or overnight, with artifact retention and failure grouping.

Real-device tests should publish JUnit results but should not block every code
change until the device lab is stable and exclusive device allocation is in
place.

## Implementation sequence

1. Define the device inventory and add an exclusive device-lock mechanism.
2. Add structured app snapshots and event records to the debug build.
3. Implement ADB install, reset, launch, logcat, and artifact adapters.
4. Implement the host control channel and request correlation IDs.
5. Add pairing and bidirectional-chat scenarios.
6. Add process-restart and automatic-reconnect assertions.
7. Add duplicate-link, stale-presence, and endpoint-rotation scenarios.
8. Add JUnit output, screenshots on failure, and run metadata.
9. Keep Android and iOS device control behind the same abstract device
   interface.
10. Run repeated reliability campaigns and classify failures as infrastructure,
    platform, LPC, or messenger-app defects.

## Completion criteria

The framework is ready for regular use when:

- a clean two-device pairing/chat run requires one host command;
- a failed run contains sufficient logs and snapshots to diagnose the failure;
- process restart recovery is asserted without manual UI interaction;
- repeated runs do not accumulate app state or stale device locks;
- the same scenario can be run against different Android/iOS device pairs by
  changing only the inventory;
- iOS installation, launch, control forwarding, and artifact collection use the
  same scenario logic as Android.
