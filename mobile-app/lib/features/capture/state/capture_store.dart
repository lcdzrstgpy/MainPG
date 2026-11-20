import 'package:flutter/foundation.dart';

import '../../../core/api/capture_api.dart';
import '../../../core/storage/capture_queue_store.dart';
import '../../../core/util/time_format.dart';

/// 从粘贴文本中提取商品链接。
///
/// 淘宝 / 天猫 / 1688 的「复制链接」复制的是整段分享文案（形如
/// `【淘宝】大促价保 https://e.tb.cn/xxx HU006 「商品名」…`），URL 夹在文案中间，
/// 因此不能要求文本以 http 开头，需按正则取第一个链接。
String extractCaptureLink(String raw) {
  final String text = raw.trim();
  if (text.isEmpty) return '';
  final RegExpMatch? match =
      RegExp(r'https?://[A-Za-z0-9\-._~:/?#@!$&*+,;=%]+').firstMatch(text);
  return match?.group(0) ?? '';
}

/// 采集记录状态。
///
/// 手机端采集会把链接同步到云端（账号维度），PC 端同账号登录后可在采集页
/// 「手机端导入」中拉取并一键导入采集流程：
/// - [synced] 已同步到云端，等待 PC 端导入
/// - [pendingSync] 本机已登记，但云端同步失败（网络异常），需重试
/// - [failed] 登记失败（链接不可识别）
enum CaptureStatus { synced, pendingSync, failed }

extension CaptureStatusLabel on CaptureStatus {
  String get label {
    switch (this) {
      case CaptureStatus.synced:
        return '待 PC 导入';
      case CaptureStatus.pendingSync:
        return '未同步';
      case CaptureStatus.failed:
        return '登记失败';
    }
  }
}

/// 一条采集记录（本机登记 + 云端同步状态）。
class CaptureRecord {
  const CaptureRecord({
    required this.id,
    required this.title,
    required this.site,
    required this.link,
    required this.createdAt,
    this.status = CaptureStatus.synced,
    this.message,
  });

  final String id;
  final String title;

  /// 淘宝 / 1688 / 天猫 / Temu
  final String site;
  final String link;

  /// 本机登记时间（ISO 8601），展示时切片，不换算时区
  final String createdAt;
  final CaptureStatus status;

  /// 失败原因
  final String? message;

  /// 形如 `10-08 09:32`
  String get time => formatShort(createdAt);

  CaptureRecord copyWith({CaptureStatus? status, String? message}) {
    return CaptureRecord(
      id: id,
      title: title,
      site: site,
      link: link,
      createdAt: createdAt,
      status: status ?? this.status,
      message: message ?? this.message,
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'title': title,
        'site': site,
        'link': link,
        'createdAt': createdAt,
        'status': status.name,
        'message': message,
      };

  factory CaptureRecord.fromJson(Map<String, dynamic> json) {
    final String rawStatus = json['status']?.toString() ?? '';
    return CaptureRecord(
      id: json['id']?.toString() ?? '',
      title: json['title']?.toString() ?? '',
      site: json['site']?.toString() ?? '',
      link: json['link']?.toString() ?? '',
      createdAt: json['createdAt']?.toString() ?? '',
      status: switch (rawStatus) {
        'failed' => CaptureStatus.failed,
        'pendingSync' => CaptureStatus.pendingSync,
        _ => CaptureStatus.synced,
      },
      message: json['message']?.toString(),
    );
  }
}

/// 采集队列状态：本机登记 + 云端同步 + 跨进程持久化。
class CaptureStore extends ChangeNotifier {
  CaptureStore(this._queue, this._api);

  final CaptureQueueStore _queue;
  final CaptureApi _api;

  final List<CaptureRecord> _records = <CaptureRecord>[];
  bool _inited = false;

  List<CaptureRecord> get records => List<CaptureRecord>.unmodifiable(_records);

  bool get isInited => _inited;

  /// 已同步到云端、等待 PC 端导入的条数
  int get pendingCount => _records
      .where((CaptureRecord r) => r.status == CaptureStatus.synced)
      .length;

  /// 云端同步失败的条数
  int get unsyncedCount => _records
      .where((CaptureRecord r) => r.status == CaptureStatus.pendingSync)
      .length;

  int get failedCount =>
      _records.where((CaptureRecord r) => r.status == CaptureStatus.failed).length;

  int get todayCount => _records.length;

  CaptureRecord? byId(String? id) {
    if (id == null) return null;
    for (final CaptureRecord r in _records) {
      if (r.id == id) return r;
    }
    return null;
  }

  /// 识别链接所属站点
  String siteOf(String link) {
    final String v = link.toLowerCase();
    if (v.contains('1688.com')) return '1688';
    if (v.contains('tmall.com')) return '天猫';
    if (v.contains('temu.com')) return 'Temu';
    return '淘宝';
  }

  Future<void> init() async {
    if (_inited) return;
    final List<Map<String, dynamic>> raw = await _queue.load();
    _records
      ..clear()
      ..addAll(
        raw.map(CaptureRecord.fromJson).where((CaptureRecord r) => r.link.isNotEmpty),
      );
    _inited = true;
    notifyListeners();
  }

  /// 登记一条采集链接：先同步到云端，再落本机。
  ///
  /// 返回用户可读错误文案，成功返回 null。
  Future<String?> submit({
    required String link,
    required String site,
    String deviceName = '',
  }) async {
    final String raw = link.trim();
    final String trimmed = extractCaptureLink(raw);
    final String now = DateTime.now().toString();
    final String id = 'c${DateTime.now().millisecondsSinceEpoch}';

    if (trimmed.isEmpty) {
      final CaptureRecord failed = CaptureRecord(
        id: id,
        title: '无法识别的链接',
        site: site,
        link: raw,
        createdAt: now,
        status: CaptureStatus.failed,
        message: '不是有效的商品链接，请重新粘贴',
      );
      _records.insert(0, failed);
      await _persist();
      notifyListeners();
      return '不是有效的商品链接，请重新粘贴';
    }

    // 云端同步是 PC 端「手机端导入」的前提，失败时降级为本机登记（未同步）。
    String? syncError;
    try {
      await _api.submit(
        captureId: id,
        sourceUrl: trimmed,
        sourceSite: site,
        deviceName: deviceName,
      );
    } catch (_) {
      syncError = '云端同步失败，请检查网络后重试';
    }

    _records.insert(
      0,
      CaptureRecord(
        id: id,
        title: '新采集商品 · 待 PC 端解析标题',
        site: site,
        link: trimmed,
        createdAt: now,
        status: syncError == null ? CaptureStatus.synced : CaptureStatus.pendingSync,
        message: syncError,
      ),
    );
    await _persist();
    notifyListeners();
    return syncError;
  }

  Future<void> remove(String id) async {
    _records.removeWhere((CaptureRecord r) => r.id == id);
    await _persist();
    notifyListeners();
    // 尽力同步删除云端登记，避免 PC 端仍可拉到已删除项。
    try {
      await _api.delete(id);
    } catch (_) {
      // 忽略：本机已删除，云端残留不影响本机使用。
    }
  }

  Future<void> clearAll() async {
    _records.clear();
    await _queue.clear();
    notifyListeners();
  }

  Future<void> _persist() async {
    await _queue.save(_records.map((CaptureRecord r) => r.toJson()).toList());
  }
}
