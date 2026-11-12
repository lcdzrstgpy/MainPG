import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../core/models/message_models.dart';
import '../../../core/util/time_format.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';
import '../state/inbox_store.dart';

/// 反馈回复详情：官方回复 + 继续回复（真实提交至 /api/customer/feedback）
class MessageDetailPage extends StatefulWidget {
  const MessageDetailPage({super.key, required this.reply});

  final FeedbackReply reply;

  @override
  State<MessageDetailPage> createState() => _MessageDetailPageState();
}

class _MessageDetailPageState extends State<MessageDetailPage> {
  final TextEditingController _reply = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _reply.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final String text = _reply.text.trim();
    if (text.isEmpty) {
      showAppToast(context, '请输入回复内容');
      return;
    }
    setState(() => _busy = true);
    final String? error =
        await context.read<InboxStore>().submitFeedback(content: text);
    if (!mounted) return;
    setState(() => _busy = false);
    if (error == null) {
      _reply.clear();
      FocusScope.of(context).unfocus();
      showAppToast(context, '已提交，客服会尽快答复');
    } else {
      showAppToast(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final FeedbackReply item = widget.reply;
    return AppSubPage(
      title: '反馈回复',
      child: ListView(
        padding: const EdgeInsets.only(bottom: 24),
        children: <Widget>[
          Container(
            margin: const EdgeInsets.fromLTRB(
              AppTokens.pagePadding,
              AppTokens.gap4,
              AppTokens.pagePadding,
              0,
            ),
            padding: const EdgeInsets.all(AppTokens.gap16),
            decoration: const BoxDecoration(
              color: AppTokens.surface,
              borderRadius: AppTokens.brLg,
              boxShadow: AppTokens.shadow1,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  item.title,
                  style: const TextStyle(
                    fontSize: 17,
                    fontWeight: FontWeight.w700,
                    height: 1.4,
                  ),
                ),
                const SizedBox(height: AppTokens.gap8),
                Wrap(
                  spacing: 9,
                  runSpacing: 6,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: <Widget>[
                    const AppTag('官方回复', tone: AppTagTone.brand),
                    Text(
                      formatDateTime(item.createdAt),
                      style: const TextStyle(
                        fontSize: 12,
                        color: AppTokens.t3,
                        fontFeatures: AppTokens.numFeatures,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppTokens.pagePadding,
              AppTokens.gap14,
              AppTokens.pagePadding,
              0,
            ),
            child: _bubble(
              'M',
              formatDateTime(item.createdAt),
              item.content.isEmpty ? '（无内容）' : item.content,
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppTokens.pagePadding,
              AppTokens.gap16,
              AppTokens.pagePadding,
              0,
            ),
            child: AppInput(
              controller: _reply,
              hint: '继续回复…',
              suffix: AppNavIconButton(
                icon: Icons.arrow_upward,
                color: _busy ? AppTokens.t4 : AppTokens.brand,
                onTap: _busy ? null : _send,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _bubble(String who, String time, String text, {bool mine = false}) {
    return AppCard(
      padding: const EdgeInsets.all(AppTokens.gap14),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          AppAvatar(
            text: who,
            size: 32,
            radius: 10,
            fontSize: 13,
            gradient: !mine,
          ),
          const SizedBox(width: AppTokens.gap10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  time,
                  style: const TextStyle(
                    fontSize: 12,
                    color: AppTokens.t3,
                    fontFeatures: AppTokens.numFeatures,
                  ),
                ),
                const SizedBox(height: AppTokens.gap4),
                Text(
                  text,
                  style: const TextStyle(
                    fontSize: 13.5,
                    color: AppTokens.t1,
                    height: 1.65,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
