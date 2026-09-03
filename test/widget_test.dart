import 'package:flutter_test/flutter_test.dart';
import 'package:local_peer_messages/main.dart';

void main() {
  testWidgets('messaging app renders required primary views', (tester) async {
    await tester.pumpWidget(const LocalPeerMessagesApp());
    expect(find.text('LPC Demo Messenger'), findsOneWidget);
    expect(find.text('Nearby'), findsOneWidget);
    expect(find.text('Chats'), findsOneWidget);
    expect(find.text('Group Demo'), findsOneWidget);
    expect(find.text('Diagnostics'), findsOneWidget);
  });
}
