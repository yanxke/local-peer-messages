import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:cryptography/cryptography.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:local_peer_connections/local_peer_connections.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'integration_control.dart';

const _friendRequest = 1,
    _friendAccept = 2,
    _friendReject = 3,
    _friendRemove = 4,
    _invite = 0x10,
    _chat = 0x20;

void main() => runApp(const LocalPeerMessagesApp());

class LocalPeerMessagesApp extends StatelessWidget {
  const LocalPeerMessagesApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'LPC Demo Messenger',
    theme: ThemeData(
      colorScheme: ColorScheme.fromSeed(seedColor: Colors.indigo),
    ),
    home: const MessagingPage(),
  );
}

class MessagingPage extends StatefulWidget {
  const MessagingPage({super.key});
  @override
  State<MessagingPage> createState() => _MessagingPageState();
}

class _MessagingPageState extends State<MessagingPage> {
  static const _permissions = MethodChannel('local_peer_messages/permissions');
  final _friends = <String, _Friend>{},
      _nearby = <String, DiscoveredEndpoint>{},
      _unnamedNearby = <String, DiscoveredEndpoint>{},
      _identifiedEndpointNames = <String, String>{},
      _endpointPeers = <String, PeerConnection>{},
      _endpointSeenAt = <String, DateTime>{},
      _connectUiStates = <String, _ConnectUiState>{},
      _connections = <String, PeerConnection>{},
      _unreadByFriend = <String, int>{},
      _pending = <String, Set<String>>{},
      _handled = <String>{},
      _logs = <String>[];
  final _lines = <_Line>[];
  final _receivedMessages = <Map<String, Object?>>[];
  int _nextReceivedMessage = 1;
  final _identifyingEndpoints = <String>{};
  final _inboundFriendRequests = <String>{};
  final _friendRequestTimers = <String, Timer>{};
  final _expandedChats = <String>{}, _newFriends = <String>{};
  final _lastEndpointLog = <String, DateTime>{};
  final _direct = TextEditingController(), _groupText = TextEditingController();
  final _selected = <String>{};
  final _random = Random.secure();
  NearbyRuntime? _runtime;
  HostSession? _host;
  DiscoverySession? _discovery;
  GroupSession? _group;
  Uint8List? _groupDemoId;
  StreamSubscription<PlatformBleEvent>? _backendSub;
  StreamSubscription<RuntimeEvent>? _runtimeSub;
  StreamSubscription<GroupEvent>? _groupSub;
  IntegrationControlServer? _integrationControl;
  Timer? _endpointExpiryTimer;
  final _peerSubs = <StreamSubscription<dynamic>>[];
  final _pendingFriendDecisions = <String, Completer<bool>>{};
  String _name = '';
  int _tab = 0;

  @override
  void initState() {
    super.initState();
    _endpointExpiryTimer = Timer.periodic(
      const Duration(seconds: 2),
      (_) => _expireEndpoints(),
    );
    if (kDebugMode) unawaited(_startIntegrationControl());
    unawaited(_start());
  }

  Future<void> _startIntegrationControl() async {
    final control = IntegrationControlServer(
      snapshot: _integrationSnapshot,
      command: _integrationCommand,
      logger: _log,
    );
    _integrationControl = control;
    try {
      await control.start();
      control.emit('runtime_state', {'state': 'control_ready'});
    } catch (error) {
      _log('Integration control server failed: $error');
    }
  }

  Future<Map<String, Object?>> _integrationSnapshot() async => {
    'localPeerId': _runtime?.localPeerId.toString(),
    'displayName': _name,
    'runtimeReady': _runtime != null,
    'discoveryActive': _discovery != null,
    'friends': [
      for (final friend in _friends.values)
        {
          'peerId': friend.peerId,
          'name': friend.name,
          'presence': friend.presence.name,
        },
    ],
    'peers': [
      for (final entry in _connections.entries)
        {
          'peerId': entry.key,
          'state': entry.value.state.name,
          'security': entry.value.securityLevel.name,
        },
    ],
    'nearbyEndpoints': [
      for (final endpoint in _nearby.values)
        {
          'endpointId': endpoint.id,
          'name': endpoint.localName,
          'rssi': endpoint.rssi,
        },
    ],
    'pendingFriendRequests': _inboundFriendRequests.toList(growable: false),
    'pendingFriendResponses': _pending.keys.toList(growable: false),
    'messagesReceived': List.unmodifiable(_receivedMessages),
    'messageCount': _lines.length,
    'logs': _logs.take(50).toList(growable: false),
  };

  Future<Map<String, Object?>> _integrationCommand(
    String action,
    Map<String, Object?> arguments,
  ) async {
    switch (action) {
      case 'getSnapshot':
        return await _integrationSnapshot();
      case 'resetTestState':
        await _resetIntegrationState();
        return await _integrationSnapshot();
      case 'setDisplayName':
        await _setIntegrationDisplayName(_requiredArgument(arguments, 'name'));
        return await _integrationSnapshot();
      case 'startNearby':
        final runtime = _runtime;
        if (runtime == null) throw StateError('runtime is not ready');
        _discovery ??= await runtime.startDiscovery();
        _integrationControl?.emit('runtime_state', {
          'state': 'discovery_active',
        });
        return await _integrationSnapshot();
      case 'acceptFriend':
        await _decideIntegrationFriendRequest(
          _requiredArgument(arguments, 'peerId'),
          true,
        );
        return await _integrationSnapshot();
      case 'declineFriend':
        await _decideIntegrationFriendRequest(
          _requiredArgument(arguments, 'peerId'),
          false,
        );
        return await _integrationSnapshot();
      case 'connect':
        final endpointId = _requiredArgument(arguments, 'endpointId');
        final endpoint = _nearby[endpointId];
        if (endpoint == null) {
          throw StateError('nearby endpoint is not present: $endpointId');
        }
        unawaited(_connect(endpoint));
        return {'endpointId': endpointId, 'state': 'connect_requested'};
      case 'sendDirectMessage':
        final peerId = _requiredArgument(arguments, 'peerId');
        final text = _requiredArgument(arguments, 'text');
        final result = await _sendDirect(peerId, text);
        if (result != SendState.remoteAcknowledged &&
            result != SendState.sentToTransport) {
          throw StateError('direct message was not delivered');
        }
        return {'sendState': result?.name};
      case 'disconnectPeer':
        final id = _requiredArgument(arguments, 'peerId');
        final peer = PeerId(_unhex(id));
        await _host?.disconnect(peer, reason: 'INTEGRATION_TEST');
        _integrationControl?.emit('transport_state', {
          'peerId': id,
          'state': 'disconnect_requested',
        });
        return await _integrationSnapshot();
      default:
        throw ArgumentError('unsupported integration action: $action');
    }
  }

  String _requiredArgument(Map<String, Object?> arguments, String key) {
    final value = arguments[key];
    if (value is! String || value.isEmpty) {
      throw ArgumentError('missing non-empty argument: $key');
    }
    return value;
  }

  Future<void> _decideIntegrationFriendRequest(String id, bool accepted) async {
    final decision = _pendingFriendDecisions[id];
    if (decision == null || decision.isCompleted) {
      throw StateError('no pending friendship prompt for peer $id');
    }
    decision.complete(accepted);
  }

  Future<void> _setIntegrationDisplayName(String value) async {
    final name = _validName(value);
    if (name == null) throw ArgumentError('invalid display name');
    final runtime = _runtime;
    if (runtime == null) throw StateError('runtime is not ready');
    await runtime.updateLocalPresentation(
      LocalPresentation(
        discoveryDisplayName: name,
        applicationMetadata: _metadata(name),
      ),
    );
    _name = name;
    (await SharedPreferences.getInstance()).setString(
      'local_display_name',
      name,
    );
    _integrationControl?.emit('runtime_state', {
      'state': 'display_name_updated',
      'name': name,
    });
    if (mounted) setState(() {});
  }

  Future<void> _resetIntegrationState() async {
    // Reset only app-level test state. The LPC identity remains persistent so
    // reconnect scenarios exercise the same authenticated peer identity.
    for (final decision in _pendingFriendDecisions.values) {
      if (!decision.isCompleted) decision.complete(false);
    }
    _pendingFriendDecisions.clear();
    for (final timer in _friendRequestTimers.values) {
      timer.cancel();
    }
    _friendRequestTimers.clear();
    for (final id in {..._friends.keys, ..._connections.keys}) {
      final peer = PeerId(_unhex(id));
      await _host?.disconnect(peer, reason: 'INTEGRATION_RESET');
      await _runtime?.releasePeerRetention(peer);
    }
    _friends.clear();
    _nearby.clear();
    _unnamedNearby.clear();
    _identifiedEndpointNames.clear();
    _endpointPeers.clear();
    _endpointSeenAt.clear();
    _connectUiStates.clear();
    _connections.clear();
    _identifyingEndpoints.clear();
    _inboundFriendRequests.clear();
    _pending.clear();
    _handled.clear();
    _lines.clear();
    _receivedMessages.clear();
    _nextReceivedMessage = 1;
    _unreadByFriend.clear();
    _newFriends.clear();
    _expandedChats.clear();
    _selected.clear();
    _group?.leave();
    _group = null;
    _groupDemoId = null;
    unawaited(_groupSub?.cancel());
    _groupSub = null;
    await _save();
    _integrationControl?.emit('test_state', {'state': 'reset'});
    if (mounted) setState(() {});
  }

  void _expireEndpoints() {
    final cutoff = DateTime.now().subtract(const Duration(seconds: 8));
    final expired = _endpointSeenAt.entries
        .where((entry) => entry.value.isBefore(cutoff))
        .map((entry) => entry.key)
        .toList(growable: false);
    if (expired.isEmpty) return;
    setState(() {
      for (final endpointId in expired) {
        _endpointSeenAt.remove(endpointId);
        _nearby.remove(endpointId);
        _unnamedNearby.remove(endpointId);
        _identifiedEndpointNames.remove(endpointId);
        _endpointPeers.remove(endpointId);
        _connectUiStates.remove(endpointId);
        _identifyingEndpoints.remove(endpointId);
      }
    });
    _log('Removed ${expired.length} stale discovery endpoint(s)');
  }

  void _log(String message) {
    final now = DateTime.now();
    final timestamp = now.toIso8601String().substring(11, 23);
    _logs.insert(0, '$timestamp  $message');
    if (_logs.length > 100) _logs.removeLast();
    debugPrint('[LPC Demo][$timestamp] $message');
    _integrationControl?.emit('diagnostic', {'message': message});
  }

  // Direct actions need immediate feedback even though the Nearby page no
  // longer carries a persistent status banner. Background discovery events
  // stay in Diagnostics so automatic reconnects do not interrupt the user.
  void _showUserFeedback(String message) {
    if (!mounted) return;
    final messenger = ScaffoldMessenger.maybeOf(context);
    if (messenger == null) return;
    messenger
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(content: Text(message), behavior: SnackBarBehavior.floating),
      );
  }

  bool _shouldLogEndpoint(String endpointId) {
    final now = DateTime.now();
    final previous = _lastEndpointLog[endpointId];
    if (previous != null &&
        now.difference(previous) < const Duration(seconds: 5)) {
      return false;
    }
    _lastEndpointLog[endpointId] = now;
    return true;
  }

  Future<void> _start() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      _name = _validName(prefs.getString('local_display_name')) ?? _alias();
      await prefs.setString('local_display_name', _name);
      for (final raw
          in prefs.getStringList('friend_records') ?? const <String>[]) {
        final f = _Friend.fromJson(jsonDecode(raw) as Map<String, dynamic>);
        _friends[f.peerId] = f;
      }
      final identityStore = PlatformIdentityStore();
      final localIdentity = await LocalIdentity.load(identityStore);
      _log(
        'Startup identity=${localIdentity.peerId} persistedFriends=${_friends.length} name="$_name"',
      );
      final backend = PlatformBleBackend(logger: _log);
      _runtime = await NearbyRuntime.create(
        platformBleBackend: backend,
        identityStore: identityStore,
        config: RuntimeConfig(
          discoveryDisplayName: _name,
          applicationMetadata: _metadata(_name),
          trustMode: HandshakeTrustMode.tofu,
          autoReconnect: true,
          logger: _log,
          // Android GATT service discovery plus the first fragmented HELLO
          // can exceed the default 15-second probe window on a warm/restarted
          // Bluetooth stack. Keep the operation bounded, but allow the
          // authenticated handshake to finish before declaring the endpoint
          // lost.
          reconnectTimeoutMs: 30000,
          // LPC owns automatic known-peer probing and reconnect. The app does
          // not arbitrate which side probes; this is required for symmetric
          // presence and for the coexistence integration test.
          autoConnectKnownPeers: true,
          knownPeerResolver: _Resolver(_friends),
          maxConcurrentKnownPeerProbes: 4,
          maxPendingKnownPeerProbes: 64,
          knownPeerLookupTimeoutMs: 2000,
          maxKnownPeerCacheEntries: 256,
          enableGatt: true,
          enableL2cap: true,
          enableLan: true,
        ),
      );
      _runtimeSub = _runtime!.events.listen(
        _runtimeEvent,
        onError: (Object error, StackTrace stack) {
          _log('Runtime event stream error: $error');
        },
      );
      _backendSub = backend.events.listen(
        _platformEvent,
        onError: (Object error, StackTrace stack) {
          _log('Platform BLE event stream error: $error');
        },
      );
      final permissionGranted = await _permission();
      _log('Bluetooth permission result=$permissionGranted');
      if (!permissionGranted) return;
      _host = _runtime!.createHostSession(
        HostConfig(
          maxPeers: 7,
          autoAccept: true,
          trustMode: HandshakeTrustMode.tofu,
        ),
      );
      _host!.events.listen(
        (e) {
          if (e is HostPeerConnected) {
            _log(
              'HostPeerConnected peer=${e.connection.peerId} endpoint=${e.discoveryEndpointId ?? 'none'}',
            );
            _onHostPeerConnected(e.connection, e.discoveryEndpointId);
          } else if (e is HostPeerVerificationRequired) {
            _log('HostPeerVerificationRequired peer=${e.peerId}');
          } else if (e is HostSessionClosed) {
            _log('HostSessionClosed');
          }
        },
        onError: (Object error, StackTrace stack) {
          _log('Host event stream error: $error');
        },
      );
      await _host!.startAdvertising();
      _log('Host advertising started');
      _discovery = await _runtime!.startDiscovery();
      _log('Runtime started: symmetric advertising/listening and discovery');
      _integrationControl?.emit('runtime_state', {'state': 'discovery_active'});
    } catch (e) {
      _log('Startup failed: $e');
    }
  }

  void _onHostPeerConnected(PeerConnection peer, String? endpointId) {
    if (endpointId != null) _rememberConnectedEndpoint(peer, endpointId);
    _attach(peer, 'PeerConnected');
    // An inbound connection used solely by the remote device's automatic
    // discovery identification must not keep the persistent HostSession
    // reconnecting forever. Keep it long enough for a real FRIEND_REQUEST to
    // arrive, then release only HostSession ownership if no relationship flow
    // began.
    Timer(const Duration(seconds: 8), () {
      final id = peer.peerId.toString();
      if (_friends.containsKey(id) || _inboundFriendRequests.contains(id)) {
        return;
      }
      if (peer.state == PeerConnectionState.ready) {
        _log('Releasing idle unknown inbound peer $id');
        unawaited(
          _host?.disconnect(peer.peerId, reason: 'IDENTIFICATION_COMPLETE'),
        );
      }
    });
  }

  Future<bool> _permission() async {
    try {
      final ok = await _permissions.invokeMethod<bool>(
        'requestBluetoothPermissions',
      );
      return ok != false;
    } on MissingPluginException {
      return true;
    }
  }

  void _platformEvent(PlatformBleEvent e) {
    if (e is PlatformEndpointFound) {
      if (_shouldLogEndpoint(e.endpointId)) {
        _log(
          'BLE endpoint observed endpoint=${e.endpointId} rssi=${e.rssi} name=${e.localName ?? 'none'}',
        );
      }
      _endpointSeenAt[e.endpointId] = DateTime.now();
      final name = _validName(e.localName);
      if (name == null) {
        final identifiedName = _identifiedEndpointNames[e.endpointId];
        if (identifiedName != null) {
          if (mounted) {
            setState(
              () => _nearby[e.endpointId] = DiscoveredEndpoint(
                e.endpointId,
                rssi: e.rssi,
                localName: identifiedName,
              ),
            );
          }
          return;
        }
        // Android cannot provide an app-specific local name in an individual
        // BLE advertisement. Retain only an ephemeral endpoint for this
        // explicit identification flow; never display or persist its opaque
        // platform address/identifier as an identity.
        final isNewUnidentifiedEndpoint = !_unnamedNearby.containsKey(
          e.endpointId,
        );
        if (mounted && isNewUnidentifiedEndpoint) {
          setState(() {
            _unnamedNearby[e.endpointId] = DiscoveredEndpoint(
              e.endpointId,
              rssi: e.rssi,
            );
            _identifyingEndpoints.add(e.endpointId);
          });
        }
        if (isNewUnidentifiedEndpoint) {
          _log(
            'EndpointFound awaiting LPC automatic identification: no usable discovery name',
          );
        }
        return;
      }
      if (mounted)
        setState(
          () => _nearby[e.endpointId] = DiscoveredEndpoint(
            e.endpointId,
            rssi: e.rssi,
            localName: name,
          ),
        );
      if (_shouldLogEndpoint(e.endpointId)) {
        _log('EndpointFound $name (unverified name)');
      }
    }
  }

  void _rememberConnectedEndpoint(PeerConnection peer, String endpointId) {
    _endpointPeers[endpointId] = peer;
    _endpointSeenAt[endpointId] = DateTime.now();
    final name = _decodeMetadata(peer.remoteApplicationMetadata);
    if (name == null) return;
    final previous = _nearby[endpointId];
    _unnamedNearby.remove(endpointId);
    _identifyingEndpoints.remove(endpointId);
    _identifiedEndpointNames[endpointId] = name;
    _nearby[endpointId] = DiscoveredEndpoint(
      endpointId,
      rssi: previous?.rssi ?? -127,
      localName: name,
    );
    _integrationControl?.emit('discovery_endpoint', {
      'endpointId': endpointId,
      'name': name,
      'authenticated': true,
    });
  }

  void _runtimeEvent(RuntimeEvent e) {
    switch (e) {
      case KnownPeerProbeStarted(:final discoveryEndpointId):
        _log('KnownPeerProbeStarted $discoveryEndpointId');
      case KnownPeerProbeFailed(:final discoveryEndpointId, :final error):
        _identifyingEndpoints.remove(discoveryEndpointId);
        _log('KnownPeerProbeFailed $discoveryEndpointId: ${error.code.name}');
        if (mounted) setState(() {});
      case UnknownPeerIdentified(:final connection, :final discoveryEndpointId):
        _log(
          'UnknownPeerIdentified peer=${connection.peerId} endpoint=${discoveryEndpointId ?? 'none'} state=${connection.state.name}',
        );
        if (discoveryEndpointId != null) {
          _rememberConnectedEndpoint(connection, discoveryEndpointId);
          _attach(connection, 'UnknownPeerIdentified');
          unawaited(
            _identifyPeer(
              connection,
              discoveryEndpointId,
              releaseRetention: false,
            ),
          );
        }
      case KnownPeerConnected(:final connection, :final discoveryEndpointId):
        _log(
          'KnownPeerConnected event peer=${connection.peerId} endpoint=${discoveryEndpointId ?? 'none'} state=${connection.state.name}',
        );
        if (discoveryEndpointId != null) {
          _rememberConnectedEndpoint(connection, discoveryEndpointId);
          _identifyingEndpoints.remove(discoveryEndpointId);
          unawaited(
            _identifyPeer(
              connection,
              discoveryEndpointId,
              releaseRetention: false,
            ),
          );
        }
        _attach(connection, 'KnownPeerConnected');
    }
  }

  void _attach(PeerConnection peer, String source) {
    final id = peer.peerId.toString();
    if (_connections[id] == peer) {
      _log('Ignoring duplicate attach peer=$id source=$source');
      return;
    }
    _connections[id] = peer;
    final name = _decodeMetadata(peer.remoteApplicationMetadata);
    final f = _friends[id];
    if (f != null) {
      f.name = name ?? f.name;
      f.presence = _Presence.online;
      unawaited(_save());
    }
    _log(
      '$source $id; security=${peer.securityLevel.name}; human name=${name ?? 'unusable'}',
    );
    _peerSubs.add(
      peer.events.listen((e) {
        // A known-peer probe can establish a replacement logical connection
        // while the previous one is still inside LPC's bounded RESUME window.
        // Events from that superseded connection must not overwrite the
        // replacement's presence or remove it from the map when its expiry
        // eventually produces PeerDisconnected.
        if (_connections[id] != peer) {
          _log('Ignoring stale peer event peer=$id event=${e.runtimeType}');
          return;
        }
        _log(
          'Peer event peer=$id event=${e.runtimeType} state=${peer.state.name}',
        );
        if (e is PeerReconnecting)
          _presence(id, _Presence.reconnecting, 'PeerReconnecting');
        if (e is PeerReconnected)
          _presence(
            id,
            _Presence.online,
            'PeerReconnected ${e.transport.name}',
          );
        if (e is PeerDisconnected) {
          _connections.remove(id);
          final endpointIds = _endpointPeers.entries
              .where((entry) => entry.value == peer)
              .map((entry) => entry.key)
              .toList(growable: false);
          for (final endpointId in endpointIds) {
            _endpointPeers.remove(endpointId);
            _endpointSeenAt.remove(endpointId);
            _nearby.remove(endpointId);
            _unnamedNearby.remove(endpointId);
            _identifiedEndpointNames.remove(endpointId);
            _identifyingEndpoints.remove(endpointId);
          }
          _presence(id, _Presence.offline, 'PeerDisconnected');
        }
      }),
    );
    _peerSubs.add(
      peer.messages.listen(
        (m) {
          _log('Peer message received peer=$id bytes=${m.bytes.length}');
          unawaited(_receive(peer, m));
        },
        onError: (Object error, StackTrace stack) {
          _log('Peer message stream error peer=$id error=$error');
        },
      ),
    );
    if (mounted) setState(() {});
  }

  void _presence(String id, _Presence p, String what) {
    if (_friends[id] != null) _friends[id]!.presence = p;
    _log('$what $id');
    _integrationControl?.emit('peer_state', {
      'peerId': id,
      'state': p.name,
      'reason': what,
    });
    if (mounted) setState(() {});
  }

  Future<void> _connect(
    DiscoveredEndpoint endpoint, {
    bool identifyOnly = false,
  }) async {
    try {
      _log(
        'Connect requested endpoint=${endpoint.id} name=${endpoint.localName} identifyOnly=$identifyOnly',
      );
      // A user Connect adopts an LPC automatic identification probe when one
      // is already running for this endpoint. Keep the attempt, but change
      // its application purpose from name-only identification to the normal
      // friendship flow so READY is followed by FRIEND_REQUEST.
      if (!identifyOnly) _identifyingEndpoints.remove(endpoint.id);
      if (!identifyOnly && mounted) {
        setState(
          () => _connectUiStates[endpoint.id] = _ConnectUiState.connecting,
        );
      }
      if (!identifyOnly) {
        final identifiedPeer = _endpointPeers[endpoint.id];
        if (identifiedPeer != null &&
            identifiedPeer.state == PeerConnectionState.ready) {
          _log(
            'Connect reusing READY endpoint=${endpoint.id} peer=${identifiedPeer.peerId}',
          );
          await _request(identifiedPeer, endpointId: endpoint.id);
          return;
        }
      }
      if (identifyOnly && _identifyingEndpoints.contains(endpoint.id)) return;
      if (identifyOnly) _identifyingEndpoints.add(endpoint.id);
      final a = _runtime!.connect(endpoint.id);
      a.events.listen(
        (e) {
          if (e is ConnectionAttemptConnected) {
            _log(
              'ConnectionAttemptConnected endpoint=${endpoint.id} peer=${e.connection.peerId}',
            );
            _endpointPeers[endpoint.id] = e.connection;
            _attach(e.connection, 'PeerConnected');
            if (_identifyingEndpoints.remove(endpoint.id)) {
              unawaited(_identifyPeer(e.connection, endpoint.id));
            } else {
              unawaited(_request(e.connection, endpointId: endpoint.id));
            }
          } else if (e is ConnectionAttemptFailed) {
            _log(
              'ConnectionAttemptFailed endpoint=${endpoint.id} code=${e.error.code.name} detail=${e.error.message}',
            );
            _identifyingEndpoints.remove(endpoint.id);
            _connectUiStates.remove(endpoint.id);
            _log('Connection failed ${e.error}');
            if (mounted) {
              setState(() {});
              if (!identifyOnly) {
                _showUserFeedback(
                  'Could not connect to ${endpoint.localName}: ${e.error.code.name}',
                );
              }
            }
          }
        },
        onError: (Object error, StackTrace stack) {
          _log(
            'Connection attempt stream error endpoint=${endpoint.id}: $error',
          );
        },
      );
    } catch (e) {
      _connectUiStates.remove(endpoint.id);
      _log('Connect failed $e');
      if (mounted) {
        setState(() {});
        if (!identifyOnly) {
          _showUserFeedback('Could not connect to ${endpoint.localName}');
        }
      }
    }
  }

  Future<void> _identifyPeer(
    PeerConnection peer,
    String endpointId, {
    bool releaseRetention = true,
  }) async {
    final name = _decodeMetadata(peer.remoteApplicationMetadata);
    if (name == null) {
      _log('Identification completed but authenticated metadata was unusable');
      return;
    }
    final discovered = _unnamedNearby.remove(endpointId);
    _identifiedEndpointNames[endpointId] = name;
    if (mounted) {
      setState(
        () => _nearby[endpointId] = DiscoveredEndpoint(
          endpointId,
          rssi: discovered?.rssi ?? 0,
          localName: name,
        ),
      );
    }
    _log(
      'Identification completed: authenticated display name "$name" (unverified)',
    );
    // Discovery-only identification MUST NOT request friendship or retain an
    // application-owned direct connection.
    if (releaseRetention) await _runtime?.releasePeerRetention(peer.peerId);
  }

  Future<void> _request(PeerConnection peer, {String? endpointId}) async {
    final id = peer.peerId.toString();
    if (_friends.containsKey(id) || peer.state != PeerConnectionState.ready)
      return;
    final request = _randomBytes(16);
    final requestKey = '$id:${_hex(request)}';
    _pending.putIfAbsent(id, () => {}).add(_hex(request));
    _friendRequestTimers[requestKey] = Timer(const Duration(seconds: 30), () {
      if (!(_pending[id]?.remove(_hex(request)) ?? false)) return;
      _friendRequestTimers.remove(requestKey);
      if (endpointId != null) _connectUiStates.remove(endpointId);
      _log('FRIEND_REQUEST timed out for $id');
      if (mounted) {
        setState(() {});
        _showUserFeedback(
          'No response from ${_decodeMetadata(peer.remoteApplicationMetadata) ?? 'the peer'}',
        );
      }
    });
    if (endpointId != null && mounted) {
      setState(() => _connectUiStates[endpointId] = _ConnectUiState.waiting);
    }
    final result = await _send(peer, _friendRequest, request, 'FRIEND_REQUEST');
    if (result == null ||
        result == SendState.failed ||
        result == SendState.cancelled) {
      _friendRequestTimers.remove(requestKey)?.cancel();
      _pending[id]?.remove(_hex(request));
      if (endpointId != null) _connectUiStates.remove(endpointId);
      _log('FRIEND_REQUEST could not be sent for $id');
      if (mounted) {
        setState(() {});
        _showUserFeedback('Could not send the friend request');
      }
      return;
    }
  }

  Future<void> _receive(
    PeerConnection peer,
    PeerMessageReceived message,
  ) async {
    final e = _Envelope.decode(message.bytes);
    if (e == null) {
      _log(
        'Ignored malformed application envelope peer=${peer.peerId} bytes=${message.bytes.length}',
      );
      return;
    }
    _log(
      'Application message peer=${peer.peerId} type=0x${e.type.toRadixString(16)} bytes=${e.payload.length}',
    );
    switch (e.type) {
      case _friendRequest:
        await _friendRequestIn(peer, e.payload);
      case _friendAccept:
        await _friendResponse(peer, e.payload, true);
      case _friendReject:
        await _friendResponse(peer, e.payload, false);
      case _friendRemove:
        await _friendRemoveIn(peer, e.payload);
      case _invite:
        await _inviteIn(peer, e.payload);
      case _chat:
        await _directIn(peer, e.payload);
      default:
        _log('Ignored unknown application message type ${e.type}');
    }
  }

  Future<void> _friendRequestIn(PeerConnection peer, Uint8List request) async {
    final id = peer.peerId.toString();
    if (request.length != 16 || !_handled.add('$id:${_hex(request)}')) return;
    if (_friends.containsKey(id)) {
      await _send(peer, _friendAccept, request, 'FRIEND_ACCEPT idempotent');
      return;
    }
    final name = _decodeMetadata(peer.remoteApplicationMetadata);
    if (name == null) {
      await _send(
        peer,
        _friendReject,
        request,
        'FRIEND_REJECT metadata unavailable',
      );
      return;
    }
    if (!mounted) return;
    _inboundFriendRequests.add(id);
    final controlDecision = Completer<bool>();
    _pendingFriendDecisions[id] = controlDecision;
    var dialogCompleted = false;
    final dialogDecision = showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (c) => AlertDialog(
        title: const Text('Friend request'),
        content: Text(
          '$name wants to connect\n\nHuman-readable name: unverified (TOFU).',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Decline'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Accept'),
          ),
        ],
      ),
    );
    dialogDecision.whenComplete(() => dialogCompleted = true);
    _integrationControl?.emit('friendship_prompt', {
      'peerId': id,
      'name': name,
      'state': 'pending',
    });
    final yes = await Future.any<bool>([
      dialogDecision.then((value) => value == true),
      controlDecision.future,
    ]);
    if (controlDecision.isCompleted && !dialogCompleted && mounted) {
      // The host command has made the decision; close the visible prompt so
      // the physical device and the test-control state cannot diverge.
      Navigator.of(context).pop();
    }
    try {
      if (yes == true) {
        _friends[id] = _Friend(id, name, _Presence.online);
        _newFriends.add(id);
        await _save();
        await _send(peer, _friendAccept, request, 'FRIEND_ACCEPT');
        if (mounted) setState(() {});
      } else {
        await _send(peer, _friendReject, request, 'FRIEND_REJECT');
      }
    } finally {
      _inboundFriendRequests.remove(id);
      _pendingFriendDecisions.remove(id);
      _integrationControl?.emit('friendship_prompt', {
        'peerId': id,
        'name': name,
        'state': yes ? 'accepted' : 'declined',
      });
    }
  }

  Future<void> _friendResponse(
    PeerConnection peer,
    Uint8List request,
    bool yes,
  ) async {
    final id = peer.peerId.toString();
    final requestKey = '$id:${_hex(request)}';
    if (request.length != 16 || !(_pending[id]?.remove(_hex(request)) ?? false))
      return _log('Ignored unmatched FRIEND response');
    _friendRequestTimers.remove(requestKey)?.cancel();
    final displayName = _decodeMetadata(peer.remoteApplicationMetadata);
    for (final entry in _endpointPeers.entries) {
      if (entry.value.peerId.toString() == id) {
        _connectUiStates.remove(entry.key);
      }
    }
    if (yes) {
      if (displayName == null) {
        _log('Acceptance ignored: unusable authenticated metadata');
        if (mounted) {
          setState(() {});
          _showUserFeedback(
            'The connection was accepted, but the peer identity was invalid',
          );
        }
        return;
      }
      final name = displayName;
      _friends[id] = _Friend(id, name, _Presence.online);
      _newFriends.add(id);
      await _save();
    }
    if (mounted) {
      setState(() {});
      _showUserFeedback(
        yes
            ? 'Friend request accepted by ${displayName ?? 'the peer'}'
            : 'Friend request declined by ${displayName ?? 'the peer'}',
      );
    }
  }

  Future<void> _friendRemoveIn(PeerConnection peer, Uint8List payload) async {
    final id = peer.peerId.toString();
    if (payload.isNotEmpty || !_friends.containsKey(id)) {
      _log('Ignored unauthorized or malformed FRIEND_REMOVE from $id');
      return;
    }
    await _removeFriendRecord(id, reason: 'REMOTE_FRIEND_REMOVED');
  }

  Future<void> _directIn(PeerConnection peer, Uint8List bytes) async {
    final chat = _Chat.decode(bytes), id = peer.peerId.toString();
    if (chat == null ||
        chat.group ||
        !_friends.containsKey(id) ||
        !_same(chat.id, await _directId(_runtime!.localPeerId, peer.peerId)))
      return _log('Ignored unauthorized/invalid direct chat from $id');
    if (mounted) {
      setState(() {
        _lines.add(_Line(false, id, chat.text, false));
        if (_tab != 1 || !_expandedChats.contains(id)) {
          _unreadByFriend[id] = (_unreadByFriend[id] ?? 0) + 1;
        }
      });
      final message = <String, Object?>{
        'messageId': 'received-${_nextReceivedMessage++}',
        'peerId': id,
        'conversationId': _hex(chat.id),
        'textLength': chat.text.length,
        'textSha256': await _sha256Hex(chat.text),
      };
      _receivedMessages.add(message);
      _integrationControl?.emit('message_received', message);
    }
  }

  Future<SendState?> _sendDirect(String id, [String? requestedText]) async {
    final text = (requestedText ?? _direct.text).trim(),
        peer = _connections[id];
    if (text.isEmpty) return null;
    if (peer == null || peer.state != PeerConnectionState.ready) {
      _log('Direct message not sent: friend $id is offline');
      if (requestedText == null)
        _showUserFeedback('Message not sent: friend is offline');
      return null;
    }
    final result = await _send(
      peer,
      _chat,
      _Chat(
        false,
        await _directId(_runtime!.localPeerId, peer.peerId),
        text,
      ).encode(),
      'Direct chat',
    );
    if (result != SendState.remoteAcknowledged &&
        result != SendState.sentToTransport) {
      if (requestedText == null)
        _showUserFeedback('Message could not be delivered');
      return result;
    }
    if (requestedText == null) _direct.clear();
    if (mounted) setState(() => _lines.add(_Line(true, id, text, false)));
    _integrationControl?.emit('message_sent', {
      'peerId': id,
      'state': result?.name,
      'textLength': text.length,
      'textSha256': await _sha256Hex(text),
    });
    return result;
  }

  Future<void> _inviteIn(PeerConnection peer, Uint8List p) async {
    if (!_friends.containsKey(peer.peerId.toString()) ||
        peer.state != PeerConnectionState.ready ||
        p.length != 33 ||
        p[32] < 2 ||
        p[32] > 8)
      return _log('Ignored unauthorized/invalid Group Demo invite');
    if (_group != null)
      return _log('Ignored invite: Group Demo already active');
    if (!mounted) return;
    final yes = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Group Demo invitation'),
        content: const Text(
          'Join this local demo group? It is not a private or secure messaging room; the join token is not proof of identity.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Decline'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Join'),
          ),
        ],
      ),
    );
    if (yes == true)
      _createGroup(
        Uint8List.fromList(p.sublist(0, 16)),
        Uint8List.fromList(p.sublist(16, 32)),
        p[32],
      );
  }

  Future<void> _createSelected() async {
    final peers = _selected
        .where((id) => _connections[id]?.state == PeerConnectionState.ready)
        .toList();
    if (peers.isEmpty) {
      _log('Group creation skipped: no selected online friends');
      return;
    }
    final gid = _randomBytes(16), token = _randomBytes(16);
    await _createGroup(gid, token, peers.length + 1);
    for (final id in peers) {
      await _send(
        _connections[id]!,
        _invite,
        Uint8List.fromList([...gid, ...token, peers.length + 1]),
        'GROUP_DEMO_INVITE',
      );
    }
  }

  Future<void> _createGroup(Uint8List id, Uint8List token, int maxPeers) async {
    if (_group != null || _runtime == null) return;
    _groupDemoId = id;
    _group = _runtime!.joinOrCreateGroup(
      GroupConfig(
        applicationNamespace: utf8.encode('lpc-demo-group-v1'),
        discoveryMode: DiscoveryMode.tokenScoped,
        groupJoinToken: token,
        maxPeers: maxPeers,
        autoAccept: true,
        autoMerge: true,
        groupTrustMode: GroupTrustMode.openTofu,
        coordinatorCheckpointing: false,
      ),
    );
    _groupSub = _group!.events.listen(_groupEvent);
    _log('Group Demo active: OPEN_TOFU is not a private room');
    if (mounted) setState(() {});
  }

  void _groupEvent(GroupEvent e) {
    if (e is GroupReady)
      _log('GroupReady coordinator=${e.coordinatorPeerId}');
    else if (e is MemberJoined)
      _log('MemberJoined ${e.member.peerId}');
    else if (e is MemberLeft)
      _log('MemberLeft ${e.peerId}');
    else if (e is CoordinatorChanged)
      _log('CoordinatorChanged ${e.current}; local=${e.localIsCoordinator}');
    else if (e is GroupError)
      _log('GroupError ${e.errorCode.name}');
    else if (e is ReliableMessageReceived) {
      final envelope = _Envelope.decode(e.bytes);
      final c = envelope?.type == _chat
          ? _Chat.decode(envelope!.payload)
          : null;
      if (c != null &&
          c.group &&
          _groupDemoId != null &&
          _same(c.id, _groupDemoId!))
        setState(
          () =>
              _lines.add(_Line(false, e.sourcePeerId.toString(), c.text, true)),
        );
    }
    if (mounted) setState(() {});
  }

  Future<void> _sendGroup() async {
    final text = _groupText.text.trim(), g = _group;
    if (text.isEmpty ||
        g == null ||
        g.state != GroupState.ready ||
        _groupDemoId == null)
      return;
    try {
      final b = g.broadcast(
        _Envelope(_chat, _Chat(true, _groupDemoId!, text).encode()).encode(),
        options: const SendOptions(deliveryMode: DeliveryMode.reliableAcked),
      );
      _log('Group broadcast submitted to ${b.targetPeerIds.length} targets');
      await b.completed;
      _log(
        'Broadcast ${b.state.name}: ${b.results.values.map((h) => h.state.name).join(', ')}',
      );
      _groupText.clear();
      if (mounted)
        setState(
          () => _lines.add(
            _Line(true, _runtime!.localPeerId.toString(), text, true),
          ),
        );
    } catch (e) {
      _log('Group send failed $e');
      if (mounted) _showUserFeedback('Group message could not be delivered');
    }
  }

  Future<SendState?> _send(
    PeerConnection peer,
    int type,
    List<int> bytes,
    String label,
  ) async {
    try {
      final s = await peer
          .send(
            _Envelope(type, Uint8List.fromList(bytes)).encode(),
            options: const SendOptions(
              deliveryMode: DeliveryMode.reliableAcked,
            ),
          )
          .completed;
      _log('$label ${s.name}');
      return s;
    } catch (e) {
      _log('$label failed $e');
      return null;
    }
  }

  Future<void> _remove(String id) async {
    final friend = _friends[id];
    if (friend == null || !mounted) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Remove friend?'),
        content: Text('Remove ${friend.name} from your friends?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    final connection = _connections[id];
    if (connection?.state == PeerConnectionState.ready) {
      await _send(connection!, _friendRemove, const [], 'FRIEND_REMOVE');
    } else {
      _log('Friend removal is local only: peer is offline');
    }
    await _removeFriendRecord(id, reason: 'FRIEND_REMOVED');
  }

  Future<void> _removeFriendRecord(String id, {required String reason}) async {
    final peer = PeerId(_unhex(id));
    _friends.remove(id);
    _unreadByFriend.remove(id);
    _newFriends.remove(id);
    _expandedChats.remove(id);
    await _save();
    await _runtime?.releasePeerRetention(peer);
    await _host?.disconnect(peer, reason: reason);
    _log('Friend removed; known-peer retention released ($reason)');
    if (mounted) setState(() {});
  }

  Future<void> _changeName() async {
    final c = TextEditingController(text: _name);
    final value = await showDialog<String>(
      context: context,
      builder: (x) => AlertDialog(
        title: const Text('Profile name'),
        content: TextField(controller: c),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(x),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(x, c.text),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    final name = _validName(value);
    if (name == null) {
      if (value != null) {
        _log('Profile name rejected: invalid UTF-8 byte length');
        _showUserFeedback('Name must be 1–29 UTF-8 bytes');
      }
      return;
    }
    await _runtime?.updateLocalPresentation(
      LocalPresentation(
        discoveryDisplayName: name,
        applicationMetadata: _metadata(name),
      ),
    );
    _name = name;
    (await SharedPreferences.getInstance()).setString(
      'local_display_name',
      name,
    );
    if (mounted) setState(() {});
  }

  Future<void> _save() async =>
      (await SharedPreferences.getInstance()).setStringList(
        'friend_records',
        _friends.values.map((f) => jsonEncode(f.toJson())).toList(),
      );
  Uint8List _randomBytes(int n) =>
      Uint8List.fromList(List.generate(n, (_) => _random.nextInt(256)));
  String _alias() {
    const a = ['Silver', 'Quiet', 'Blue', 'Golden'],
        b = ['Otter', 'Maple', 'Falcon', 'Willow'];
    return '${a[_random.nextInt(a.length)]} ${b[_random.nextInt(b.length)]} ${1000 + _random.nextInt(9000)}';
  }

  @override
  void dispose() {
    _endpointExpiryTimer?.cancel();
    for (final timer in _friendRequestTimers.values) {
      timer.cancel();
    }
    _direct.dispose();
    _groupText.dispose();
    for (final s in _peerSubs) {
      unawaited(s.cancel());
    }
    unawaited(_groupSub?.cancel());
    unawaited(_backendSub?.cancel());
    unawaited(_runtimeSub?.cancel());
    _group?.leave();
    unawaited(_discovery?.stop());
    unawaited(_host?.close());
    unawaited(_runtime?.close());
    unawaited(_integrationControl?.stop());
    super.dispose();
  }

  @override
  Widget build(BuildContext c) => Scaffold(
    appBar: AppBar(
      title: const Text('LPC Demo Messenger'),
      actions: [
        TextButton.icon(
          onPressed: _changeName,
          icon: const Icon(Icons.person_outline),
          label: Text(_name, overflow: TextOverflow.ellipsis),
        ),
      ],
    ),
    body: IndexedStack(
      index: _tab,
      children: [_nearbyView(), _chatsView(), _groupView(), _diagnosticsView()],
    ),
    bottomNavigationBar: NavigationBar(
      selectedIndex: _tab,
      onDestinationSelected: (v) => setState(() {
        _tab = v;
        if (v == 1) {
          // This small demo treats opening Chats as viewing its current-run
          // updates; it deliberately has no network read-receipt protocol.
          _unreadByFriend.clear();
          _newFriends.clear();
        }
      }),
      destinations: [
        const NavigationDestination(icon: Icon(Icons.radar), label: 'Nearby'),
        NavigationDestination(icon: _chatNavigationIcon, label: 'Chats'),
        const NavigationDestination(
          icon: Icon(Icons.groups_outlined),
          label: 'Group Demo',
        ),
        const NavigationDestination(
          icon: Icon(Icons.monitor_heart_outlined),
          label: 'Diagnostics',
        ),
      ],
    ),
  );
  Widget _nearbyView() => ListView(
    children: [
      for (final e in _nearby.values)
        Builder(
          builder: (context) {
            final peer = _endpointPeers[e.id];
            final isFriend =
                peer != null && _friends.containsKey(peer.peerId.toString());
            final connectState = _connectUiStates[e.id];
            return ListTile(
              title: Text(e.localName!),
              subtitle: Text(
                isFriend ? 'Encrypted TOFU' : 'Human-readable name: unverified',
              ),
              trailing: isFriend
                  ? const Chip(label: Text('Friend'))
                  : FilledButton(
                      onPressed: connectState == null
                          ? () => _connect(e)
                          : null,
                      child: Text(switch (connectState) {
                        _ConnectUiState.connecting => 'Connecting…',
                        _ConnectUiState.waiting => 'Waiting…',
                        null => 'Connect',
                      }),
                    ),
            );
          },
        ),
      if (_onlineFriendsWithoutNearbyEndpoint.isNotEmpty)
        const Padding(
          padding: EdgeInsets.fromLTRB(16, 16, 16, 4),
          child: Text('Online friends'),
        ),
      for (final f in _onlineFriendsWithoutNearbyEndpoint)
        ListTile(
          title: Text(f.name),
          subtitle: const Text('Encrypted TOFU • ONLINE'),
          trailing: const Chip(label: Text('Friend')),
        ),
    ],
  );

  List<_Friend> get _onlineFriendsWithoutNearbyEndpoint {
    final represented = <String>{};
    for (final endpoint in _nearby.values) {
      final peer = _endpointPeers[endpoint.id];
      if (peer != null) represented.add(peer.peerId.toString());
    }
    return _friends.values
        .where(
          (friend) =>
              friend.presence == _Presence.online &&
              !represented.contains(friend.peerId),
        )
        .toList(growable: false);
  }

  Widget get _chatNavigationIcon {
    final unreadMessages = _unreadByFriend.values.fold<int>(
      0,
      (sum, value) => sum + value,
    );
    final count = unreadMessages + _newFriends.length;
    return Badge(
      isLabelVisible: count > 0,
      label: Text(count > 99 ? '99+' : '$count'),
      child: const Icon(Icons.chat_bubble_outline),
    );
  }

  Widget _chatsView() => ListView(
    children: [
      for (final f in _friends.values)
        Card(
          child: ExpansionTile(
            onExpansionChanged: (expanded) {
              setState(() {
                if (expanded) {
                  _expandedChats.add(f.peerId);
                  _unreadByFriend.remove(f.peerId);
                  _newFriends.remove(f.peerId);
                } else {
                  _expandedChats.remove(f.peerId);
                }
              });
            },
            title: Row(
              children: [
                Expanded(child: Text(f.name)),
                if (_newFriends.contains(f.peerId))
                  const Padding(
                    padding: EdgeInsets.only(left: 8),
                    child: Chip(label: Text('New friend')),
                  ),
                if ((_unreadByFriend[f.peerId] ?? 0) > 0)
                  Padding(
                    padding: const EdgeInsets.only(left: 8),
                    child: Badge(
                      label: Text('${_unreadByFriend[f.peerId]}'),
                      child: const Icon(Icons.mark_chat_unread_outlined),
                    ),
                  ),
              ],
            ),
            subtitle: Text(f.presence.name.toUpperCase()),
            trailing: IconButton(
              icon: const Icon(Icons.person_remove_outlined),
              onPressed: () => _remove(f.peerId),
            ),
            children: [
              for (final l in _lines.where(
                (l) => !l.group && l.peer == f.peerId,
              ))
                ListTile(
                  title: Text(l.text),
                  trailing: l.local ? const Icon(Icons.arrow_upward) : null,
                ),
              Padding(
                padding: const EdgeInsets.all(12),
                child: Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _direct,
                        enabled: f.presence == _Presence.online,
                        decoration: const InputDecoration(hintText: 'Message'),
                      ),
                    ),
                    IconButton(
                      onPressed: f.presence == _Presence.online
                          ? () => _sendDirect(f.peerId)
                          : null,
                      icon: const Icon(Icons.send),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
    ],
  );
  Widget _groupView() {
    final g = _group;
    return ListView(
      padding: const EdgeInsets.all(12),
      children: [
        const Text(
          'A local demo group—not a private or secure messaging room. OPEN_TOFU and the join token do not prove identity.',
        ),
        const SizedBox(height: 12),
        if (g == null) ...[
          for (final f in _friends.values)
            CheckboxListTile(
              value: _selected.contains(f.peerId),
              onChanged: f.presence == _Presence.online
                  ? (v) => setState(
                      () => v!
                          ? _selected.add(f.peerId)
                          : _selected.remove(f.peerId),
                    )
                  : null,
              title: Text(f.name),
              subtitle: Text(f.presence.name.toUpperCase()),
            ),
          FilledButton(
            onPressed: _createSelected,
            child: const Text('Create Group Demo'),
          ),
        ] else ...[
          ListTile(
            title: Text('State: ${g.state.name}'),
            subtitle: Text(
              'Coordinator: ${g.coordinatorPeerId}\nLocal is coordinator: ${g.isCoordinator}\nTerm: ${g.coordinatorTerm}',
            ),
          ),
          for (final l in _lines.where((l) => l.group))
            ListTile(
              title: Text(l.text),
              subtitle: Text(l.local ? 'You' : l.peer),
            ),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _groupText,
                  decoration: const InputDecoration(hintText: 'Group message'),
                ),
              ),
              IconButton(
                onPressed: g.state == GroupState.ready ? _sendGroup : null,
                icon: const Icon(Icons.send),
              ),
            ],
          ),
          TextButton(
            onPressed: () {
              g.leave();
              setState(() => _group = null);
            },
            child: const Text('Leave Group Demo'),
          ),
        ],
      ],
    );
  }

  Widget _diagnosticsView() => ListView(
    children: [
      ListTile(
        title: Text('Local PeerId: ${_runtime?.localPeerId ?? 'starting'}'),
        subtitle: const Text(
          'Raw PeerIds and transport details are diagnostics, never user identity.',
        ),
      ),
      for (final l in _logs) ListTile(dense: true, title: Text(l)),
    ],
  );
}

class _Resolver implements KnownPeerResolver {
  _Resolver(this.friends);
  final Map<String, _Friend> friends;
  @override
  Future<bool> isKnownPeer(PeerId id) async =>
      friends.containsKey(id.toString());
}

enum _Presence { offline, online, reconnecting }

enum _ConnectUiState { connecting, waiting }

class _Friend {
  _Friend(this.peerId, this.name, this.presence);
  final String peerId;
  String name;
  _Presence presence;
  Map<String, dynamic> toJson() => {
    'peerId': peerId,
    'authenticatedName': name,
  };
  factory _Friend.fromJson(Map<String, dynamic> j) => _Friend(
    j['peerId'] as String,
    j['authenticatedName'] as String? ?? 'Unverified name',
    _Presence.offline,
  );
}

class _Line {
  const _Line(this.local, this.peer, this.text, this.group);
  final bool local, group;
  final String peer, text;
}

class _Envelope {
  _Envelope(this.type, this.payload);
  final int type;
  final Uint8List payload;
  Uint8List encode() => Uint8List.fromList([
    1,
    type,
    payload.length >> 8,
    payload.length & 255,
    ...payload,
  ]);
  static _Envelope? decode(List<int> b) {
    if (b.length < 4 || b[0] != 1 || ((b[2] << 8) | b[3]) != b.length - 4)
      return null;
    return _Envelope(b[1], Uint8List.fromList(b.sublist(4)));
  }
}

class _Chat {
  _Chat(this.group, this.id, this.text);
  final bool group;
  final Uint8List id;
  final String text;
  Uint8List encode() {
    final b = utf8.encode(text);
    return Uint8List.fromList([
      group ? 2 : 1,
      ...id,
      b.length >> 8,
      b.length & 255,
      ...b,
    ]);
  }

  static _Chat? decode(List<int> b) {
    if (b.length < 20 || (b[0] != 1 && b[0] != 2)) return null;
    final n = (b[17] << 8) | b[18];
    if (n < 1 || n > 4096 || b.length != 19 + n) return null;
    try {
      return _Chat(
        b[0] == 2,
        Uint8List.fromList(b.sublist(1, 17)),
        utf8.decode(b.sublist(19)),
      );
    } catch (_) {
      return null;
    }
  }
}

List<int> _metadata(String n) {
  final b = utf8.encode(n);
  if (b.isEmpty || b.length > 29) throw ArgumentError('name');
  return [1, b.length, ...b];
}

String? _decodeMetadata(List<int> b) {
  if (b.length < 3 ||
      b[0] != 1 ||
      b[1] < 1 ||
      b[1] > 29 ||
      b.length != b[1] + 2)
    return null;
  try {
    return _validName(utf8.decode(b.sublist(2)));
  } catch (_) {
    return null;
  }
}

String? _validName(String? n) =>
    n == null ||
        n.trim().isEmpty ||
        utf8.encode(n).length > 29 ||
        n.runes.any((r) => r < 0x20 || r == 0x7f)
    ? null
    : n;
Future<Uint8List> _directId(PeerId l, PeerId r) async {
  final a = l.bytes,
      b = r.bytes,
      o = _compare(a, b) <= 0 ? [...a, ...b] : [...b, ...a];
  final h = await Sha256().hash([...ascii.encode('LPC-DEMO-DIRECT-1'), ...o]);
  return Uint8List.fromList(h.bytes.take(16).toList());
}

Future<String> _sha256Hex(String value) async =>
    _hex((await Sha256().hash(utf8.encode(value))).bytes);

int _compare(List<int> a, List<int> b) {
  for (var i = 0; i < a.length; i++) {
    final x = a[i].compareTo(b[i]);
    if (x != 0) return x;
  }
  return 0;
}

bool _same(List<int> a, List<int> b) =>
    a.length == b.length &&
    List.generate(a.length, (i) => a[i] == b[i]).every((x) => x);
String _hex(List<int> b) =>
    b.map((x) => x.toRadixString(16).padLeft(2, '0')).join();
List<int> _unhex(String s) => List.generate(
  s.length ~/ 2,
  (i) => int.parse(s.substring(i * 2, i * 2 + 2), radix: 16),
);
