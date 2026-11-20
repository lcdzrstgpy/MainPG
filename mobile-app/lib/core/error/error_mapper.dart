import '../http/api_client.dart';

/// 错误文案映射，对齐 Web 端 `transport/http/client.ts` 的 `toUserMessage`。
class ErrorMapper {
  ErrorMapper._();

  static const List<String> _sessionInvalidHints = <String>[
    '会话已失效',
    '请重新登录',
    '登录状态已过期',
    'session revoked',
  ];

  /// 裸 401 不一定代表会话失效：只有 detail 命中失效文案才应清 token。
  static bool isSessionInvalid(ApiException error) {
    if (error.statusCode == 401 || error.statusCode == 403) {
      return _sessionInvalidHints.any(error.detail.contains);
    }
    return false;
  }

  static String toUserMessage(ApiException error) {
    if (error.statusCode == 503) {
      return '服务繁忙，请稍后重试';
    }
    if (error.statusCode == 401 || error.statusCode == 403) {
      return isSessionInvalid(error) ? '登录状态已失效，请重新登录' : error.detail;
    }
    return error.detail.isNotEmpty ? error.detail : '请求失败，请稍后重试';
  }
}
