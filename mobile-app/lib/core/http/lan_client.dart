import '../config/app_config.dart';
import 'api_client.dart';

/// 通道 B：局域网直连 PC 工作台（需 PC 在线）。
///
/// 基址由「扫码配对」写入，可在设置页手动修改；失效时上层应降级为仅云端可用。
class LanClient {
  LanClient({ApiClient? client})
      : client = client ?? ApiClient(AppConfig.defaultLanBaseUrl);

  final ApiClient client;

  String get baseUrl => client.baseUrl;
  bool get isConfigured => client.baseUrl.isNotEmpty;

  void useBaseUrl(String value) => client.baseUrl = value;

  void setToken(String? token) => client.setToken(token);
}
