import '../config/app_config.dart';
import '../http/api_client.dart';
import '../http/lan_client.dart';

/// 局域网（PC 工作台）连通服务。
///
/// PC 端 `/api/customer/*` 与云端同契约，用同一账号登录后拿到
/// `wh_local_*` 本地 token（**会触发单端登录互踢**，调用方需二次确认）。
class LanApi {
  LanApi(this._lan);

  final LanClient _lan;

  /// 规范化 PC 地址：补 `http://` 前缀、补默认端口，去掉末尾斜杠。
  ///
  /// `192.168.1.24` → `http://192.168.1.24:8010`
  static String normalizeBaseUrl(String input) {
    String value = input.trim();
    if (value.isEmpty) return '';
    if (!value.startsWith('http://') && !value.startsWith('https://')) {
      value = 'http://$value';
    }
    final Uri? uri = Uri.tryParse(value);
    if (uri == null || uri.host.isEmpty) return '';
    final int port =
        uri.hasPort ? uri.port : AppConfig.lanDefaultPort;
    return '${uri.scheme}://${uri.host}:$port';
  }

  /// 用当前账号登录 PC 工作台，返回本地 token。
  Future<String> login({
    required String baseUrl,
    required String identifier,
    required String password,
  }) async {
    final ApiClient client = ApiClient(baseUrl);
    final String trimmed = identifier.trim();
    final Map<String, dynamic> data = await client.post(
      AppConfig.lanLoginPath,
      auth: false,
      body: <String, dynamic>{
        if (trimmed.contains('@')) 'email': trimmed else 'username': trimmed,
        'password': password,
        // 手机端经 PC 局域网通道登录时同样上报 client_type=mobile，
        // 云端据此把该会话归入手机端分组，避免与 PC 端会话互相顶替。
        'client_type': 'mobile',
      },
    );
    return data['token']?.toString() ?? '';
  }

  /// 探活：能连上即视为 PC 在线。
  ///
  /// `/api/customer/me` 需要 Bearer，未登录会回 401 —— 这仍然说明 PC 可达，
  /// 因此把 401/403 也算作在线，只有网络层失败才判定离线。
  Future<bool> probe({String? baseUrl, String? token}) async {
    final String target = (baseUrl ?? _lan.baseUrl).trim();
    if (target.isEmpty) return false;
    final ApiClient client = ApiClient(target);
    if (token != null && token.isNotEmpty) client.setToken(token);
    try {
      await client.get(AppConfig.lanHealthPath);
      return true;
    } on ApiException catch (error) {
      return error.statusCode == 401 ||
          error.statusCode == 403 ||
          error.statusCode == 404;
    } catch (_) {
      return false;
    }
  }
}
