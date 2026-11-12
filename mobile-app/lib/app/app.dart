import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../core/session/session_manager.dart';
import '../features/login/pages/login_page.dart';
import '../shared/theme/app_theme.dart';
import 'shell/app_shell.dart';

class MainPgCompanionApp extends StatelessWidget {
  const MainPgCompanionApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'MainPG 伴侣',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light,
      themeMode: ThemeMode.light,
      home: const _RootGate(),
    );
  }
}

/// 登录门禁：未登录 → 登录页；已登录 → 主壳（含底部 dock）。
class _RootGate extends StatelessWidget {
  const _RootGate();

  @override
  Widget build(BuildContext context) {
    final bool loggedIn = context.watch<SessionManager>().isLoggedIn;
    return AnimatedSwitcher(
      duration: const Duration(milliseconds: 220),
      child: loggedIn ? const AppShell() : const LoginPage(),
    );
  }
}
