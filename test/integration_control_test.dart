import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:local_peer_messages/integration_control.dart';

void main() {
  test(
    'integration control server serves snapshots, events, and commands',
    () async {
      final commands = <String>[];
      final server = IntegrationControlServer(
        port: 0,
        snapshot: () => {'runtimeReady': true, 'value': 7},
        command: (action, arguments) async {
          commands.add('$action:${arguments['value']}');
          return {'accepted': true};
        },
        logger: (_) {},
      );
      await server.start();
      addTearDown(server.stop);

      Future<Map<String, dynamic>> get(String path) async {
        final client = HttpClient();
        final request = await client.getUrl(
          Uri.parse('http://127.0.0.1:${server.boundPort}$path'),
        );
        final response = await request.close();
        final body = await response.transform(utf8.decoder).join();
        client.close();
        return jsonDecode(body) as Map<String, dynamic>;
      }

      final health = await get('/health');
      expect(health['ok'], isTrue);
      expect(health['runtimeReady'], isTrue);

      server.emit('test_event', {'value': 9});
      final events = await get('/events?after=0');
      expect((events['events'] as List).single['type'], 'test_event');

      final client = HttpClient();
      final request = await client.postUrl(
        Uri.parse('http://127.0.0.1:${server.boundPort}/command'),
      );
      request.headers.contentType = ContentType.json;
      request.write(
        jsonEncode({
          'action': 'echo',
          'arguments': {'value': 3},
        }),
      );
      final response = await request.close();
      final body = await response.transform(utf8.decoder).join();
      client.close();
      final command = jsonDecode(body) as Map<String, dynamic>;
      expect(command['ok'], isTrue);
      expect(command['result']['accepted'], isTrue);
      expect(commands, ['echo:3']);
    },
  );
}
