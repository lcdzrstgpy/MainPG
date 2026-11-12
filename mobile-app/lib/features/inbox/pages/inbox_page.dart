import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../core/models/message_models.dart';
import '../../../core/util/time_format.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';
import '../../home/pages/announce_page.dart';
import '../state/inbox_store.dart';
import 'message_detail_page.dart';

/// 消息：公告 / 反馈回复分段 · 未读点 · 全部已读
class InboxPage extends StatefulWidget {
  const InboxPage({super.key});

  @override
  State<InboxPage> createState() => _InboxPageState();
}

class _InboxPageState extends State<InboxPage> {
  int _seg = 0;

  Future<void> _readAll(InboxStore inbox) async {
    if (inbox.unreadCount == 0) {
      showAppToast(context, '没有未读消息');
      return;
    }
    await inbox.markAllRead();
    if (!mounted) return;
    showAppToast(context, '已全部标记为已读');
  }

  @override
  Widget build(BuildContext context) {
    final InboxStore inbox = context.watch<InboxStore>();

    return AppScreen(
      navBar: AppNavBar(
        title: '消息',
        leading: const SizedBox(width: 30),
        actions: <Widget>[
          AppNavIconButton(
            icon: Icons.check_circle_outline,
            onTap: () => _readAll(inbox),
          ),
        ],
      ),
      child: RefreshIndicator(
        onRefresh: inbox.refresh,
        child: ListView(
          padding: EdgeInsets.only(
            bottom: tabPageBottomInset(context) + AppTokens.gap16,
          ),
          physics: const AlwaysScrollableScrollPhysics(),
          children: <Widget>[
            AppSegment(
              items: const <String>['公告', '反馈回复'],
              index: _seg,
              onChanged: (int index) => setState(() => _seg = index),
            ),
            const SizedBox(height: AppTokens.gap8),
            if (inbox.error != null && !inbox.loaded)
              _error(inbox)
            else if (inbox.loading && !inbox.loaded)
              const Padding(
                padding: EdgeInsets.only(top: 40),
                child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
              )
            else if (_seg == 0)
              ..._announcements(inbox)
            else
              ..._replies(inbox),
          ],
        ),
      ),
    );
  }

  List<Widget> _announcements(InboxStore inbox) {
    if (inbox.announcements.isEmpty) {
      return <Widget>[
        const AppCard(
          margin: EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
          padding: EdgeInsets.zero,
          child: AppEmptyState(text: '暂无公告', icon: Icons.campaign_outlined),
        ),
      ];
    }
    return <Widget>[
      for (final Announcement item in inbox.announcements)
        _msgCard(
          localKey: item.localKey,
          icon: item.directed ? Icons.error_outline : Icons.campaign_outlined,
          tone: item.directed ? AppTagTone.warn : AppTagTone.brand,
          title: item.title,
          time: formatShort(item.publishedAt),
          desc: item.content.isEmpty ? '（无正文）' : item.content,
          unread: !inbox.isRead(item.localKey),
          onTap: () => _openAnnouncement(inbox, item),
        ),
    ];
  }

  List<Widget> _replies(InboxStore inbox) {
    if (inbox.feedbackReplies.isEmpty) {
      return <Widget>[
        const AppCard(
          margin: EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
          padding: EdgeInsets.zero,
          child: AppEmptyState(
            text: '暂无反馈回复',
            icon: Icons.chat_bubble_outline,
          ),
        ),
      ];
    }
    return <Widget>[
      for (final FeedbackReply item in inbox.feedbackReplies)
        _msgCard(
          localKey: item.localKey,
          icon: Icons.chat_bubble_outline,
          tone: AppTagTone.brand,
          title: item.title,
          time: formatShort(item.createdAt),
          desc: item.content,
          unread: !inbox.isRead(item.localKey),
          onTap: () => _openReply(inbox, item),
        ),
    ];
  }

  Future<void> _openAnnouncement(InboxStore inbox, Announcement item) async {
    await inbox.markRead(item.localKey);
    if (!mounted) return;
    await pushPage<void>(context, AnnouncePage(announcement: item));
  }

  Future<void> _openReply(InboxStore inbox, FeedbackReply item) async {
    await inbox.markRead(item.localKey);
    if (!mounted) return;
    await pushPage<void>(context, MessageDetailPage(reply: item));
  }

  Widget _error(InboxStore inbox) {
    return AppCard(
      margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
      padding: EdgeInsets.zero,
      onTap: inbox.refresh,
      child: AppEmptyState(text: inbox.error ?? '加载失败，点击重试', icon: Icons.wifi_off),
    );
  }

  Widget _msgCard({
    required String localKey,
    required IconData icon,
    required AppTagTone tone,
    required String title,
    required String time,
    required String desc,
    required bool unread,
    required VoidCallback onTap,
  }) {
    final (Color bg, Color fg) = switch (tone) {
      AppTagTone.brand => (AppTokens.brandWeak, AppTokens.brand),
      AppTagTone.warn => (AppTokens.warnWeak, AppTokens.warn),
      AppTagTone.ok => (AppTokens.okWeak, AppTokens.ok),
      _ => (AppTokens.line, AppTokens.t3),
    };

    return AppCard(
      margin: const EdgeInsets.fromLTRB(
        AppTokens.pagePadding,
        0,
        AppTokens.pagePadding,
        AppTokens.gap10,
      ),
      onTap: onTap,
      child: Stack(
        clipBehavior: Clip.none,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Container(
                width: 38,
                height: 38,
                decoration: BoxDecoration(
                  color: bg,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Icon(icon, size: 19, color: fg),
              ),
              const SizedBox(width: AppTokens.gap12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.baseline,
                      textBaseline: TextBaseline.alphabetic,
                      children: <Widget>[
                        Expanded(
                          child: Text(
                            title,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 14,
                              fontWeight: FontWeight.w600,
                              color: AppTokens.t1,
                            ),
                          ),
                        ),
                        const SizedBox(width: AppTokens.gap8),
                        Text(
                          time,
                          style: const TextStyle(
                            fontSize: 11.5,
                            color: AppTokens.t3,
                            fontFeatures: AppTokens.numFeatures,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      desc,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 12.5,
                        color: AppTokens.t3,
                        height: 1.55,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          if (unread)
            Positioned(
              left: -7,
              top: 0,
              bottom: 0,
              child: Center(
                child: Container(
                  width: 6,
                  height: 6,
                  decoration: const BoxDecoration(
                    color: AppTokens.danger,
                    shape: BoxShape.circle,
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}
