import 'package:flutter/foundation.dart';

import '../api/account_api.dart';
import '../api/asset_api.dart';
import '../api/capture_api.dart';
import '../error/error_mapper.dart';
import '../http/api_client.dart';
import '../http/cloud_client.dart';
import '../http/lan_client.dart';
import '../storage/token_store.dart';

/// 会话语义：单端登录、强制登录、云端 token 注入。
///
/// 局域网 token（PC 端 `wh_local_*`）与云端 sessionToken 相互独立，
/// 由 `DeviceStore` 单独管理，这里不触碰 [lan] 的 token。
class SessionManager extends ChangeNotifier {
  SessionManager(this._store, {CloudClient? cloud, LanClient? lan})
      : cloud = cloud ?? CloudClient(),
        lan = lan ?? LanClient() {
    account = AccountApi(this.cloud);
    asset = AssetApi(this.cloud);
    capture = CaptureApi(this.cloud);
    _applyToken(_store.token);
  }

  final TokenStore _store;
  final CloudClient cloud;
  final LanClient lan;
  late final AccountApi account;
  late final AssetApi asset;
  late final CaptureApi capture;

  bool get isLoggedIn => (_store.token ?? '').isNotEmpty;

  Map<String, dynamic> get profile => _store.account;

  /// 账号 id（定向消息 / 反馈归属）
  String get accountId => _store.account['account_id']?.toString() ?? '';

  /// 登录标识：优先用户名，其次邮箱
  String get identifier {
    final String username = _store.account['username']?.toString() ?? '';
    if (username.isNotEmpty) return username;
    return _store.account['email']?.toString() ?? '';
  }

  String get displayName {
    final String username = _store.account['username']?.toString() ?? '';
    if (username.isNotEmpty) return username;
    return _store.account['email']?.toString() ?? '未登录';
  }

  String get email => _store.account['email']?.toString() ?? '';

  /// 登录失败若命中「已在其他设备登录」，需暴露强制登录入口。
  bool get forceLoginRequired => _store.forceLogin;

  void _applyToken(String? token) => cloud.setToken(token);

  /// 登录成功返回 null，否则返回用户可读错误文案。
  Future<String?> login({
    required String identifier,
    required String password,
    bool force = false,
  }) async {
    try {
      final Map<String, dynamic> data = await account.login(
        identifier: identifier,
        password: password,
        force: force,
      );
      final String token = data['token']?.toString() ?? '';
      if (token.isEmpty) return '服务端未返回有效令牌，请稍后重试';
      final Map<String, dynamic> profile =
          (data['account'] as Map?)?.cast<String, dynamic>() ??
              const <String, dynamic>{};
      await _store.save(token: token, account: profile);
      await _store.setForceLogin(false);
      _applyToken(token);
      notifyListeners();
      return null;
    } on ApiException catch (error) {
      if (error.detail.contains('已在其他设备登录')) {
        await _store.setForceLogin(true);
      }
      notifyListeners();
      return ErrorMapper.toUserMessage(error);
    } catch (_) {
      return '网络异常，请稍后重试';
    }
  }

  /// 修改密码。成功返回 null，否则返回用户可读错误文案。
  Future<String?> changePassword({
    required String currentPassword,
    required String newPassword,
  }) async {
    if (identifier.isEmpty) return '缺少账号信息，请重新登录后再试';
    try {
      await account.changePassword(
        identifier: identifier,
        currentPassword: currentPassword,
        newPassword: newPassword,
      );
      return null;
    } on ApiException catch (error) {
      return ErrorMapper.toUserMessage(error);
    } catch (_) {
      return '网络异常，请稍后重试';
    }
  }

  /// 发送注册邮箱验证码。成功返回 `(冷却秒数, null)`，失败返回 `(null, 错误文案)`。
  Future<(int?, String?)> sendRegisterEmailCode({required String email}) async {
    try {
      final int cooldown = await account.sendRegisterEmailCode(email: email);
      return (cooldown, null);
    } on ApiException catch (error) {
      return (null, _registerErrorMessage(error));
    } catch (_) {
      return (null, '网络异常，请稍后重试');
    }
  }

  /// 注册账号。成功返回 null，否则返回用户可读错误文案。
  Future<String?> register({
    required String username,
    required String email,
    required String emailCode,
    required String password,
    String invitationCode = '',
  }) async {
    try {
      await account.register(
        username: username,
        email: email,
        emailCode: emailCode,
        password: password,
        invitationCode: invitationCode,
      );
      return null;
    } on ApiException catch (error) {
      return _registerErrorMessage(error);
    } catch (_) {
      return '网络异常，请稍后重试';
    }
  }

  Future<void> logout() async {
    try {
      await account.logout();
    } catch (_) {
      // 忽略：本地会话仍需清除
    }
    await _store.clearCloudSession();
    _applyToken(null);
    notifyListeners();
  }

  /// 会话失效（401 命中失效文案）：清云端会话，[isLoggedIn] 变 false 后
  /// 根部 RootGate 自动回登录页。
  Future<void> handleSessionInvalid() async {
    if (!isLoggedIn) return;
    await _store.clearCloudSession();
    _applyToken(null);
    notifyListeners();
  }
}

/// 注册 / 注册验证码接口的后端文案映射（后端为英文固定串，转成用户可读中文）。
String _registerErrorMessage(ApiException error) {
  const Map<String, String> known = <String, String>{
    'username is required': '请输入用户名',
    'a valid 6-digit email code is required': '请输入 6 位邮箱验证码',
    'password must be at least 6 characters': '密码至少 6 位',
    'invitation code is invalid': '邀请码无效',
    'invitation code has expired': '邀请码已过期',
    'invitation code has been used up': '邀请码已被用尽',
    'invalid or expired email code': '邮箱验证码错误或已过期',
    'username or email already exists': '用户名或邮箱已被注册',
    'please wait 60 seconds before requesting another email code': '操作过于频繁，请稍后再试',
    'too many email code requests; please try again later': '请求过于频繁，请稍后再试',
  };
  for (final MapEntry<String, String> entry in known.entries) {
    if (error.detail.contains(entry.key)) return entry.value;
  }
  return ErrorMapper.toUserMessage(error);
}
