import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// 采集队列本地持久化：`wh_mobile_capture_queue`。
///
/// 采集链接会同步到云端账号（PC 端「手机端导入」拉取），本队列仍在本地保留一份，
/// 用于离线展示与跨进程恢复。
class CaptureQueueStore {
  static const String _kQueue = 'wh_mobile_capture_queue';

  SharedPreferences? _prefs;

  Future<void> init() async {
    _prefs ??= await SharedPreferences.getInstance();
  }

  Future<List<Map<String, dynamic>>> load() async {
    await init();
    final String? raw = _prefs!.getString(_kQueue);
    if (raw == null || raw.isEmpty) return <Map<String, dynamic>>[];
    try {
      final dynamic decoded = jsonDecode(raw);
      if (decoded is! List) return <Map<String, dynamic>>[];
      return decoded
          .whereType<Map>()
          .map((Map item) => item.cast<String, dynamic>())
          .toList();
    } catch (_) {
      return <Map<String, dynamic>>[];
    }
  }

  Future<void> save(List<Map<String, dynamic>> items) async {
    await init();
    await _prefs!.setString(_kQueue, jsonEncode(items));
  }

  Future<void> clear() async {
    await init();
    await _prefs!.remove(_kQueue);
  }
}
