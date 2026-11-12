import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config/app_config.dart';

/// 双通道 HTTP 客户端基类。
///
/// - `CloudClient`：云端直连（auth-api / publish-api），免 PC 可用
/// - `LanClient`：局域网直连 PC 工作台，需 PC 在线
class ApiClient {
  ApiClient(this.baseUrl, {http.Client? httpClient})
      : _http = httpClient ?? http.Client();

  String baseUrl;
  final http.Client _http;
  String? _token;

  void setToken(String? token) => _token = token;

  Map<String, String> _headers({bool auth = true, String? tokenHeader}) {
    final headers = <String, String>{
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    };
    final token = _token;
    if (token != null && token.isNotEmpty) {
      if (auth) headers['Authorization'] = 'Bearer $token';
      if (tokenHeader != null) headers[tokenHeader] = token;
    }
    return headers;
  }

  Uri _uri(String path, [Map<String, dynamic>? query]) {
    final normalized = path.startsWith('/') ? path : '/$path';
    final uri = Uri.parse('$baseUrl$normalized');
    if (query == null || query.isEmpty) return uri;
    return uri.replace(
      queryParameters: query.map((key, value) => MapEntry(key, '$value')),
    );
  }

  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, dynamic>? query,
    bool auth = true,
    String? tokenHeader,
  }) async {
    final response = await _http
        .get(_uri(path, query), headers: _headers(auth: auth, tokenHeader: tokenHeader))
        .timeout(AppConfig.requestTimeout);
    return _decode(response);
  }

  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    Map<String, dynamic>? query,
    bool auth = true,
    String? tokenHeader,
  }) async {
    final response = await _http
        .post(
          _uri(path, query),
          headers: _headers(auth: auth, tokenHeader: tokenHeader),
          body: jsonEncode(body ?? const <String, dynamic>{}),
        )
        .timeout(AppConfig.requestTimeout);
    return _decode(response);
  }

  Future<Map<String, dynamic>> delete(
    String path, {
    bool auth = true,
  }) async {
    final response = await _http
        .delete(_uri(path), headers: _headers(auth: auth))
        .timeout(AppConfig.requestTimeout);
    return _decode(response);
  }

  Map<String, dynamic> _decode(http.Response response) {
    final raw = response.body.trim();
    final decoded = raw.isEmpty ? const <String, dynamic>{} : jsonDecode(raw);
    if (decoded is! Map<String, dynamic>) {
      throw ApiException(statusCode: response.statusCode, detail: '响应格式异常');
    }
    if (response.statusCode >= 400) {
      throw ApiException(
        statusCode: response.statusCode,
        detail: decoded['detail']?.toString() ??
            decoded['message']?.toString() ??
            '请求失败',
        raw: decoded,
      );
    }
    return decoded;
  }
}

/// 统一的接口异常，携带 HTTP 状态码与后端 detail 文案。
class ApiException implements Exception {
  ApiException({
    required this.statusCode,
    required this.detail,
    this.raw = const <String, dynamic>{},
  });

  final int statusCode;
  final String detail;
  final Map<String, dynamic> raw;

  @override
  String toString() => 'ApiException($statusCode): $detail';
}
