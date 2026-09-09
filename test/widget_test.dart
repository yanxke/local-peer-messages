import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/material.dart';
import 'package:local_peer_messages/main.dart';

void main() {
  testWidgets('messaging app renders required primary views', (tester) async {
    await tester.pumpWidget(const LocalPeerMessagesApp());
    expect(find.text('LPC Demo Messenger'), findsOneWidget);
    expect(find.text('Nearby'), findsOneWidget);
    expect(find.text('Chats'), findsOneWidget);
    expect(find.text('Group Demo'), findsOneWidget);
    expect(find.text('Diagnostics'), findsOneWidget);
    expect(find.textContaining('LPC security:'), findsNothing);
    expect(find.textContaining('Nearby discovery active'), findsNothing);
    expect(
      find.byWidgetPredicate(
        (widget) => widget is Badge && !widget.isLabelVisible,
      ),
      findsNWidgets(2),
    );
  });

  testWidgets('direct action failures are shown as transient feedback', (
    tester,
  ) async {
    await tester.pumpWidget(const LocalPeerMessagesApp());
    await tester.tap(find.byIcon(Icons.person_outline));
    await tester.pump();
    await tester.enterText(find.byType(TextField), 'a' * 30);
    await tester.tap(find.text('Save'));
    await tester.pump();

    expect(find.text('Name must be 1–29 UTF-8 bytes'), findsOneWidget);
  });
}
