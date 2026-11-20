import 'json_utils.dart';

/// 公告（`publish-api /api/announcements/public` 条目）
///
/// 匿名条目字段：`id/title/content/published_at/image_count/image_rev`；
/// 定向公告额外带 `account_id`，据此判定 [directed]。
class Announcement {
  const Announcement({
    required this.id,
    required this.title,
    required this.content,
    required this.publishedAt,
    required this.imageCount,
    required this.imageRev,
    required this.directed,
  });

  final int id;
  final String title;
  final String content;
  final String publishedAt;
  final int imageCount;
  final int imageRev;
  final bool directed;

  /// 本机已读键（与反馈回复的 `fb_` 前缀区分）
  String get localKey => 'ann_$id';

  bool get hasImages => imageCount > 0;

  factory Announcement.fromJson(Map<String, dynamic> json) {
    return Announcement(
      id: asInt(json['id']),
      title: asString(json['title'], '（无标题公告）'),
      content: asString(json['content']),
      publishedAt: asString(json['published_at']),
      imageCount: asInt(json['image_count']),
      imageRev: asInt(json['image_rev']),
      directed: asString(json['account_id']).isNotEmpty,
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'title': title,
        'content': content,
        'published_at': publishedAt,
        'image_count': imageCount,
        'image_rev': imageRev,
        'account_id': directed ? 'directed' : '',
      };
}

/// 管理员对用户反馈的回复（`/api/feedback-replies/public` 条目）
class FeedbackReply {
  const FeedbackReply({
    required this.id,
    required this.title,
    required this.content,
    required this.createdAt,
  });

  final int id;
  final String title;
  final String content;
  final String createdAt;

  /// 与公告 id 隔离的本地键
  String get localKey => 'fb_$id';

  factory FeedbackReply.fromJson(Map<String, dynamic> json) {
    return FeedbackReply(
      id: asInt(json['id']),
      title: asString(json['title'], '反馈回复'),
      content: asString(json['content']),
      createdAt: asString(json['created_at'], asString(json['published_at'])),
    );
  }

  Map<String, dynamic> toJson() => <String, dynamic>{
        'id': id,
        'title': title,
        'content': content,
        'created_at': createdAt,
      };
}

/// 公告图片（`/api/announcements/{id}/images` → `images[]`）
///
/// 元素形如 `{name, mime, size, data}`，`data` 为 base64 本体。
class AnnouncementImage {
  const AnnouncementImage({required this.data});

  final String data;

  factory AnnouncementImage.fromJson(Map<String, dynamic> json) {
    return AnnouncementImage(data: asString(json['data']));
  }
}

/// 从响应中提取列表：兼容 `announcements`（实测）与 `items`（旧文档）两种键。
List<Map<String, dynamic>> extractList(Map<String, dynamic> payload) {
  final dynamic raw = payload['announcements'] ?? payload['items'];
  return asList(raw)
      .whereType<Map>()
      .map((Map item) => item.cast<String, dynamic>())
      .toList();
}

/// 已读字段归一化（`true|1|"1"|"true"`）
bool normalizeRead(dynamic value) => asBool(value);
