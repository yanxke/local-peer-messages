# LPC Demo Messenger feature status

## Implemented

- Four primary views: Nearby, Chats, Group Demo, and Diagnostics.
- Persistent local human-readable display name with in-place runtime presentation updates.
- Exact `DemoMessengerMetadataV1`, application envelope, friendship control, and chat payload validation.
- TOFU runtime configuration, symmetric advertising/discovery, indexed persisted `FriendRecord` resolver, and LPC automatic known-peer reconnection.
- Explicit friendship acceptance, idempotent in-process requests, friend presence, removal/retention release, and `RELIABLE_ACKED` direct chat admission.
- Direct and group chat receive paths unwrap and validate the application envelope before presenting messages; failed direct sends are not shown as delivered.
- One ephemeral OPEN_TOFU Group Demo, direct invites, authenticated LPC automatic singleton-session merge to a shared group/coordinator, reliable broadcast, original group source attribution, coordinator diagnostics, and no durable outbox.
- Diagnostics for discovery, relationship, reconnect, group membership/coordinator, and send outcomes.
- Android/iOS interoperability fallback for unnamed BLE advertisements: LPC's bounded automatic known-peer probe exposes authenticated metadata before releasing an unknown peer, allowing the app to display the unverified friendly name. Unauthenticated endpoints remain internal and are never shown as generic nearby devices; platform addresses are never displayed or persisted, and identification never sends a friendship request as a side effect.
- Nearby endpoint rows and their ephemeral endpoint-name caches expire after eight seconds without a new discovery observation. Idle inbound non-friend HostSession ownership is released after eight seconds unless a friendship request is in progress.
- Timestamped, rate-limited endpoint diagnostics plus local unread-message and new-friend badges in the Chats navigation and collapsed conversation rows. Friendship acceptance is shown separately from TOFU human-name verification.
- LPC reconnect candidates preserve the original READY connection trust mode, preventing TOFU peers from rejecting RESUME with a mismatched HELLO trust mode.
- Known-peer and inbound HostSession connections retain their ephemeral discovery endpoint for accurate Nearby friend presentation; unread messages are counted whenever Chats is not the active view, even if that conversation was previously expanded.
- Friend removal requires confirmation and sends authenticated, idempotent `FRIEND_REMOVE` control traffic to a currently READY friend. Offline removal remains local-only by design: no durable outbox is created.
- Friend-request dialogs require explicit Accept or Decline. An explicit Connect tap adopts an in-progress LPC automatic probe for that endpoint rather than competing with it.
- LPC keeps an outbound attempt adoptable through HELLO/AUTH/READY, eliminating the post-GATT race where a user Connect could otherwise create a second path.
- Nearby Connect controls show bounded per-endpoint progress (`Connecting…`, then `Waiting…`) and re-enable after send/connection failure, rejection, or a 30-second in-process friendship-request timeout.
- Opening Chats clears local-only unread/new-friend indicators (without a network read-receipt), and Nearby labels accepted friendship separately from TOFU presentation-name verification.
- Nearby promotes authenticated connected endpoints into the presentation list and shows an authenticated online-friend fallback when a platform connection has no discovery-endpoint association.
- After a logical peer disconnects, its endpoint can be probed again, preserving LPC automatic reconnect when the same platform endpoint reappears.
- Reconnecting inbound GATT peers have the same bounded reconnect deadline, so a powered-off remote device cannot remain online indefinitely while still allowing resume before the deadline.
- Superseded reconnect/probe connections cannot overwrite a replacement friend's presence; duplicate Android GATT readiness callbacks are coalesced before they can create competing sessions.
- Android peripheral GATT notifications are serialized through native send acknowledgements, with bounded backpressure and server-link cancellation on transport close, preventing restart handshakes from receiving non-contiguous fragments.
- Runtime stops competing automatic privacy-endpoint probes once an authenticated peer is READY; the LPC logical reconnect scheduler remains active for genuine transport loss.
- Messenger startup known-peer probing uses the persisted PeerId order to select one initiator for a single-friend pair, avoiding simultaneous Android privacy-address GATT startup links.
- Messenger uses a bounded 30-second GATT probe/reconnect window to accommodate observed Android restart latency without making identification or reconnect indefinite.
- Structured diagnostics cover startup/configuration, discovery, probe scheduling, native GATT lifecycle, handshake stages, reconnect attempts, peer ownership, application message delivery, and transport failures; payload and key material contents are summarized or omitted.

## Unimplemented / dependent on LPC binding exposure

- Fine-grained native transport-upgrade/fallback event stream and transport generation display; the current LPC Flutter public API only exposes the active transport after reconnect.
- Native `EndpointUpdated` / `EndpointLost` callbacks; the current platform event API exposes endpoint-found only.
- Explicit send-handle state-change stream (final states are displayed).
- Full physical-device integration scenarios from sections 28–32; the widget smoke test covers the required primary views, but multi-device BLE tests need device/emulator hardware support.
