import '../config/app_config.dart';
import '../http/cloud_client.dart';
import '../models/message_models.dart';

/// 消息服务（publish-api 公告 / 反馈回复 + auth-api 反馈提交）。
///
/// publish-api 的身份走 `x-auth-token` 头（非 Bearer），由 [CloudClient]
/// 注入同一份 sessionToken。
class MessageApi {
  MessageApi(this._cloud);

  final CloudClient _cloud;

  /// 公告列表：全员公告 + （带身份时）发给当前账号的定向公告。
  Future<List<Announcement>> publicAnnouncements({String accountId = ''}) async {
    final Map<String, dynamic> data = await _cloud.publish.get(
      AppConfig.publicAnnouncementsPath,
      auth: false,
      tokenHeader: AppConfig.publishTokenHeader,
      query: <String, dynamic>{if (accountId.isNotEmpty) 'account_id': accountId},
    );
    return extractList(data)
        .map(Announcement.fromJson)
        .where((Announcement item) => item.id > 0)
        .toList();
  }

  /// 反馈回复列表（发给当前账号）。
  Future<List<FeedbackReply>> feedbackReplies({String accountId = ''}) async {
    final Map<String, dynamic> data = await _cloud.publish.get(
      AppConfig.publicFeedbackRepliesPath,
      auth: false,
      tokenHeader: AppConfig.publishTokenHeader,
      query: <String, dynamic>{if (accountId.isNotEmpty) 'account_id': accountId},
    );
    return extractList(data)
        .map(FeedbackReply.fromJson)
        .where((FeedbackReply item) => item.id > 0)
        .toList();
  }

  /// 公告图片本体（base64）。
  Future<List<AnnouncementImage>> announcementImages(int id) async {
    final Map<String, dynamic> data = await _cloud.publish.get(
      AppConfig.announcementImagesPath(id),
      auth: false,
      tokenHeader: AppConfig.publishTokenHeader,
    );
    return (data['images'] is List ? data['images'] as List : const <dynamic>[])
        .whereType<Map>()
        .map((Map item) => AnnouncementImage.fromJson(item.cast<String, dynamic>()))
        .where((AnnouncementImage item) => item.data.isNotEmpty)
        .toList();
  }

  /// 提交反馈（反馈回复页的继续回复）。
  Future<void> submitFeedback({
    required String content,
    String category = 'other',
    String contact = '',
  }) async {
    await _cloud.auth.post(
      AppConfig.feedbackPath,
      body: <String, dynamic>{
        'content': content,
        'category': category,
        if (contact.isNotEmpty) 'contact': contact,
        'app_version': AppConfig.appVersion,
      },
    );
  }

  /// 我的反馈记录（含管理员回复）。
  Future<List<Map<String, dynamic>>> myFeedback({
    int limit = 20,
    int offset = 0,
  }) async {
    final Map<String, dynamic> data = await _cloud.auth.get(
      AppConfig.feedbackMinePath,
      query: <String, dynamic>{'limit': limit, 'offset': offset},
    );
    final dynamic items = data['feedback'];
    if (items is! List) return <Map<String, dynamic>>[];
    return items
        .whereType<Map>()
        .map((Map item) => item.cast<String, dynamic>())
        .toList();
  }
}
