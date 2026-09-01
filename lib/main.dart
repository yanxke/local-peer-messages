import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:local_peer_connections/local_peer_connections.dart';

void main() => runApp(const LocalPeerMessagesApp());

class LocalPeerMessagesApp extends StatelessWidget {
  const LocalPeerMessagesApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Local Peer Messages',
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
  NearbyRuntime? _runtime;
  DiscoverySession? _discovery;
  final _text = TextEditingController();
  final _attempts = <String, ConnectionAttempt>{};
  final _connections = <String, PeerConnection>{};
  final _messages = <_ChatLine>[];
  String _status = 'Starting…';

  @override
  void initState() {
    super.initState();
    unawaited(_start());
  }

  Future<void> _start() async {
    try {
      final runtime = await NearbyRuntime.create(
        platformBleBackend: PlatformBleBackend(),
      );
      _runtime = runtime;
      if (mounted) setState(() => _status = 'Ready');
    } catch (error) {
      if (mounted) setState(() => _status = 'Startup failed: $error');
    }
  }

  Future<void> _discover() async {
    if (_runtime == null) return;
    try {
      final discovery = await _runtime!.startDiscovery();
      if (mounted) {
        setState(() {
          _discovery = discovery;
          _status = 'Scanning';
        });
      }
    } catch (error) {
      if (mounted) setState(() => _status = 'Scan failed: $error');
    }
  }

  Future<void> _advertise() async {
    if (_runtime == null) return;
    try {
      final host = _runtime!.createHostSession(HostConfig(autoAccept: true));
      await host.startAdvertising();
      host.events.listen((event) {
        if (event case HostPeerConnected(:final connection)) {
          _attach(connection);
        }
      });
      if (mounted) setState(() => _status = 'Advertising');
    } catch (error) {
      if (mounted) setState(() => _status = 'Advertise failed: $error');
    }
  }

  void _connect(DiscoveredEndpoint endpoint) {
    if (_runtime == null || _attempts.containsKey(endpoint.id)) return;
    try {
      final attempt = _runtime!.connect(endpoint.id);
      _attempts[endpoint.id] = attempt;
      attempt.events.listen((event) {
        switch (event) {
          case ConnectionAttemptConnected(:final connection):
            _attempts.remove(endpoint.id);
            _attach(connection);
          case ConnectionAttemptFailed(:final error):
            _attempts.remove(endpoint.id);
            if (mounted) setState(() => _status = 'Connection failed: $error');
          case ConnectionAttemptCancelled():
            _attempts.remove(endpoint.id);
          case PeerVerificationRequired(:final peerId, :final sas):
            unawaited(_verifyPeer(attempt, peerId, sas));
        }
      });
      setState(() => _status = 'Connecting…');
    } catch (error) {
      setState(() => _status = 'Connection failed: $error');
    }
  }

  Future<void> _verifyPeer(
    ConnectionAttempt attempt,
    PeerId peerId,
    String sas,
  ) async {
    if (!mounted) return;
    setState(() => _status = 'Verify peer $peerId');
    final accepted = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        title: const Text('Verify peer'),
        content: Text(
          'Compare this code with the other device before connecting:\n\n'
          '$sas',
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.headlineMedium,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Reject'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Accept'),
          ),
        ],
      ),
    );
    try {
      await attempt.confirmPeerVerification(accepted == true);
    } catch (error) {
      if (mounted) setState(() => _status = 'Verification failed: $error');
    }
  }

  void _attach(PeerConnection connection) {
    _connections[connection.peerId.toString()] = connection;
    connection.messages.listen((message) {
      if (mounted) {
        setState(
          () => _messages.add(
            _ChatLine(false, utf8.decode(message.bytes, allowMalformed: true)),
          ),
        );
      }
    });
    if (mounted) setState(() => _status = 'Connected to ${connection.peerId}');
  }

  Future<void> _send() async {
    final value = _text.text.trim();
    if (value.isEmpty || _connections.isEmpty) return;
    try {
      await _connections.values.first.send(utf8.encode(value)).completed;
      _text.clear();
      if (mounted) setState(() => _messages.add(_ChatLine(true, value)));
    } catch (error) {
      if (mounted) setState(() => _status = 'Send failed: $error');
    }
  }

  @override
  void dispose() {
    _text.dispose();
    unawaited(_runtime?.close());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final endpoints =
        _discovery?.currentEndpoints() ?? const <DiscoveredEndpoint>[];
    return Scaffold(
      appBar: AppBar(title: const Text('Local Peer Messages')),
      body: Column(
        children: [
          ListTile(
            title: Text(_status),
            subtitle: Text('${_connections.length} connected peer(s)'),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Row(
              children: [
                Expanded(
                  child: FilledButton.icon(
                    onPressed: _discover,
                    icon: const Icon(Icons.search),
                    label: const Text('Discover'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: FilledButton.icon(
                    onPressed: _advertise,
                    icon: const Icon(Icons.wifi_tethering),
                    label: const Text('Advertise'),
                  ),
                ),
              ],
            ),
          ),
          if (endpoints.isNotEmpty)
            SizedBox(
              height: 90,
              child: ListView(
                children: [
                  for (final endpoint in endpoints)
                    ListTile(
                      dense: true,
                      title: Text(endpoint.localName ?? endpoint.id),
                      subtitle: Text('RSSI ${endpoint.rssi}'),
                      trailing: TextButton(
                        onPressed: () => _connect(endpoint),
                        child: const Text('Connect'),
                      ),
                    ),
                ],
              ),
            ),
          const Divider(height: 1),
          Expanded(
            child: _messages.isEmpty
                ? const Center(
                    child: Text('Discover and connect to another device.'),
                  )
                : ListView.builder(
                    itemCount: _messages.length,
                    itemBuilder: (_, index) {
                      final message = _messages[index];
                      return Align(
                        alignment: message.local
                            ? Alignment.centerRight
                            : Alignment.centerLeft,
                        child: Card(
                          child: Padding(
                            padding: const EdgeInsets.all(10),
                            child: Text(message.text),
                          ),
                        ),
                      );
                    },
                  ),
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _text,
                    onSubmitted: (_) => _send(),
                    decoration: const InputDecoration(hintText: 'Message'),
                  ),
                ),
                IconButton(onPressed: _send, icon: const Icon(Icons.send)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _ChatLine {
  const _ChatLine(this.local, this.text);
  final bool local;
  final String text;
}
