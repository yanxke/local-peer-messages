import 'dart:async';
import 'dart:convert';
import 'dart:io';

typedef IntegrationSnapshot = FutureOr<Map<String, Object?>> Function();
typedef IntegrationCommand =
    Future<Map<String, Object?>> Function(
      String action,
      Map<String, Object?> arguments,
    );

/// Loopback-only control server used by the real-device integration lab.
///
/// The host reaches this server through an explicit USB port forward. Keeping
/// it out of release builds prevents the test controls from becoming an
/// alternate production API or bypassing LPC authentication.
class IntegrationControlServer {
  IntegrationControlServer({
    required this.snapshot,
    required this.command,
    required this.logger,
    this.port = 8765,
  });

  final IntegrationSnapshot snapshot;
  final IntegrationCommand command;
  final void Function(String message) logger;
  final int port;

  HttpServer? _server;
  int? _boundPort;
  final _events = <Map<String, Object?>>[];
  int _nextEvent = 1;
  int _nextOperation = 1;

  bool get isRunning => _server != null;
  int get boundPort => _boundPort ?? port;

  Future<void> start() async {
    if (_server != null) return;
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, port);
    _boundPort = _server!.port;
    _server!.listen(
      _handleRequest,
      onError: (Object error) {
        logger('Integration control server error: $error');
      },
    );
    logger('Integration control server listening on loopback:$port');
  }

  Future<void> stop() async {
    final server = _server;
    _server = null;
    _boundPort = null;
    await server?.close(force: true);
  }

  void emit(String type, [Map<String, Object?> fields = const {}]) {
    final event = <String, Object?>{
      'seq': _nextEvent++,
      'timestamp': DateTime.now().toUtc().toIso8601String(),
      'type': type,
      ...fields,
    };
    _events.add(event);
    if (_events.length > 2000) _events.removeAt(0);
  }

  Future<void> _handleRequest(HttpRequest request) async {
    request.response.headers.contentType = ContentType.json;
    request.response.headers.set('cache-control', 'no-store');
    try {
      if (request.method == 'GET' && request.uri.path == '/health') {
        final currentSnapshot = await snapshot();
        await _write(request, {
          'ok': true,
          'controlPort': boundPort,
          'eventSeq': _nextEvent - 1,
          'runtimeReady': currentSnapshot['runtimeReady'] == true,
          'discoveryActive': currentSnapshot['discoveryActive'] == true,
        });
        return;
      }
      if (request.method == 'GET' && request.uri.path == '/snapshot') {
        await _write(request, await snapshot());
        return;
      }
      if (request.method == 'GET' && request.uri.path == '/events') {
        final after =
            int.tryParse(request.uri.queryParameters['after'] ?? '0') ?? 0;
        await _write(request, {
          'events': _events
              .where((event) => (event['seq'] as int) > after)
              .toList(),
          'nextSeq': _nextEvent - 1,
        });
        return;
      }
      if (request.method == 'POST' && request.uri.path == '/command') {
        await _handleCommand(request);
        return;
      }
      request.response.statusCode = HttpStatus.notFound;
      await _write(request, {'ok': false, 'error': 'unknown endpoint'});
    } catch (error, stack) {
      logger('Integration control request failed: $error');
      logger(stack.toString().split('\n').take(3).join(' | '));
      request.response.statusCode = HttpStatus.internalServerError;
      await _write(request, {'ok': false, 'error': '$error'});
    }
  }

  Future<void> _handleCommand(HttpRequest request) async {
    final body = await utf8.decoder.bind(request).join();
    final decoded = jsonDecode(body);
    if (decoded is! Map) {
      request.response.statusCode = HttpStatus.badRequest;
      await _write(request, {
        'ok': false,
        'error': 'command must be an object',
      });
      return;
    }
    final action = decoded['action'];
    final rawArguments = decoded['arguments'];
    if (action is! String || (rawArguments != null && rawArguments is! Map)) {
      request.response.statusCode = HttpStatus.badRequest;
      await _write(request, {
        'ok': false,
        'error': 'command requires action and object arguments',
      });
      return;
    }
    final operationId =
        decoded['operationId'] as String? ?? 'op-${_nextOperation++}';
    final arguments = <String, Object?>{
      for (final entry in (rawArguments as Map? ?? const {}).entries)
        '${entry.key}': entry.value,
    };
    emit('command_received', {'operationId': operationId, 'action': action});
    try {
      final result = await command(action, arguments);
      emit('command_completed', {
        'operationId': operationId,
        'action': action,
        'ok': true,
      });
      await _write(request, {
        'ok': true,
        'operationId': operationId,
        'result': result,
      });
    } catch (error) {
      emit('command_completed', {
        'operationId': operationId,
        'action': action,
        'ok': false,
      });
      request.response.statusCode = HttpStatus.badRequest;
      await _write(request, {
        'ok': false,
        'operationId': operationId,
        'error': '$error',
      });
    }
  }

  Future<void> _write(HttpRequest request, Object value) async {
    request.response.write(jsonEncode(value));
    await request.response.close();
  }
}
