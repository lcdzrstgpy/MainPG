import 'package:flutter/foundation.dart';

import '../../../core/api/message_api.dart';
import '../../../core/error/error_mapper.dart';
import '../../../core/http/api_client.dart';
import '../../../core/models/message_models.dart';
import '../../../core/storage/message_store.dart';

/// 消息中心状态：公告 + 反馈回复 + 本机已读表。
///
/// 装配顺序固定为「先本地缓存后网络覆盖」：冷启动即可渲染上次内容，
/// 拉取失败时保留缓存并给出错误提示，不把列表清空。
class InboxStore extends ChangeNotifier {
  InboxStore(this._api, this._store);

  final MessageApi _api;
  final MessageStore _store;

  static const String _kAnnouncements = 'announcements';
  static const String _kReplies = 'replies';

  bool loading = false;
  bool loaded = false;
  String? error;

  List<Announcement> announcements = <Announcement>[];
  List<FeedbackReply> feedbackReplies = <FeedbackReply>[];

  Set<String> _readIds = <String>{};
  String _accountId = '';

  /// 当前登录账号 id，用于拉取定向公告 / 反馈回复。
  void setAccountId(String value) => _accountId = value.trim();

  /// 未读数 = 公告 + 反馈回复中本机未读的条数。
  int get unreadCount {
    int count = 0;
    for (final Announcement item in announcements) {
      if (!_readIds.contains(item.localKey)) count++;
    }
    for (final FeedbackReply item in feedbackReplies) {
      if (!_readIds.contains(item.localKey)) count++;
    }
    return count;
  }

  bool isRead(String localKey) => _readIds.contains(localKey);

  /// 冷启动入口：读缓存 → 渲染 → 拉网络覆盖。
  Future<void> bootstrap() async {
    await _store.init();
    _readIds = await _store.loadReadIds();

    final List<Map<String, dynamic>> cachedAnnouncements =
        await _store.loadCache(_kAnnouncements);
    final List<Map<String, dynamic>> cachedReplies =
        await _store.loadCache(_kReplies);
    if (cachedAnnouncements.isNotEmpty || cachedReplies.isNotEmpty) {
      announcements = cachedAnnouncements
          .map(Announcement.fromJson)
          .where((Announcement item) => item.id > 0)
          .toList();
      feedbackReplies = cachedReplies
          .map(FeedbackReply.fromJson)
          .where((FeedbackReply item) => item.id > 0)
          .toList();
      loaded = true;
      notifyListeners();
    }
    await load();
  }

  /// 拉取公告与反馈回复（两者并行）。
  Future<void> load() async {
    loading = true;
    error = null;
    notifyListeners();
    try {
      final List<Object> results = await Future.wait(<Future<Object>>[
        _api.publicAnnouncements(accountId: _accountId),
        _api.feedbackReplies(accountId: _accountId),
      ]);
      announcements = results[0] as List<Announcement>;
      feedbackReplies = results[1] as List<FeedbackReply>;
      loaded = true;
      loading = false;
      notifyListeners();
      await _store.saveCache(
        _kAnnouncements,
        announcements.map((Announcement item) => item.toJson()).toList(),
      );
      await _store.saveCache(
        _kReplies,
        feedbackReplies.map((FeedbackReply item) => item.toJson()).toList(),
      );
    } on ApiException catch (e) {
      error = ErrorMapper.toUserMessage(e);
      loading = false;
      notifyListeners();
    } catch (_) {
      error = '网络异常，请稍后重试';
      loading = false;
      notifyListeners();
    }
  }

  Future<void> refresh() => load();

  /// 公告图片（base64），供公告详情页按需加载。
  Future<List<AnnouncementImage>> loadImages(int id) => _api.announcementImages(id);

  /// 提交反馈 / 继续回复。返回用户可读错误文案，成功返回 null。
  Future<String?> submitFeedback({
    required String content,
    String category = 'other',
  }) async {
    try {
      await _api.submitFeedback(content: content, category: category);
      await load();
      return null;
    } on ApiException catch (e) {
      return ErrorMapper.toUserMessage(e);
    } catch (_) {
      return '网络异常，请稍后重试';
    }
  }

  /// 清除消息本地缓存（保留已读表，避免红点回弹）。
  Future<void> clearCache() => _store.clearCache();

  Future<void> markRead(String localKey) async {
    if (_readIds.contains(localKey)) return;
    _readIds = <String>{..._readIds, localKey};
    notifyListeners();
    await _store.saveReadIds(_readIds);
  }

  Future<void> markAllRead() async {
    final Set<String> next = <String>{
      ..._readIds,
      ...announcements.map((Announcement item) => item.localKey),
      ...feedbackReplies.map((FeedbackReply item) => item.localKey),
    };
    if (next.length == _readIds.length) return;
    _readIds = next;
    notifyListeners();
    await _store.saveReadIds(_readIds);
  }

  void clear() {
    loading = false;
    loaded = false;
    error = null;
    announcements = <Announcement>[];
    feedbackReplies = <FeedbackReply>[];
    _readIds = <String>{};
    notifyListeners();
  }
}
