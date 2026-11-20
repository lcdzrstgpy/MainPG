import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../../core/session/session_manager.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';
import 'register_page.dart';

/// 登录：账号 / 密码 · 强制登录（R8，命中单端登录时出现）
class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final TextEditingController _account = TextEditingController();
  final TextEditingController _password = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _account.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _login({bool force = false}) async {
    final String identifier = _account.text.trim();
    final String password = _password.text;
    if (identifier.isEmpty || password.isEmpty) {
      showAppToast(context, '请输入账号与密码');
      return;
    }
    setState(() => _busy = true);
    final String? error = await context.read<SessionManager>().login(
          identifier: identifier,
          password: password,
          force: force,
        );
    if (!mounted) return;
    setState(() => _busy = false);
    if (error == null) {
      HapticFeedback.mediumImpact();
      // 登录成功后 RootGate 自动切到主壳，无需手动跳转
      showAppToast(context, force ? '已强制登录，原设备会话失效' : '登录成功');
    } else {
      HapticFeedback.heavyImpact();
      showAppToast(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final bool needForce = context.watch<SessionManager>().forceLoginRequired;
    return Scaffold(
      backgroundColor: AppTokens.bg,
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(24, 0, 24, 30),
          child: Column(
            children: <Widget>[
              Container(
                width: 66,
                height: 66,
                margin: const EdgeInsets.only(top: 26, bottom: 18),
                decoration: BoxDecoration(
                  gradient: const LinearGradient(
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                    colors: <Color>[Color(0xFF6F9BFF), AppTokens.brand],
                  ),
                  borderRadius: BorderRadius.circular(20),
                  boxShadow: const <BoxShadow>[
                    BoxShadow(
                      color: Color(0xCC2F6BFF),
                      blurRadius: 30,
                      offset: Offset(0, 14),
                      spreadRadius: -12,
                    ),
                  ],
                ),
                child: const Center(
                  child: Text(
                    'M',
                    style: TextStyle(
                      fontSize: 27,
                      fontWeight: FontWeight.w700,
                      color: Colors.white,
                    ),
                  ),
                ),
              ),
              const Text(
                '登录 MainPG',
                style: TextStyle(
                  fontSize: 21,
                  fontWeight: FontWeight.w700,
                  letterSpacing: -0.2,
                ),
              ),
              const Padding(
                padding: EdgeInsets.only(top: 6, bottom: 26),
                child: Text(
                  '手机号 / 邮箱与 PC 端工作台通用',
                  style: TextStyle(fontSize: 12.5, color: AppTokens.t3),
                ),
              ),
              AppField(
                label: '账号',
                child: AppInput(
                  controller: _account,
                  icon: Icons.person_outline,
                  hint: '手机号 / 邮箱',
                ),
              ),
              AppField(
                label: '密码',
                child: AppInput(
                  controller: _password,
                  obscure: true,
                  icon: Icons.lock_outline,
                  hint: '密码',
                ),
              ),
              const SizedBox(height: AppTokens.gap8),
              AppButton(
                label: '登 录',
                block: true,
                loading: _busy,
                onPressed: () => _login(),
              ),
              const SizedBox(height: AppTokens.gap6),
              Center(
                child: TextButton(
                  onPressed: _busy
                      ? null
                      : () => pushPage<void>(context, const RegisterPage()),
                  child: const Text(
                    '还没有账号？立即注册',
                    style: TextStyle(fontSize: 13, color: AppTokens.brand),
                  ),
                ),
              ),
              if (needForce) ...<Widget>[
                const SizedBox(height: AppTokens.gap10),
                AppButton(
                  label: '强制登录（挤掉原设备）',
                  tone: AppButtonTone.ghost,
                  block: true,
                  onPressed: _busy ? null : () => _login(force: true),
                ),
                const AppHint(
                  text: '该账号已在其他设备登录。强制登录后原设备会话失效。',
                  alignCenter: true,
                  margin: EdgeInsets.only(top: AppTokens.gap10),
                ),
              ],
              const AppHint(
                text: '登录后可在「我的 → 设备绑定」登记 PC 工作台地址\n'
                    '未登记时 App 走云端通道，登记并连通后自动切换局域网通道',
                alignCenter: true,
                margin: EdgeInsets.only(top: AppTokens.gap16),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
