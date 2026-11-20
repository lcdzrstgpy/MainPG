import '../config/app_config.dart';
import 'api_client.dart';

/// 通道 A：云端直连（免 PC）。
///
/// 本地 `/api/customer/*` 是云端 customer-auth 的透明代理，路径完全一致，
/// 因此 App 只需把基址换成 auth-api，契约零改动。
class CloudClient {
  CloudClient({ApiClient? auth, ApiClient? publish})
      : auth = auth ?? ApiClient(AppConfig.authBaseUrl),
        publish = publish ?? ApiClient(AppConfig.publishBaseUrl);

  /// 账号 / 计费 / 充值 / 订单 / 设备绑定
  final ApiClient auth;

  /// 公告 / 反馈回复（使用 x-auth-token 头）
  final ApiClient publish;

  void setToken(String? token) {
    auth.setToken(token);
    publish.setToken(token);
  }
}
