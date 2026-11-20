import 'package:flutter/material.dart';

import '../theme/app_tokens.dart';

/// 轻提示：深色胶囊，底部居中，对齐原型 `.toast`。
void showAppToast(BuildContext context, String message) {
  final ScaffoldMessengerState? messenger = ScaffoldMessenger.maybeOf(context);
  if (messenger == null) return;
  messenger.clearSnackBars();
  messenger.showSnackBar(
    SnackBar(
      content: Text(
        message,
        textAlign: TextAlign.center,
        style: const TextStyle(fontSize: 13, color: Colors.white, height: 1.4),
      ),
      backgroundColor: const Color(0xE61D2129),
      behavior: SnackBarBehavior.floating,
      elevation: 0,
      duration: const Duration(milliseconds: 1800),
      padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 10),
      margin: const EdgeInsets.only(left: 44, right: 44, bottom: 104),
      shape: const RoundedRectangleBorder(borderRadius: AppTokens.brPill),
    ),
  );
}
