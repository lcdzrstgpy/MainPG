import '../config/app_config.dart';
import '../http/api_client.dart';
import '../http/cloud_client.dart';

/// 账号服务（auth-api `/api/customer/*`）
class AccountApi {
  AccountApi(this._cloud);

  final CloudClient _cloud;

  /// 登录：成功返回 `{token, account, expires_at}`；失败抛 [ApiException]。
  ///
  /// 固定上报 `client_type=mobile`：云端按端分组做单端登录互踢，
  /// 手机端与 PC 端互不顶替，只有手机端之间才会挤占。
  Future<Map<String, dynamic>> login({
    required String identifier,
    required String password,
    bool force = false,
  }) {
    final String trimmed = identifier.trim();
    return _cloud.auth.post(
      AppConfig.accountLoginPath,
      auth: false,
      body: <String, dynamic>{
        if (trimmed.contains('@')) 'email': trimmed else 'username': trimmed,
        'password': password,
        'client_type': 'mobile',
        if (force) 'force': true,
      },
    );
  }

  /// 发送注册用邮箱验证码，返回下次可重发的冷却秒数（后端 `raw.cooldown_seconds`）。
  Future<int> sendRegisterEmailCode({required String email}) async {
    final Map<String, dynamic> data = await _cloud.auth.post(
      AppConfig.accountEmailCodePath,
      auth: false,
      body: <String, dynamic>{
        'email': email.trim(),
        'purpose': 'register',
      },
    );
    final Map<String, dynamic> raw =
        (data['raw'] as Map?)?.cast<String, dynamic>() ??
            const <String, dynamic>{};
    final int cooldown = int.tryParse(raw['cooldown_seconds']?.toString() ?? '') ?? 60;
    return cooldown > 0 ? cooldown : 60;
  }

  /// 注册账号（邮箱 + 验证码 + 邀请码，与 PC 端同契约）。
  ///
  /// 注册不返回 token、不自动登录；成功后可用同一账号登录 PC 端工作台。
  Future<void> register({
    required String username,
    required String email,
    required String emailCode,
    required String password,
    String invitationCode = '',
  }) async {
    await _cloud.auth.post(
      AppConfig.accountRegisterPath,
      auth: false,
      body: <String, dynamic>{
        'username': username.trim(),
        'email': email.trim(),
        'email_code': emailCode.trim(),
        'password': password,
        if (invitationCode.trim().isNotEmpty)
          'invitation_code': invitationCode.trim(),
      },
    );
  }

  Future<void> logout() async {
    await _cloud.auth.post(AppConfig.accountLogoutPath);
  }

  /// 当前会话信息（也可用于探测会话有效性）。
  Future<Map<String, dynamic>> me() => _cloud.auth.get(AppConfig.accountMePath);

  /// 修改密码，返回后端消息（契约字段为 `current_password`）。
  Future<String> changePassword({
    required String identifier,
    required String currentPassword,
    required String newPassword,
  }) async {
    final String trimmed = identifier.trim();
    final Map<String, dynamic> data = await _cloud.auth.post(
      AppConfig.accountChangePasswordPath,
      body: <String, dynamic>{
        if (trimmed.contains('@')) 'email': trimmed else 'username': trimmed,
        'current_password': currentPassword,
        'new_password': newPassword,
      },
    );
    return data['message']?.toString() ?? '密码已修改';
  }
}
