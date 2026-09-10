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

To verify that declining a friendship request leaves both participants
unfriended:

```sh
python3 -m integration_lab.runner --scenario rejected_pairing
```

To test group coordinator convergence and bidirectional group delivery after
pairing:

```sh
python3 -m integration_lab.runner --scenario group_chat
```

The `group_chat` scenario creates the group on the creator, sends the real
`GROUP_DEMO_INVITE`, accepts it through the recipient's integration-controlled
Join decision, and then verifies coordinator convergence and bidirectional
delivery.

To test direct/group coexistence and connection ownership:

```sh
python3 -m integration_lab.runner --scenario coexistence_chat
```

This keeps HostSession, DiscoverySession, and GroupSession active while direct
and group traffic runs, then leaves Group Demo and verifies that direct
friendship delivery and the single logical peer connection remain available.

To verify explicit rejection of a valid Group Demo invitation:

```sh
python3 -m integration_lab.runner --scenario rejected_group_invite
```

To verify that FriendRecords survive an app restart while the ephemeral Group
Demo does not:

```sh
python3 -m integration_lab.runner --scenario group_restart_chat
```

To verify that removing a friendship blocks stale-peer application traffic,
while LPC still permits reconnect and a later friendship recovery:

```sh
python3 -m integration_lab.runner --scenario removed_friend_authorization
```

To verify idempotent repeated friendship requests when the receiver already
has the requester as a friend:

```sh
python3 -m integration_lab.runner --scenario repeated_friend_request
```

To verify offline direct-send failure, absence of hidden durable queuing, and
automatic reconnect afterward:

```sh
python3 -m integration_lab.runner --scenario offline_send_failure
```

For a three-device coordinator-failure test, add one inventory entry with
`role: tertiary`, then run:

```sh
python3 -m integration_lab.runner --scenario coordinator_migration
```

The scenario forms one three-member group, stops the elected coordinator,
verifies the surviving sessions elect a new coordinator, and checks continued
group delivery.

To verify duplicate Group Demo invites are coalesced before acceptance and
ignored after a GroupSession is active:

```sh
python3 -m integration_lab.runner --scenario duplicate_group_invite
```

To verify malformed application envelopes, payloads, and stale friendship
responses are ignored and diagnosed without changing visible state:

```sh
python3 -m integration_lab.runner --scenario malformed_application
```

To verify confirmed local removal sends `FRIEND_REMOVE`, deletes the remote
FriendRecord, and rejects subsequent non-friend removal traffic:

```sh
python3 -m integration_lab.runner --scenario friend_removal_notification
```

To restart the secondary participant and verify persisted friendship,
automatic reconnect, and bidirectional direct chat after a fresh app launch:

```sh
python3 -m integration_lab.runner --scenario restart_chat
```

The runner builds the Android APK and signed iOS debug app, installs and
launches both, resets only messenger test state, and runs authenticated pairing
followed by bidirectional direct chat. The `group_chat` scenario additionally
exercises the application invite and explicit acceptance, verifies one shared
LPC GroupId and coordinator, and sends reliable group messages in both
directions. The `removed_friend_authorization` scenario verifies that a stale
remote FriendRecord cannot authorize direct chat or Group Demo invites after
local removal, then verifies reconnect, re-friending, and normal traffic.
The `restart_chat` scenario terminates and relaunches the secondary app through
the platform-appropriate tooling, then verifies persisted friendship,
automatic reconnect, and bidirectional direct delivery.
To submit a short bidirectional direct-message burst and verify every payload
arrives at the opposite participant:

```sh
python3 -m integration_lab.runner --scenario burst_chat
```

The `burst_chat` scenario submits five messages in each direction without
waiting between sends, exercising queueing and message-stream serialization.
To deliberately submit duplicate Connect actions during the same handshake:

```sh
python3 -m integration_lab.runner --scenario duplicate_link_chat
```

The `duplicate_link_chat` scenario verifies LPC adopts the in-flight attempt,
coalesces any simultaneous inbound path, leaves one logical peer per side, and
still delivers direct messages in both directions.
To interrupt Bluetooth on the Android participant and verify recovery:

```sh
python3 -m integration_lab.runner --scenario bluetooth_recovery_chat
```

The `bluetooth_recovery_chat` scenario disables and re-enables Android
Bluetooth through ADB, checks that the peer observes the interruption, and
verifies automatic reconnect plus bidirectional direct delivery. It always
restores Bluetooth after the interruption phase.
`--skip-build` reuses existing artifacts.

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
`startNearby`, `connect`, `acceptFriend`, `declineFriend`, `createGroup`,
`sendDirectMessage`, `sendGroupMessage`, and `disconnectPeer`.

The smoke runner requires the devices to be unlocked, paired with the host,
have Bluetooth permissions granted, and be close enough for discovery. A
failure is reported with the last snapshots and collected logs rather than
being retried as if it were an infrastructure failure.
