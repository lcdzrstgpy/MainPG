import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../core/session/session_manager.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';

/// 修改密码：当前 / 新 / 确认（真实调用 change-password）
class PasswordPage extends StatefulWidget {
  const PasswordPage({super.key});

  @override
  State<PasswordPage> createState() => _PasswordPageState();
}

class _PasswordPageState extends State<PasswordPage> {
  final TextEditingController _current = TextEditingController();
  final TextEditingController _next = TextEditingController();
  final TextEditingController _confirm = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _current.dispose();
    _next.dispose();
    _confirm.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_current.text.isEmpty || _next.text.isEmpty || _confirm.text.isEmpty) {
      showAppToast(context, '请填写完整');
      return;
    }
    if (_next.text.length < 8 || _next.text.length > 20) {
      showAppToast(context, '新密码需 8-20 位');
      return;
    }
    if (_next.text != _confirm.text) {
      showAppToast(context, '两次输入的新密码不一致');
      return;
    }
    setState(() => _busy = true);
    final String? error = await context.read<SessionManager>().changePassword(
          currentPassword: _current.text,
          newPassword: _next.text,
        );
    if (!mounted) return;
    setState(() => _busy = false);
    if (error == null) {
      showAppToast(context, '密码已修改，请在其他设备重新登录');
    } else {
      showAppToast(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    return AppSubPage(
      title: '修改密码',
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
              label: '当前密码',
              child: AppInput(
                controller: _current,
                obscure: true,
                icon: Icons.lock_outline,
              ),
            ),
            AppField(
              label: '新密码',
              child: AppInput(
                controller: _next,
                obscure: true,
                icon: Icons.lock_outline,
                hint: '8-20 位，含字母与数字',
              ),
            ),
            AppField(
              label: '确认新密码',
              child: AppInput(
                controller: _confirm,
                obscure: true,
                icon: Icons.lock_outline,
                hint: '再次输入新密码',
              ),
            ),
            const SizedBox(height: AppTokens.gap6),
            AppButton(label: '保存', block: true, loading: _busy, onPressed: _save),
            const AppEnvNote(
              child: Text(
                '密码与 PC 端工作台共用同一账号体系，修改后需在其他设备重新登录。',
              ),
            ),
          ],
        ),
      ),
    );
  }
}
