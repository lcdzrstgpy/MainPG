import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// token / account / 设备本地存储。
///
/// - 敏感项（云端 sessionToken、局域网 lanToken）：`flutter_secure_storage`
/// - 非敏感项（account 摘要、局域网基址、设备列表、当前设备）：`SharedPreferences`
class TokenStore {
  static const String _kToken = 'wh_mobile_token';
  static const String _kAccount = 'wh_mobile_account';
  static const String _kForceLogin = 'wh_mobile_force_login';
  static const String _kLanToken = 'wh_mobile_lan_token';
  static const String _kLanBaseUrl = 'wh_mobile_lan_base_url';
  static const String _kLanOnly = 'wh_mobile_lan_only';
  static const String _kDevices = 'wh_mobile_devices';
  static const String _kCurrentDeviceId = 'wh_mobile_current_device';

  final FlutterSecureStorage _secure = const FlutterSecureStorage();
  SharedPreferences? _prefs;

  String? token;
  String? lanToken;
  Map<String, dynamic> account = const <String, dynamic>{};
  bool forceLogin = false;

  /// 局域网基址，形如 `http://192.168.1.24:8010`（空串=未配置）
  String lanBaseUrl = '';

  /// 是否仅使用局域网通道
  bool lanOnly = false;

  /// 已登记的设备：`{id, name, baseUrl}`
  List<Map<String, dynamic>> devices = <Map<String, dynamic>>[];

  /// 当前设备 id
  String currentDeviceId = '';

  Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
    token = await _secure.read(key: _kToken);
    lanToken = await _secure.read(key: _kLanToken);

    final String? rawAccount = _prefs!.getString(_kAccount);
    if (rawAccount != null && rawAccount.isNotEmpty) {
      account = (jsonDecode(rawAccount) as Map).cast<String, dynamic>();
    }

    final String? rawDevices = _prefs!.getString(_kDevices);
    if (rawDevices != null && rawDevices.isNotEmpty) {
      final dynamic decoded = jsonDecode(rawDevices);
      if (decoded is List) {
        devices = decoded
            .whereType<Map>()
            .map((Map item) => item.cast<String, dynamic>())
            .toList();
      }
    }

    lanBaseUrl = _prefs!.getString(_kLanBaseUrl) ?? '';
    currentDeviceId = _prefs!.getString(_kCurrentDeviceId) ?? '';
    lanOnly = _prefs!.getBool(_kLanOnly) ?? false;
    forceLogin = _prefs!.getBool(_kForceLogin) ?? false;
  }

  Future<void> save({
    required String token,
    required Map<String, dynamic> account,
  }) async {
    this.token = token;
    this.account = account;
    await _secure.write(key: _kToken, value: token);
    await _prefs!.setString(_kAccount, jsonEncode(account));
  }

  Future<void> setForceLogin(bool value) async {
    forceLogin = value;
    await _prefs!.setBool(_kForceLogin, value);
  }

  // ---------------------------------------------------------------- 局域网

  Future<void> saveLanToken(String value) async {
    lanToken = value;
    await _secure.write(key: _kLanToken, value: value);
  }

  Future<void> setLanBaseUrl(String value) async {
    lanBaseUrl = value;
    await _prefs!.setString(_kLanBaseUrl, value);
  }

  Future<void> setLanOnly(bool value) async {
    lanOnly = value;
    await _prefs!.setBool(_kLanOnly, value);
  }

  Future<void> saveDevices(
    List<Map<String, dynamic>> value, {
    required String currentId,
  }) async {
    devices = value;
    currentDeviceId = currentId;
    await _prefs!.setString(_kDevices, jsonEncode(value));
    await _prefs!.setString(_kCurrentDeviceId, currentId);
  }

  /// 仅清除云端会话，保留设备与局域网配置。
  Future<void> clearCloudSession() async {
    token = null;
    account = const <String, dynamic>{};
    forceLogin = false;
    await _secure.delete(key: _kToken);
    await _prefs!.remove(_kAccount);
    await _prefs!.remove(_kForceLogin);
  }

  Future<void> clear() async {
    token = null;
    lanToken = null;
    account = const <String, dynamic>{};
    forceLogin = false;
    lanBaseUrl = '';
    lanOnly = false;
    devices = <Map<String, dynamic>>[];
    currentDeviceId = '';
    await _secure.delete(key: _kToken);
    await _secure.delete(key: _kLanToken);
    await _prefs!.remove(_kAccount);
    await _prefs!.remove(_kForceLogin);
    await _prefs!.remove(_kLanBaseUrl);
    await _prefs!.remove(_kLanOnly);
    await _prefs!.remove(_kDevices);
    await _prefs!.remove(_kCurrentDeviceId);
  }
}
