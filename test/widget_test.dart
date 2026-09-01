import 'package:flutter_test/flutter_test.dart';
import 'package:local_peer_messages/main.dart';

void main() {
  testWidgets('messaging app renders its controls', (tester) async {
    await tester.pumpWidget(const LocalPeerMessagesApp());
    expect(find.text('Local Peer Messages'), findsOneWidget);
    expect(find.text('Discover'), findsOneWidget);
    expect(find.text('Advertise'), findsOneWidget);
  });
}
