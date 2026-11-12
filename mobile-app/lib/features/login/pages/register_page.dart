import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../../core/session/session_manager.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';

/// 注册账号：邮箱 + 邮箱验证码 + 邀请码，与 PC 端工作台同契约。
///
/// 注册不自动登录；成功后可用同一账号登录手机端与 PC 端。
class RegisterPage extends StatefulWidget {
  const RegisterPage({super.key});

  @override
  State<RegisterPage> createState() => _RegisterPageState();
}

class _RegisterPageState extends State<RegisterPage> {
  final TextEditingController _username = TextEditingController();
  final TextEditingController _email = TextEditingController();
  final TextEditingController _emailCode = TextEditingController();
  final TextEditingController _password = TextEditingController();
  final TextEditingController _confirm = TextEditingController();
  final TextEditingController _invitation = TextEditingController();

  bool _busy = false;
  bool _codeBusy = false;
  int _countdown = 0;
  Timer? _timer;

  @override
  void dispose() {
    _timer?.cancel();
    _username.dispose();
    _email.dispose();
    _emailCode.dispose();
    _password.dispose();
    _confirm.dispose();
    _invitation.dispose();
    super.dispose();
  }

  void _startCountdown(int seconds) {
    _timer?.cancel();
    setState(() => _countdown = seconds);
    _timer = Timer.periodic(const Duration(seconds: 1), (Timer timer) {
      if (!mounted) {
        timer.cancel();
        return;
      }
      setState(() => _countdown -= 1);
      if (_countdown <= 0) timer.cancel();
    });
  }

  Future<void> _sendCode() async {
    final String email = _email.text.trim();
    if (email.isEmpty || !email.contains('@')) {
      showAppToast(context, '请先输入有效邮箱');
      return;
    }
    setState(() => _codeBusy = true);
    final (int?, String?) result =
        await context.read<SessionManager>().sendRegisterEmailCode(email: email);
    if (!mounted) return;
    setState(() => _codeBusy = false);
    final int? cooldown = result.$1;
    final String? error = result.$2;
    if (error != null) {
      showAppToast(context, error);
    } else {
      showAppToast(context, '验证码已发送，请检查收件箱与垃圾邮件箱');
      _startCountdown(cooldown ?? 60);
    }
  }

  Future<void> _submit() async {
    final String username = _username.text.trim();
    final String email = _email.text.trim();
    final String emailCode = _emailCode.text.trim();
    final String password = _password.text;
    if (username.isEmpty) {
      showAppToast(context, '请输入用户名');
      return;
    }
    if (email.isEmpty || !email.contains('@')) {
      showAppToast(context, '请输入有效邮箱');
      return;
    }
    if (emailCode.length != 6) {
      showAppToast(context, '请输入 6 位邮箱验证码');
      return;
    }
    if (password.length < 6) {
      showAppToast(context, '密码至少 6 位');
      return;
    }
    if (password != _confirm.text) {
      showAppToast(context, '两次输入的密码不一致');
      return;
    }
    setState(() => _busy = true);
    final String? error = await context.read<SessionManager>().register(
          username: username,
          email: email,
          emailCode: emailCode,
          password: password,
          invitationCode: _invitation.text,
        );
    if (!mounted) return;
    setState(() => _busy = false);
    if (error == null) {
      HapticFeedback.mediumImpact();
      showAppToast(context, '注册成功，请用新账号登录');
      Navigator.of(context).maybePop();
    } else {
      HapticFeedback.heavyImpact();
      showAppToast(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final bool canSendCode = _countdown == 0 && !_codeBusy;
    return AppSubPage(
      title: '注册账号',
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(
          AppTokens.pagePadding,
          AppTokens.gap18,
          AppTokens.pagePadding,
          24,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            AppField(
              label: '用户名',
              child: AppInput(
                controller: _username,
                icon: Icons.person_outline,
                hint: '用于登录的用户名',
              ),
            ),
            AppField(
              label: '邮箱',
              child: AppInput(
                controller: _email,
                icon: Icons.mail_outline,
                hint: '用于接收验证码',
                keyboardType: TextInputType.emailAddress,
              ),
            ),
            AppField(
              label: '邮箱验证码',
              child: AppInput(
                controller: _emailCode,
                icon: Icons.verified_outlined,
                hint: '6 位数字验证码',
                keyboardType: TextInputType.number,
                suffix: AppButton(
                  label: _countdown > 0 ? '${_countdown}s' : '获取验证码',
                  small: true,
                  tone: AppButtonTone.line,
                  loading: _codeBusy,
                  onPressed: canSendCode ? _sendCode : null,
                ),
              ),
            ),
            AppField(
              label: '密码',
              child: AppInput(
                controller: _password,
                obscure: true,
                icon: Icons.lock_outline,
                hint: '至少 6 位',
              ),
            ),
            AppField(
              label: '确认密码',
              child: AppInput(
                controller: _confirm,
                obscure: true,
                icon: Icons.lock_outline,
                hint: '再次输入密码',
              ),
            ),
            AppField(
              label: '邀请码（选填）',
              child: AppInput(
                controller: _invitation,
                icon: Icons.card_giftcard_outlined,
                hint: '选填，填错将无法注册',
              ),
            ),
            const SizedBox(height: AppTokens.gap6),
            AppButton(
              label: '注 册',
              block: true,
              loading: _busy,
              onPressed: _submit,
            ),
            const AppEnvNote(
              child: Text(
                '注册成功后不自动登录；同一账号可在手机端与 PC 端工作台登录，'
                '两端会话互不挤占。',
              ),
            ),
          ],
        ),
      ),
    );
  }
}
