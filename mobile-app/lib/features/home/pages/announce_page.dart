import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../core/models/message_models.dart';
import '../../../core/util/time_format.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../inbox/state/inbox_store.dart';

/// 公告详情：标题 / 元信息 + 正文 + 图片（base64 本体）
class AnnouncePage extends StatefulWidget {
  const AnnouncePage({super.key, this.announcement});

  /// 为空时展示占位（从消息列表进入但无数据）
  final Announcement? announcement;

  @override
  State<AnnouncePage> createState() => _AnnouncePageState();
}

class _AnnouncePageState extends State<AnnouncePage> {
  List<AnnouncementImage> _images = <AnnouncementImage>[];
  bool _loadingImages = false;
  String? _imageError;

  @override
  void initState() {
    super.initState();
    final Announcement? item = widget.announcement;
    if (item == null) return;
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      await context.read<InboxStore>().markRead(item.localKey);
      if (item.imageCount > 0) await _loadImages(item.id);
    });
  }

  Future<void> _loadImages(int id) async {
    setState(() {
      _loadingImages = true;
      _imageError = null;
    });
    try {
      final List<AnnouncementImage> images =
          await context.read<InboxStore>().loadImages(id);
      if (!mounted) return;
      setState(() {
        _images = images;
        _loadingImages = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _imageError = '图片加载失败';
        _loadingImages = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final Announcement? item = widget.announcement;
    if (item == null) {
      return const AppSubPage(
        title: '公告详情',
        child: Center(
          child: AppEmptyState(text: '公告不存在或已下线', icon: Icons.campaign_outlined),
        ),
      );
    }

    return AppSubPage(
      title: '公告详情',
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
                    AppTag(
                      item.directed ? '定向公告' : '全员公告',
                      tone: item.directed ? AppTagTone.warn : AppTagTone.brand,
                    ),
                    Text(
                      formatDateTime(item.publishedAt),
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
          Container(
            margin: const EdgeInsets.fromLTRB(
              AppTokens.pagePadding,
              AppTokens.gap12,
              AppTokens.pagePadding,
              0,
            ),
            padding: const EdgeInsets.all(AppTokens.gap16),
            decoration: const BoxDecoration(
              color: AppTokens.surface,
              borderRadius: AppTokens.brLg,
              boxShadow: AppTokens.shadow1,
            ),
            child: Text(
              item.content.isEmpty ? '（无正文）' : item.content,
              style: const TextStyle(fontSize: 14, height: 1.85, color: AppTokens.t2),
            ),
          ),
          if (item.imageCount > 0) _imageSection(item),
          const AppEnvNote(
            child: Text(
              '公告由云端 publish-api 下发；带账号定向的公告仅推送给指定账号。',
            ),
          ),
        ],
      ),
    );
  }

  Widget _imageSection(Announcement item) {
    if (_loadingImages) {
      return const Padding(
        padding: EdgeInsets.only(top: AppTokens.gap16),
        child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
      );
    }
    if (_imageError != null) {
      return Padding(
        padding: const EdgeInsets.only(top: AppTokens.gap16),
        child: Center(
          child: Text(
            _imageError!,
            style: const TextStyle(fontSize: 12.5, color: AppTokens.t3),
          ),
        ),
      );
    }
    if (_images.isEmpty) return const SizedBox.shrink();

    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppTokens.pagePadding,
        AppTokens.gap12,
        AppTokens.pagePadding,
        0,
      ),
      child: Column(
        children: <Widget>[
          for (final AnnouncementImage image in _images)
            Padding(
              padding: const EdgeInsets.only(bottom: AppTokens.gap10),
              child: ClipRRect(
                borderRadius: AppTokens.brMd,
                child: _decode(image.data),
              ),
            ),
        ],
      ),
    );
  }

  Widget _decode(String base64Data) {
    try {
      final Uint8List bytes = base64Decode(base64Data);
      return Image.memory(bytes, fit: BoxFit.fitWidth);
    } catch (_) {
      return const SizedBox.shrink();
    }
  }
}
