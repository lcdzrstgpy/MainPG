import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:provider/single_child_widget.dart';

import 'app/app.dart';
import 'core/api/billing_api.dart';
import 'core/api/lan_api.dart';
import 'core/api/message_api.dart';
import 'core/session/session_manager.dart';
import 'core/storage/capture_queue_store.dart';
import 'core/storage/message_store.dart';
import 'core/storage/token_store.dart';
import 'features/capture/state/capture_store.dart';
import 'features/device/state/device_store.dart';
import 'features/inbox/state/inbox_store.dart';
import 'features/points/state/wallet_store.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final TokenStore tokenStore = TokenStore();
  await tokenStore.init();
  final MessageStore messageStore = MessageStore();
  await messageStore.init();
  final CaptureQueueStore captureQueue = CaptureQueueStore();
  await captureQueue.init();

  final SessionManager session = SessionManager(tokenStore);
  final WalletStore wallet = WalletStore(BillingApi(session.cloud));
  final InboxStore inbox = InboxStore(MessageApi(session.cloud), messageStore);
  final DeviceStore device =
      DeviceStore(tokenStore, LanApi(session.lan), session.lan);
  final CaptureStore capture = CaptureStore(captureQueue, session.capture);

  runApp(
    MultiProvider(
      providers: <SingleChildWidget>[
        ChangeNotifierProvider<SessionManager>.value(value: session),
        ChangeNotifierProvider<WalletStore>.value(value: wallet),
        ChangeNotifierProvider<InboxStore>.value(value: inbox),
        ChangeNotifierProvider<DeviceStore>.value(value: device),
        ChangeNotifierProvider<CaptureStore>.value(value: capture),
      ],
      child: const MainPgCompanionApp(),
    ),
  );
}
