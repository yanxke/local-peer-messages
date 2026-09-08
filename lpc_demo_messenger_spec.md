# LPC Demo Messenger
## Normative Offline Proximity LPC Demonstration Specification

**Specification version:** 0.1.0  
**Application protocol version:** 1  
**Required networking dependency:** Local Peer Connections protocol major 1, minor 0, or later compatible revision  
**Working project name:** `lpc_demo_messenger`

> This document is normative for the demo application. An implementation claiming conformance MUST implement all MUST requirements in this document.
>
> The words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, and MAY are normative requirements.

---

# 1. Purpose and Scope

LPC Demo Messenger is a small test and showcase application for Local Peer Connections.

Its purpose is to make LPC behavior visible and exercise LPC correctness. It is **not** a production messenger and MUST NOT grow production-messenger machinery merely to emulate cloud messaging products.

The demo MUST exercise:

- symmetric nearby discovery;
- user-readable discovery names;
- persistent LPC `PeerId` identity independent of Bluetooth/platform identifiers;
- explicit first-time application relationship acceptance;
- LPC automatic known-peer probing and reconnection;
- direct `RELIABLE_ACKED` text messages while peers are online;
- one ephemeral multi-peer Group Demo session;
- LPC automatic coordinator election and coordinator migration;
- coordinator-relayed group messaging and original `sourcePeerId` preservation;
- transport upgrade and fallback when the platform/backend supports them;
- LPC backpressure, disconnect, reconnect, and diagnostics events.

The demo MUST NOT require or implement:

- Internet access or cloud relay;
- accounts or registration;
- production-grade offline message delivery;
- a durable application outbox;
- application-level exactly-once delivery across new LPC sessions;
- persistent room history;
- multiple simultaneously active room/group sessions;
- read receipts;
- typing indicators;
- reactions;
- images, video, voice, or arbitrary files;
- synchronization between multiple devices belonging to one human;
- production-grade friendship transaction recovery.

Chat history MAY be retained in memory for the current application run. Persisting chat history is optional and is not part of demo conformance.

---

# 2. Required User-Visible Structure

The application MUST expose these primary views:

```text
Nearby
Chats
Group Demo
Diagnostics
```

A small Profile/Settings surface MUST allow the local display name to be viewed and changed.

Normal users MUST NOT be asked to choose:

```text
Host
Client
Coordinator
BLE central/peripheral role
GATT/L2CAP/TCP/UDP transport
```

Those are LPC implementation details.

---

# 3. Responsibility Boundary

The application owns only the minimum state needed to drive the demo:

```text
LocalDisplayName
FriendRecord set
optional current-run chat history
current pending friendship prompts
current ephemeral Group Demo invitation/session state
application payload encode/decode
UI and diagnostics presentation
```

LPC owns:

```text
physical discovery
advertising/listening transport behavior
DiscoveryEndpointId lifecycle
cryptographic PeerId establishment
bounded automatic known-peer probing
KnownPeerResolver invocation
automatic known-peer connection retention
PeerConnection security
framing and fragmentation
RELIABLE_ACKED transport semantics
short reconnect and RESUME
GroupSession formation
coordinator election/migration
group routing and source identity preservation
transport upgrade/fallback
queue bounds and backpressure
```

The application MUST NOT implement its own BLE-address-to-friend mapping, reconnect scheduler, fragmentation, transport migration, or coordinator election.

---

# 4. Canonical Peer Identity

The application MUST use authenticated LPC `PeerId` as the only persistent remote identity.

A `FriendRecord` MUST be keyed by `PeerId`.

The application MUST NOT use any of these as persistent identity:

```text
Bluetooth MAC address
CBPeripheral identifier
Android BluetoothDevice address
DiscoveryEndpointId
advertised local name
IP address
hostname
RSSI
```

If a friend later appears through a different platform/BLE identifier but authenticates as the same `PeerId`, the application MUST treat that peer as the same friend.

---

# 5. LocalDisplayName

Each installation MUST maintain one current `LocalDisplayName`.

It MUST:

- be valid UTF-8;
- encode to 1..29 bytes inclusive;
- be human-readable;
- remain stable until explicitly changed;
- NOT be used as cryptographic identity;
- NOT be required to be globally unique.

On first launch, the app SHOULD invite the user to choose a name.

If no name is supplied, the app MUST generate and persist a human-readable alias such as:

```text
Silver Otter 4827
Quiet Maple 1934
Blue Falcon 7201
```

It MUST NOT generate a UUID, hexadecimal string, PeerId fragment, BLE address, platform GUID, or other opaque machine identifier.

The same logical `LocalDisplayName` MUST be used for:

```text
RuntimeConfig.discoveryDisplayName
DemoMessengerMetadataV1.displayName
local profile UI
```

Changing the name MUST call the LPC Runtime local-presentation update operation with both the new discovery name and freshly encoded `DemoMessengerMetadataV1`. It MUST affect future discovery presentation and future HELLO application metadata, but MUST NOT recreate the Runtime, disconnect existing READY peers, change LPC `PeerId`, change friendship identity, or change active Group Demo membership.

---

# 6. DemoMessengerMetadataV1

The demo MUST encode authenticated application metadata exactly as follows:

```text
Offset  Size  Field
0       1     metadata_version = 0x01
1       1     display_name_length N, 1..29
2       N     display_name UTF-8 bytes
```

Total encoded length is `2 + N`, therefore at most 31 bytes.

The encoded name bytes MUST equal the current `LocalDisplayName` bytes exactly.

A receiver MUST treat metadata as unusable if:

- total length is less than 3 bytes;
- `metadata_version != 0x01`;
- N is outside 1..29;
- total length is not exactly `2 + N`;
- the name bytes are invalid UTF-8;
- the decoded name is not usable human-readable text.

Malformed application metadata MUST NOT by itself terminate a valid LPC session.

---

# 7. Runtime and Symmetric Presence Configuration

The demo SHALL configure the LPC Runtime conceptually as:

```text
RuntimeConfig {
    discoveryDisplayName = LocalDisplayName
    applicationMetadata = DemoMessengerMetadataV1(LocalDisplayName)
    trustMode = TOFU
    autoReconnect = true
    autoConnectKnownPeers = true
    knownPeerResolver = FriendDatabaseResolver

    maxConcurrentKnownPeerProbes = 4
    maxPendingKnownPeerProbes = 64
    knownPeerLookupTimeoutMs = 2000
    maxKnownPeerCacheEntries = 256

    enableGatt = true
    enableL2cap = true
    enableLan = true
}
```

The demo MUST create exactly one low-level listener HostSession for direct point-to-point presence:

```text
HostConfig {
    maxPeers = 7
    autoAccept = true
    trustMode = TOFU
    applicationMetadata omitted   // inherits RuntimeConfig.applicationMetadata
}
```

The demo MUST call `startAdvertising()` on that HostSession.

The demo MUST also maintain one DiscoverySession for the same service UUID.

Therefore every running demo instance normally:

```text
advertises/listens
+
scans/discovers
```

simultaneously, subject to LPC/platform behavior.

This HostSession is an internal physical-listener abstraction only. The UI MUST NOT label the device as a host.

When the Group Demo creates a `GroupSession`, the HostSession and DiscoverySession MUST remain logically active. The demo relies on LPC Runtime-owned resource multiplexing: the GroupSession reuses the Runtime's compatible physical advertiser/listener and scan rather than requiring duplicate BLE advertising, duplicate GATT service registration/listeners, or a second platform scan. The application MUST NOT stop/recreate the HostSession or DiscoverySession merely to enter Group Demo.

---

# 8. FriendRecord and KnownPeerResolver

The demo MUST persist:

```text
FriendRecord {
    peerId              PeerId PRIMARY KEY
    authenticatedName   UTF-8 optional
    localNickname       UTF-8 optional
}
```

Friend equality MUST be based on `peerId` only.

The friend database SHALL implement:

```text
KnownPeerResolver.isKnownPeer(peerId):
    return exact database membership lookup by PeerId
```

The demo MUST NOT load the complete friend population into LPC objects at startup.

A large historical FriendRecord set MUST result only in indexed resolver lookups for actually authenticated nearby candidates.

---

# 9. Nearby Discovery Presentation

Before LPC authenticates a PeerId, a discovered endpoint MAY be shown using the observed application discovery name.

A selectable unknown row MUST have a usable human-readable name, for example:

```text
Silver Otter 4827
Unverified name
[Connect]
```

`Unverified name` means only that the human-readable identity claim has not been verified as a real-world person/device identity. It does **not** mean that an already-completed LPC cryptographic session is unauthenticated.

The application MUST NOT substitute:

```text
Unknown nearby peer
Bluetooth MAC address
platform GUID
DiscoveryEndpointId
PeerId fragment
UUID
opaque random string
```

as the visible name.

If no usable human-readable discovery name is available, the endpoint MUST NOT be displayed in the user-facing Nearby list as a platform address, opaque identifier, or generic person/device identity. LPC SHALL own the bounded automatic known-peer probe connection used for discovery-only identification and SHALL coalesce duplicate observations of its currently known `DiscoveryEndpointId` rather than repeatedly reconnecting for each scan callback. LPC MUST expose the authenticated probe connection/metadata to the application before it releases a negative known-peer probe result. The application MAY cache the resulting display name by the ephemeral `DiscoveryEndpointId` for the current discovery lifetime only; it MUST NOT persist that mapping or treat it as peer identity. If authenticated `DemoMessengerMetadataV1` is valid, the demo MAY then display that unverified human-readable name in the normal selectable unknown-peer list. This automatic identification connection MUST NOT itself send `FRIEND_REQUEST`, create a `FriendRecord`, or create a durable connection/probe queue.

After LPC emits authenticated metadata for an unknown peer, a valid `DemoMessengerMetadataV1.displayName` SHOULD replace a truncated discovery hint.

Discovery presentation is current-run, ephemeral state. The demo MUST remove an endpoint row and its endpoint-to-display-name cache entry after it has not been observed for a short bounded period (recommended: 8 seconds). A new platform `DiscoveryEndpointId` after an application restart is a new presentation candidate, even if it later authenticates to the same `PeerId`.

When LPC reports a `KnownPeerConnected` or inbound `HostPeerConnected` event, the binding SHOULD include the associated ephemeral `DiscoveryEndpointId` when available. The demo MAY use that event-scoped association only to update the corresponding Nearby row's friend presentation; it MUST NOT persist it or use it as relationship identity.

Diagnostics MUST distinguish, when applicable:

```text
LPC security: ENCRYPTED_TOFU
Human-readable name: unverified
Friend status: friend / not friend
```

---

# 10. Known-Peer Auto-Connection

When LPC authenticates a PeerId and `KnownPeerResolver` returns true, LPC retains that connection according to the LPC specification.

The application SHALL mark the FriendRecord ONLINE after the corresponding READY/known-peer event.

The application MUST NOT press Connect automatically itself, maintain its own probe queue, or reconnect by historical Bluetooth/platform identifier.

If the user explicitly taps Connect while LPC already has an automatic known-peer probe in progress for that same current `DiscoveryEndpointId`, LPC MUST adopt the existing physical attempt as the user-requested direct connection rather than reject it as an active-attempt collision or start a second connection. The resulting READY connection is direct-retained and follows the normal `FRIEND_REQUEST` flow.

For this purpose, LPC MUST keep an outbound attempt adoptable from native GATT connection establishment through HELLO/AUTH/READY (or terminal failure), not merely until the platform reports the physical link as connected.

For LPC reconnect/RESUME, the candidate HELLO authentication mode MUST match the original READY connection's negotiated trust mode. A reconnection MUST NOT switch a TOFU connection to `KNOWN_PEER` merely because the runtime now knows the authenticated `PeerId`; that would cause a TOFU listener to reject the HELLO before RESUME.

An inbound auto-accepted non-friend connection that receives no `FRIEND_REQUEST` within a short bounded grace period (recommended: 8 seconds) MAY have its HostSession ownership released. This prevents a remote discovery-only identification probe from becoming a long-lived reconnecting connection. A pending friendship prompt keeps the connection eligible until resolved.

---

# 11. First-Time Connect and Friendship

For a non-friend, tapping Connect MUST establish or retain a current LPC PeerConnection using the current endpoint/connection handle supplied by LPC.

After LPC reaches READY, the initiator sends:

```text
FRIEND_REQUEST(requestId)
```

using `RELIABLE_ACKED`.

The receiver MUST obtain the displayed requester name from authenticated `DemoMessengerMetadataV1`, not from a second name field in `FRIEND_REQUEST`.

If the remote PeerId is not already a friend and the authenticated `DemoMessengerMetadataV1` is valid, the receiver displays:

```text
<authenticated self-declared name> wants to connect
Accept
Decline
```

The name MUST still be described as an unverified human-readable identity claim under TOFU.

The friendship prompt MUST be modal and non-dismissible by tapping its scrim; only the explicit Accept or Decline actions resolve it.

If `FRIEND_REQUEST(requestId)` is received on a valid LPC connection but authenticated `DemoMessengerMetadataV1` is absent or unusable, the receiver MUST NOT display an ambiguous friendship prompt and MUST NOT create a FriendRecord. It MUST send `FRIEND_REJECT(requestId)` using `RELIABLE_ACKED`. No rejection reason field is added.

If accepted:

1. receiver persists the authenticated requester PeerId as a FriendRecord;
2. receiver persists the valid authenticated metadata name if available;
3. receiver sends `FRIEND_ACCEPT(requestId)` using `RELIABLE_ACKED`;
4. initiator persists the authenticated receiver PeerId when that matching acceptance is received.

If declined, receiver sends `FRIEND_REJECT(requestId)` and does not create a FriendRecord.

`FRIEND_REQUEST`, `FRIEND_ACCEPT`, and `FRIEND_REJECT` MUST be idempotent by `(remotePeerId, requestId)` for the lifetime of the current process.

A locally initiated pending friendship request MAY expire after a bounded current-run timeout (recommended: 30 seconds). On expiry, the UI MUST stop presenting it as waiting and a later `FRIEND_ACCEPT` or `FRIEND_REJECT` for that request MUST be treated as stale.

If a peer that is already a FriendRecord receives another valid `FRIEND_REQUEST`, it MUST send `FRIEND_ACCEPT` for that request without requiring the user to accept again. This provides a simple recovery path from a previously lost acceptance without adding a durable friendship transaction protocol.

The demo does not guarantee atomic distributed friendship commit across application/device crashes. That is intentionally outside demo scope.

---

# 12. Friend Presence

Each FriendRecord SHALL display one of:

```text
OFFLINE
CONNECTING
ONLINE
RECONNECTING
```

Recommended mapping:

```text
no usable READY connection -> OFFLINE
explicit connect attempt active -> CONNECTING
PeerConnected/KnownPeerConnected READY -> ONLINE
PeerReconnecting -> RECONNECTING
PeerReconnected -> ONLINE
terminal PeerDisconnected -> OFFLINE
```

Friendship persists independently of presence.

---

# 13. Chats View and Direct Messaging

The Chats view MAY show one current-run conversation per FriendRecord.

The demo MUST only enable Send while the friend has a usable READY LPC connection.

Direct text MUST use:

```text
RELIABLE_ACKED
```

The demo MUST NOT implement a durable offline outbox.

If a peer is offline, the UI MUST report that the peer is offline rather than silently queueing a durable message.

If a send fails, the UI MUST surface the LPC send result/error. It MAY offer a user-initiated Retry button after connectivity returns.

A user retry is a new application send. The demo does not attempt application-level exactly-once delivery across separate LPC logical sessions.

Receiving a READY LPC connection from an authenticated PeerId does **not** by itself authorize direct chat. A former friend or other non-friend MAY legitimately obtain a new READY TOFU connection through the always-on HostSession so that the peer can appear as unknown and establish friendship again.

When receiving `CHAT_MESSAGE` with `conversation_type=DIRECT`, the application MUST perform all of the following admission checks before displaying or recording it as normal chat content:

1. authenticated LPC `sourcePeerId` MUST currently exist in `FriendRecord`;
2. `conversation_id` MUST equal `directConversationId(localPeerId, sourcePeerId)`;
3. the message MUST have arrived as direct PeerConnection application traffic, not as GroupSession-routed application delivery.

If any check fails, the application MUST ignore or diagnostics-log the message and MUST NOT display it as normal direct chat content, create/recreate a FriendRecord, create a normal chat conversation for that sender, or infer friendship from the READY connection.

---

# 14. Application Wire Envelope

Every demo application message sent inside LPC payload bytes MUST use this envelope:

```text
Offset  Size  Field
0       1     app_protocol_version = 0x01
1       1     message_type
2       2     payload_length, uint16 big-endian
4       N     payload
```

`payload_length` MUST equal the remaining byte count exactly.

Unknown message types MUST be ignored or logged and MUST NOT by themselves close an otherwise valid LPC connection.

Defined message types:

```text
0x01 FRIEND_REQUEST
0x02 FRIEND_ACCEPT
0x03 FRIEND_REJECT
0x04 FRIEND_REMOVE
0x10 GROUP_DEMO_INVITE
0x20 CHAT_MESSAGE
```

## 14.1 Inbound Application Authorization

LPC authentication establishes the remote `sourcePeerId` and protects the transport/session. It does not imply that every demo application message type is authorized by the current application relationship state.

The demo MUST apply the following admission policy using the current application state at the time the message is processed:

| Incoming message/path | Non-friend admission |
|---|---|
| `FRIEND_REQUEST` | allowed, subject to Section 11 metadata and idempotency rules |
| `FRIEND_ACCEPT` | allowed only when `(sourcePeerId, requestId)` matches a currently pending local friendship request |
| `FRIEND_REJECT` | allowed only when `(sourcePeerId, requestId)` matches a currently pending local friendship request |
| `FRIEND_REMOVE` | allowed only when `sourcePeerId` currently exists in `FriendRecord`; payload must be empty |
| `CHAT_MESSAGE` with `DIRECT` | not allowed; sourcePeerId must currently be a FriendRecord and pass Section 13 checks |
| `GROUP_DEMO_INVITE` | not allowed; sourcePeerId must currently be a FriendRecord and pass Section 18 checks |
| GroupSession application traffic | governed by the currently active GroupSession, its committed membership/source identity, and Section 20; FriendRecord membership is not required |

An unauthorized application message MUST NOT by itself close an otherwise valid LPC connection. Unless another rule defines a response, the demo MUST ignore it or record it in Diagnostics.

Friendship state is evaluated **when the application message is received**, not when the underlying PeerConnection was originally established. Therefore deleting a FriendRecord takes effect immediately for subsequent friendship-gated inbound application messages even if the same authenticated PeerConnection remains READY for GroupSession or later reconnects through HostSession.

---

# 15. Friendship Control Payloads

`FRIEND_REQUEST` payload:

```text
Offset  Size  Field
0       16    request_id
```

`FRIEND_ACCEPT` payload:

```text
Offset  Size  Field
0       16    request_id
```

`FRIEND_REJECT` payload:

```text
Offset  Size  Field
0       16    request_id
```

No friendship control message contains a display-name field. The authenticated HELLO application metadata is the presentation source after LPC READY.

A received `FRIEND_ACCEPT(requestId)` or `FRIEND_REJECT(requestId)` MUST affect application state only if `(authenticated sourcePeerId, requestId)` matches a currently pending friendship request initiated locally toward that same PeerId. An unmatched or stale acceptance/rejection MUST be ignored or diagnostics-logged and MUST NOT create, delete, or otherwise change a FriendRecord.

`FRIEND_REMOVE` has an empty payload. A local user MUST confirm removal before the demo sends it. If a currently READY direct Friend connection exists, the demo SHOULD send `FRIEND_REMOVE` using `RELIABLE_ACKED` before deleting its own FriendRecord; delivery failure does not prevent local deletion and MUST NOT be queued durably. A receiver MUST accept it only from a current FriendRecord, then immediately delete that FriendRecord and perform the normal retention/HostSession release. It is authenticated as originating from that LPC peer, but is not proof of a human's intent beyond control of that peer's current cryptographic identity.

---

# 16. ChatMessageV1

`CHAT_MESSAGE` payload is:

```text
Offset  Size  Field
0       1     conversation_type
1       16    conversation_id
17      2     text_length M, uint16 big-endian
19      M     text UTF-8 bytes
```

`conversation_type` values:

```text
0x01 DIRECT
0x02 GROUP_DEMO
```

`text_length` MUST be 1..4096.

The payload length MUST equal `19 + M`.

For DIRECT messages:

```text
conversation_id =
    SHA256(
        ASCII "LPC-DEMO-DIRECT-1" ||
        min(localPeerId, remotePeerId) ||
        max(localPeerId, remotePeerId)
    )[0..15]
```

For GROUP_DEMO messages, `conversation_id` MUST equal the current Group Demo `groupDemoId` defined below.

Authenticated LPC `sourcePeerId` is authoritative for sender identity. No sender name or sender PeerId is duplicated in the chat payload.

---

# 17. Group Demo Scope

The Group Demo exists only to exercise LPC `GroupSession` behavior.

At most one Group Demo GroupSession MAY be active in one demo Runtime.

The Group Demo is ephemeral. It is not restored after application restart and it has no durable message outbox or persistent room history requirement.

The UI MUST clearly describe it as a local demo group, not a private/secure messaging room.

---

# 18. Creating a Group Demo

A user may create a Group Demo by selecting 1..7 currently ONLINE friends.

The creator generates:

```text
groupDemoId      = 16 cryptographically random bytes
groupJoinToken   = 16 cryptographically random bytes
maxPeers         = selected friends + local user
```

The creator creates exactly one GroupSession:

```text
GroupConfig {
    applicationNamespace = "lpc-demo-group-v1"
    discoveryMode = TOKEN_SCOPED
    groupJoinToken = groupJoinToken
    maxPeers = maxPeers
    autoAccept = true
    autoMerge = true
    groupTrustMode = OPEN_TOFU
    coordinatorCheckpointing = false
}
```

The creator sends one `GROUP_DEMO_INVITE` to each selected friend over the already READY direct PeerConnection using `RELIABLE_ACKED`.

Because the demo's direct trust mode is TOFU and `OPEN_TOFU` maps GroupSession pairwise security to TOFU, LPC SHOULD adopt/reuse an existing compatible READY direct PeerConnection when that same peer becomes a required GroupSession link. The application MUST NOT create a duplicate direct BLE connection itself and MUST NOT transfer ownership manually. LPC owns compatible PeerConnection reuse and logical-owner lifetime.

The invite payload is:

```text
Offset  Size  Field
0       16    group_demo_id
16      16    group_join_token
32      1     max_peers, 2..8
```

A receiver MAY accept or decline the invitation only after it passes inbound admission.

A received `GROUP_DEMO_INVITE` MUST be presented/accepted only if:

1. authenticated LPC `sourcePeerId` currently exists in `FriendRecord`;
2. the invite arrived over a usable READY direct PeerConnection from that source peer;
3. the payload is otherwise valid.

If the sender is not currently a friend, or the invite did not arrive through the required direct path, the application MUST ignore or diagnostics-log the invite and MUST NOT show a Group Demo invitation UI or create a GroupSession from it. No application-level decline response is required.

On acceptance, if no other Group Demo is active, it creates a GroupSession using the received token and maxPeers.

If another Group Demo is already active, the receiver MUST decline or require the current demo group to be left first.

Each device initially creates a local singleton GroupSession. LPC MUST then use authenticated `GROUP_INFO` and `GROUP_MERGE` exchange to converge compatible sessions onto one winning LPC `GroupId`, one committed membership snapshot, and one coordinator before accepting group traffic. A locally READY singleton with a different coordinator or LPC `GroupId` is not a formed Group Demo and MUST NOT be treated as a usable group route. The application supplies only the common namespace, token, and capacity; it MUST NOT select the winning group, coordinator, or membership itself.

---

# 19. Group Demo Security Meaning

`groupJoinToken` is a GroupSession scoping value, not an authentication credential.

The Group Demo deliberately uses:

```text
groupTrustMode = OPEN_TOFU
```

because its purpose is to demonstrate low-friction LPC automatic grouping.

Therefore:

- the demo MUST NOT claim that membership is private or strongly authenticated;
- the demo MUST NOT use the Group Demo for sensitive/private content claims;
- the coordinator may see relayed LPC plaintext as defined by LPC;
- possession of the join token MUST NOT be described as proof of identity or authorization.

Production private-messaging applications should use a stronger LPC trust/admission design. That product problem is outside this demo specification.

---

# 20. Group Demo Messaging

Group Demo text MUST use LPC GroupSession reliable messaging.

The default user action SHOULD call:

```text
group.broadcast(payload, deliveryMode=RELIABLE_ACKED)
```

so the demo visibly exercises LPC broadcast fanout and destination-level acknowledgment behavior.

The UI SHOULD expose the resulting per-target/broadcast status in Diagnostics.

The application MUST use LPC `sourcePeerId` from `ReliableMessageReceived` as the original author.

It MUST NOT attribute a relayed message to the coordinator.

The demo MUST NOT add application-level durable retry after coordinator migration. LPC's own in-session GroupMessageId/retry behavior is what the demo is intended to test.

---

# 21. Coordinator Behavior

The application MUST NOT select a coordinator.

Diagnostics MUST show:

```text
current coordinator PeerId
localIsCoordinator
coordinator term if exposed
CoordinatorChanged events
member join/leave events
```

When the coordinator disappears, the app MUST allow LPC to elect/migrate automatically.

The UI SHOULD make the transition visible but MUST NOT require user approval.

The Group Demo remains the same `groupDemoId` while the active GroupSession migrates coordinator.

---

# 22. Transport Migration Demonstration

When supported by LPC/backends, Diagnostics SHOULD show:

```text
current reliable transport
transport generation
upgrade offered
upgrade accepted
upgrade completed
fallback occurred
reconnect/resume events
```

Normal messaging UI MUST remain transport-independent.

The user MUST NOT choose GATT/L2CAP/TCP/UDP in order to send a message.

---

# 23. Backpressure and Failure Presentation

The demo MUST NOT create unbounded application queues.

Because offline durable queueing is out of scope, new direct/group sends that LPC cannot currently accept MUST fail or remain bounded exactly according to LPC APIs.

The UI/Diagnostics SHOULD make at least these conditions visible when produced:

```text
SEND_QUEUE_FULL
DESTINATION_UNAVAILABLE
ACK_TIMEOUT
RECONNECTING
PROTOCOL_MISMATCH
AUTHENTICATION_FAILED
TRANSPORT_FAILED
```

The application MUST NOT silently retry forever.

---

# 24. Friend Removal

The app SHOULD support `Remove Friend`.

Removing a friend MUST:

- after the optional bounded `FRIEND_REMOVE` attempt, delete the FriendRecord immediately so subsequent `KnownPeerResolver` lookups return false;
- call the LPC Runtime direct/known-peer retention-release operation for that PeerId. Conceptually `runtime.releasePeerRetention(peerId)`;
- if the persistent listener HostSession currently owns that peer connection, call `listener.disconnect(peerId, FRIEND_REMOVED)` to release HostSession ownership;
- rely on LPC's release operation to invalidate any cached known-peer classification for that PeerId;
- leave the LPC installation PeerId unchanged.

The UI MUST ask for confirmation before a local removal. If the peer is currently READY, the demo SHOULD use the Section 15 `FRIEND_REMOVE` notification to converge the remote application state; an offline peer cannot be notified without the prohibited durable outbox.

These are ownership-release operations, not force-disconnect operations. If the underlying PeerConnection remains alive because another LPC logical owner, such as the active GroupSession, still requires it, the application MUST stop treating the peer as a friend immediately and MUST NOT call `PeerConnection.disconnect()` merely to tear down the friendship.

If no other logical owner remains after the required releases, LPC closes the final-owner connection normally. A later nearby observation may therefore be authenticated again, receive a fresh false resolver result, and appear as an unknown peer with Connect.

A removed peer MAY subsequently reconnect through the always-on `autoAccept=true` HostSession. Such a READY connection does not restore friendship. Section 14.1 applies immediately: a new valid `FRIEND_REQUEST` may be processed, but direct chat and Group Demo invitations from that PeerId remain unauthorized until friendship is established again.

---

# 25. Application Restart

The demo MUST restore only:

```text
LocalDisplayName
FriendRecords
```

It MUST recreate:

```text
Runtime
listener HostSession
DiscoverySession
```

and reinstall the FriendRecord-backed KnownPeerResolver.

It MUST NOT iterate through every FriendRecord and call `connect()`.

Known friends are rediscovered and automatically classified/retained by LPC.

The ephemeral Group Demo MUST NOT be restored automatically.

Pending friendship prompts, current-run chat history, and failed unsent messages MAY be discarded at restart.

---

# 26. First Launch Sequence

Recommended sequence:

```text
1. initialize LPC persistent identity
2. open FriendRecord storage
3. load or create LocalDisplayName
4. encode DemoMessengerMetadataV1
5. create Runtime with discoveryDisplayName + applicationMetadata
6. install FriendRecord-backed KnownPeerResolver
7. create HostSession and startAdvertising()
8. start DiscoverySession
9. render Nearby/Chats/Group Demo/Diagnostics
```

No account registration or Internet access is required.

---

# 27. Required Diagnostics Events

The Diagnostics view SHOULD surface, when exposed by the binding:

```text
EndpointFound
EndpointUpdated
EndpointLost
KnownPeerProbeStarted
UnknownPeerIdentified
KnownPeerConnected
PeerConnected
PeerReconnecting
PeerReconnected
PeerDisconnected
SecurityLevelChanged / security summary
GroupReady
MemberJoined
MemberLeft
CoordinatorChanged
ReliableMessageReceived
send/broadcast state changes
transport upgrade/fallback events
queue/backpressure errors
```

Raw Bluetooth MAC addresses or platform GUIDs MUST NOT be promoted as user identity or persisted as relationship identity. Because these values are non-secret transport diagnostics, the Diagnostics view and diagnostic logs MAY show raw Bluetooth MAC addresses, platform GUIDs, `DiscoveryEndpointId` values, and other platform transport identifiers to correlate events during the current run. This allowance applies only to diagnostics and logs; it does not permit such values in Nearby identity presentation, FriendRecords, or other persistent application state. Diagnostic logs MUST continue to omit private keys, PSKs, authentication secrets, and raw application payload contents; payloads SHOULD be represented by type, length, and state summaries.

Raw `PeerId`, `SessionId`, `GroupId`, and transport details MAY be shown in Diagnostics.

---

# 28. Mandatory Identity and Discovery Tests

A conforming demo test suite MUST include:

1. Two fresh installations both advertise and scan and can discover each other without either user selecting Host/Client.
2. A discovered unknown peer with a usable name is shown with that name plus an unverified-name indicator.
3. A discovered endpoint without a usable human-readable name is not shown as a generic selectable `Unknown nearby peer` row or as a platform address. If automatic temporary identification is implemented, it displays a name only after valid authenticated metadata is received and does not send `FRIEND_REQUEST` as a side effect.
4. The same `LocalDisplayName` bytes are used for discovery and `DemoMessengerMetadataV1`.
5. `DemoMessengerMetadataV1` rejects names longer than 29 UTF-8 bytes.
6. After HELLO/AUTH, authenticated metadata is available independently of BLE/platform identifiers.
7. A peer reappearing with a different platform/BLE discovery identifier but the same authenticated PeerId maps to the same FriendRecord.
8. A large FriendRecord database does not create one LPC runtime connection/reconnect object per stored friend.
9. Changing `LocalDisplayName` updates Runtime discovery presentation plus future inherited HELLO metadata without recreating Runtime, changing PeerId, or disconnecting an existing READY PeerConnection.

---

# 29. Mandatory Friendship and Known-Peer Tests

The suite MUST verify:

1. First-time Connect sends `FRIEND_REQUEST` only after LPC READY.
2. The request UI name comes from authenticated `DemoMessengerMetadataV1`, not from the FRIEND_REQUEST payload.
3. Accept persists exactly the authenticated remote PeerId.
4. Reject does not create friendship.
5. A repeated request to a peer already stored as a friend is automatically accepted idempotently.
6. After restart, FriendRecords are restored but no explicit connect loop over all friends occurs.
7. When a friend is physically rediscovered, LPC invokes the resolver and emits/produces the known-peer connection path.
8. Friend removal makes future resolver lookups false, releases Runtime direct/known-peer retention, releases applicable HostSession ownership, invalidates stale LPC known-peer cache state, and does not force-disconnect a connection still owned by GroupSession.
9. A `FRIEND_REQUEST` received with absent or unusable authenticated `DemoMessengerMetadataV1` is deterministically answered with `FRIEND_REJECT`, creates no FriendRecord, and shows no ambiguous prompt.
10. `FRIEND_ACCEPT` and `FRIEND_REJECT` affect relationship state only when `(sourcePeerId, requestId)` matches a currently pending local request; unsolicited, mismatched, or stale responses are ignored/logged.
11. A local removal requires confirmation; a valid `FRIEND_REMOVE` from a current friend removes the receiver's FriendRecord, while malformed or non-friend removal messages are ignored.

---

# 30. Mandatory Direct Messaging Tests

The suite MUST verify:

1. Direct text uses `RELIABLE_ACKED`.
2. A successful send reaches the remote application with the authenticated LPC source PeerId.
3. The application does not maintain a durable offline outbox.
4. Sending while the target is offline is disabled or returns a visible failure rather than creating hidden durable delivery state.
5. PeerConnection reconnect/resume occurs through LPC rather than application-created Bluetooth identity mappings.
6. During `PeerReconnecting`, UI state changes to RECONNECTING and returns to ONLINE after `PeerReconnected`.
7. LPC send failures/backpressure are surfaced rather than retried forever.
8. A `DIRECT` `CHAT_MESSAGE` is displayed only when authenticated `sourcePeerId` currently exists in FriendRecord and `conversation_id` equals the canonical direct conversation ID for the local/remote PeerIds.
9. After friend removal, the former friend may reconnect through the always-on HostSession and reach READY, but a `DIRECT` `CHAT_MESSAGE` from that non-friend is ignored/logged and is not displayed or used to recreate friendship.

---

# 31. Mandatory Group Demo Tests

The suite MUST verify with 2..8 devices where practical:

1. At most one Group Demo GroupSession is active per Runtime.
2. Invited peers use the same application namespace and join token.
3. No user selects Host or Coordinator.
4. LPC automatically produces a GroupReady state.
5. All invited devices converge to the same LPC `GroupId`, committed member set, coordinator PeerId, and coordinator term after authenticated automatic merge; different local singleton coordinator IDs are not acceptable completion.
6. LPC elects one coordinator.
7. Non-coordinator-to-non-coordinator group delivery works through coordinator relay when topology requires it.
8. The receiver reports original `sourcePeerId`, not coordinator PeerId.
9. `broadcast(..., RELIABLE_ACKED)` exposes correct per-target completion/failure semantics.
10. Removing/killing the coordinator causes LPC coordinator migration without user approval.
11. Messaging can continue after migration once LPC returns the group to an appropriate READY state.
12. Group Demo state is not automatically restored after app restart.
13. The UI does not describe OPEN_TOFU or the join token as private-room authentication.
14. Creating Group Demo while the persistent HostSession and DiscoverySession are active does not stop/recreate them and does not require duplicate physical advertising/listening/scanning.
15. If two invited friends already have a compatible READY TOFU direct PeerConnection, GroupSession can reuse/adopt that connection for its logical ownership without leaving a duplicate physical BLE connection.
16. Direct chat and Group Demo traffic sharing one PeerConnection are delivered to the correct logical application path.
17. Leaving Group Demo does not disconnect a direct friendship PeerConnection still required for direct/known-peer use.
18. Removing friendship while GroupSession still owns the shared connection does not break GroupSession.
19. `GROUP_DEMO_INVITE` is presented only when authenticated `sourcePeerId` is currently a FriendRecord and the invite arrived through a usable READY direct PeerConnection.
20. After friend removal, a former friend that reconnects through HostSession cannot cause a Group Demo invitation UI or GroupSession creation by sending `GROUP_DEMO_INVITE`.

---

# 32. Mandatory Transport Tests

Where the backend capability exists, the suite SHOULD verify:

1. baseline BLE/GATT connection;
2. negotiated L2CAP upgrade and fallback;
3. negotiated LAN reliable upgrade and fallback;
4. transport generation changes do not change PeerId or FriendRecord identity;
5. transport changes remain invisible to normal send UI;
6. diagnostics report the relevant migration events.

Unsupported optional transports MUST be reported as unsupported rather than faked.

---

# 32.1 Mandatory Demo Coexistence Integration Test

The demo test suite MUST include the following end-to-end scenario:

```text
IT-DEMO-COEXISTENCE

1. Alice and Bob have READY direct connections.
2. HostSession remains logically active.
3. DiscoverySession remains logically active.
4. Alice creates GroupSession.
5. Bob accepts Group Demo invite.
6. GroupSession reaches READY.
7. Direct chat still works.
8. Group chat works.
9. No duplicate physical BLE connection survives.
10. Group traffic and direct traffic are routed to the correct logical application object.
11. Leaving GroupSession does not disconnect the direct friendship PeerConnection.
12. Removing friendship calls the mandatory Runtime retention-release operation plus applicable HostSession ownership release; GroupSession still owns the link, remains usable, and no force-disconnect occurs.
```

This test is specifically intended to verify LPC Runtime physical-resource multiplexing and PeerConnection logical ownership. The demo MUST NOT implement application-layer arbitration to make this test pass.

## 32.2 Mandatory Removed-Friend Reconnection Authorization Test

The demo test suite MUST include:

```text
IT-DEMO-REMOVED-FRIEND-AUTHORIZATION

1. Alice and Bob are friends and have a READY direct connection.
2. Alice removes Bob.
3. Alice's FriendRecord(Bob) no longer exists.
4. Bob still considers Alice a friend.
5. Bob rediscovers/reconnects to Alice through Alice's always-on autoAccept HostSession.
6. LPC authentication succeeds and the new connection may reach READY.
7. Bob sends CHAT_MESSAGE(DIRECT) with the otherwise-correct direct conversation_id.
8. Alice does not display or record the direct chat message as normal chat content.
9. Bob sends GROUP_DEMO_INVITE.
10. Alice does not show an invitation and does not create/join a GroupSession from it.
11. Bob sends a valid FRIEND_REQUEST with usable authenticated DemoMessengerMetadataV1.
12. Alice may show the normal friendship prompt.
13. If Alice accepts and friendship is re-established, subsequent valid direct chat and Group Demo invites are admitted normally.
```

The purpose of this test is to verify that LPC READY/authenticated connectivity and demo-level friendship authorization remain separate concepts.

---

# 33. Suggested Integration Pseudocode

```text
name = loadOrCreateLocalDisplayName(maxUtf8Bytes=29)
metadata = encodeDemoMessengerMetadataV1(name)

runtime = createRuntime(RuntimeConfig(
    discoveryDisplayName=name,
    applicationMetadata=metadata,
    trustMode=TOFU,
    autoReconnect=true,
    autoConnectKnownPeers=true,
    knownPeerResolver=friendDatabase
))

listener = runtime.createHostSession(HostConfig(
    maxPeers=7,
    autoAccept=true,
    trustMode=TOFU
))
listener.startAdvertising()

discovery = runtime.startDiscovery(...)

runtime.events.on(UnknownPeerIdentified, showUnknownPeer)
runtime.events.on(KnownPeerConnected, markFriendOnline)
runtime.events.on(PeerReconnecting, markFriendReconnecting)
runtime.events.on(PeerReconnected, markFriendOnline)
runtime.events.on(PeerDisconnected, markFriendOffline)

function changeLocalDisplayName(newName):
    persist(newName)
    runtime.updateLocalPresentation(
        discoveryDisplayName=newName,
        applicationMetadata=encodeDemoMessengerMetadataV1(newName)
    )
```

---

# 34. Suggested Direct Send Pseudocode

```text
function sendDirect(friendPeerId, text):
    connection = readyConnection(friendPeerId)
    if connection == null:
        show("Peer is offline")
        return

    payload = encodeChatMessage(
        conversationType=DIRECT,
        conversationId=directConversationId(localPeerId, friendPeerId),
        text=text
    )

    handle = connection.send(payload, RELIABLE_ACKED)
    showSendHandleState(handle)
```

---

# 35. Suggested Group Demo Pseudocode

```text
function createGroupDemo(selectedOnlineFriends):
    require(noActiveGroupDemo())

    id = random16()
    token = random16()
    maxPeers = 1 + selectedOnlineFriends.size

    group = runtime.joinOrCreateGroup(GroupConfig(
        applicationNamespace="lpc-demo-group-v1",
        discoveryMode=TOKEN_SCOPED,
        groupJoinToken=token,
        maxPeers=maxPeers,
        autoAccept=true,
        autoMerge=true,
        groupTrustMode=OPEN_TOFU
    ))

    for friend in selectedOnlineFriends:
        sendReliableAcked(friend, GROUP_DEMO_INVITE(id, token, maxPeers))
```

---

# 36. Review Issue Resolution Record

This revision intentionally resolves the independent review as follows:

1. **LPC minor mismatch. Valid.** Dependency changed from protocol minor 1 to minor 0. LPC roadmap wording was also corrected.
2. **No persistent direct advertising/listening endpoint. Valid.** Demo now explicitly runs one advertising HostSession plus one DiscoverySession.
3. **Runtime HELLO application-metadata source missing. Valid LPC gap.** LPC 0.9.10 adds `RuntimeConfig.applicationMetadata`; the demo uses it.
4. **OPEN_TOFU persistent-room admission weakness. Valid for a private messenger.** The production-style private room model was removed. Group Demo is ephemeral, explicitly non-private, and exists only to exercise LPC grouping.
5. **Many persistent GroupSessions / shared PeerConnection ambiguity. Partly broader than this demo, but direct-plus-one-group reuse is immediately required.** Demo still permits only one active Group Demo, while LPC 0.9.11 now defines compatible PeerConnection reuse and reference-like logical ownership so an existing direct connection can also serve GroupSession without ambiguous lifetime.
6. **One-sided durable friendship establishment. Valid for production messaging.** Demo deliberately avoids a durable distributed friendship transaction. It adds idempotent repeat-request acceptance as a lightweight recovery path and documents the non-atomic crash limitation.
7. **Redundant FRIEND_REQUEST display name. Valid.** Name fields were removed from friendship control messages; authenticated HELLO metadata is authoritative presentation metadata after READY.
8. **DemoMessengerMetadataV1 undefined. Valid.** Exact binary encoding and malformed-input behavior are now defined.
9. **Display-name maximum length missing. Valid.** `LocalDisplayName` is limited to 29 UTF-8 bytes.
10. **Direct durable delivery design. Correct but out of scope.** Durable outbox/application retry machinery was removed because it tests messenger semantics rather than LPC.
11. **Per-recipient persistent room outbox. Correct but out of scope.** Removed with persistent rooms.
12. **Room retry after coordinator migration. Correct but out of scope at application layer.** Demo relies on LPC's own in-session behavior and surfaces the result.
13. **senderSequence underspecified. Valid only because the field was unnecessary.** The field was removed.
14. **Application wire encoding underspecified. Valid.** A small exact application envelope and exact payload encodings are now defined.
15. **Friend removal while connected. Valid.** Current friendship state changes immediately. LPC 0.9.12 requires a direct/known-peer retention-release API, and the demo must use it plus applicable HostSession ownership release rather than depending on an optional binding capability.
16. **`Unauthenticated` wording can confuse LPC security state. Valid.** Normal UI uses `Unverified name`; LPC 0.9.11 explicitly permits equivalent unauthenticated/unverified user-facing wording while Diagnostics separately displays LPC cryptographic security.
17. **HostSession/GroupSession advertising and DiscoverySession/GroupSession scan collision. Valid blocker.** LPC 0.9.11 makes physical advertising/listening/scanning Runtime-owned shared resources; the demo keeps HostSession and DiscoverySession logically active during Group Demo.
18. **Direct versus GroupSession PeerConnection ownership. Valid blocker for this demo.** LPC 0.9.12 makes compatible connection adoption/reuse mandatory, defines owner-specific dispatch, and requires application-facing direct/known-peer release semantics.
19. **LocalDisplayName mutation API missing. Valid.** LPC 0.9.11 adds atomic `updateLocalPresentation`; the demo uses it instead of recreating Runtime.
20. **Malformed authenticated metadata plus FRIEND_REQUEST. Valid.** The demo deterministically sends `FRIEND_REJECT`, creates no FriendRecord, and displays no ambiguous prompt.
21. **Dedicated direct/group coexistence test. Valid.** `IT-DEMO-COEXISTENCE` is mandatory.
22. **Direct/known-peer release API only SHOULD. Valid.** LPC 0.9.12 makes equivalent `releasePeerRetention(peerId)` semantics mandatory for every binding and requires cache invalidation.
23. **Section 33.1 numbering order. Valid editorial issue.** LPC 0.9.12 numbers Runtime BLE multiplexing as 33.1.1 and KnownPeerResolver as 33.1.2.
24. **HostSession close versus shared ownership. Valid.** LPC 0.9.12 limits graceful CLOSE/flush/force-close to connections for which HostSession release removes the final owner.
25. **Compatible reuse SHOULD/MUST mismatch. Valid.** LPC 0.9.12 makes compatible reuse mandatory unless another normative requirement independently requires a distinct logical/security session.
26. **Removed friend can reconnect through permanent autoAccept HostSession. Valid demo authorization gap.** Reconnection itself remains allowed, but READY no longer implies friendship authorization. Section 14.1 explicitly gates inbound application message types by current relationship state.
27. **Non-friend direct chat after removal. Valid.** `CHAT_MESSAGE(DIRECT)` now requires a current FriendRecord, the canonical direct conversation ID, and direct PeerConnection delivery.
28. **Non-friend Group Demo invitation after removal. Valid.** `GROUP_DEMO_INVITE` now requires a current FriendRecord and usable READY direct delivery; unauthorized invites are ignored/logged without UI.
29. **Friendship control response admission. Valid related clarification.** `FRIEND_ACCEPT/REJECT` now affect state only when they match a currently pending local request.
30. **Removed-friend reconnection integration coverage. Added.** `IT-DEMO-REMOVED-FRIEND-AUTHORIZATION` verifies READY reconnection is permitted while friendship-gated application traffic remains blocked until a new friendship request is accepted.

---

# 37. Final Developer Experience

The intended implementation remains small:

```text
create Runtime
start one internal listener HostSession
start one DiscoverySession
let Runtime multiplex those resources with GroupSession when Group Demo is active
show nearby peers
accept a peer as a friend
let LPC auto-reconnect known friends
send online RELIABLE_ACKED direct text
optionally start one ephemeral Group Demo
observe LPC coordinator/relay/transport behavior in Diagnostics
```

If implementing a feature requires durable distributed messaging state, persistent multi-room orchestration, production authorization, or cloud-messenger semantics, it is probably outside this demo specification unless it is directly necessary to expose or verify LPC behavior.
