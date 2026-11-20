import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// 消息本地缓存与已读表。
///
/// - 缓存：`wh_mobile_msg_cache`，存接口原始条目，进页先渲染再拉网络覆盖
/// - 已读：`wh_mobile_msg_read`，存本机维护的消息键（`ann_<id>` / `fb_<id>`）
class MessageStore {
  static const String _kCache = 'wh_mobile_msg_cache';
  static const String _kRead = 'wh_mobile_msg_read';

  SharedPreferences? _prefs;

  Future<void> init() async {
    _prefs ??= await SharedPreferences.getInstance();
  }

  Future<List<Map<String, dynamic>>> loadCache(String key) async {
    await init();
    final String? raw = _prefs!.getString(_kCache);
    if (raw == null || raw.isEmpty) return <Map<String, dynamic>>[];
    try {
      final dynamic decoded = jsonDecode(raw);
      if (decoded is! Map) return <Map<String, dynamic>>[];
      final dynamic items = decoded[key];
      if (items is! List) return <Map<String, dynamic>>[];
      return items
          .whereType<Map>()
          .map((Map item) => item.cast<String, dynamic>())
          .toList();
    } catch (_) {
      return <Map<String, dynamic>>[];
    }
  }

  Future<void> saveCache(String key, List<Map<String, dynamic>> items) async {
    await init();
    Map<String, dynamic> cache = <String, dynamic>{};
    final String? raw = _prefs!.getString(_kCache);
    if (raw != null && raw.isNotEmpty) {
      try {
        final dynamic decoded = jsonDecode(raw);
        if (decoded is Map) cache = decoded.cast<String, dynamic>();
      } catch (_) {
        cache = <String, dynamic>{};
      }
    }
    cache[key] = items;
    await _prefs!.setString(_kCache, jsonEncode(cache));
  }

  Future<Set<String>> loadReadIds() async {
    await init();
    return (_prefs!.getStringList(_kRead) ?? const <String>[]).toSet();
  }

  Future<void> saveReadIds(Set<String> ids) async {
    await init();
    await _prefs!.setStringList(_kRead, ids.toList());
  }

  /// 清除消息缓存（保留已读表，避免用户重进后未读红点回弹）。
  Future<void> clearCache() async {
    await init();
    await _prefs!.remove(_kCache);
  }
}
