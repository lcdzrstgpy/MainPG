import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../../core/util/time_format.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';
import '../../device/state/device_store.dart';
import '../state/capture_store.dart';
import '../widgets/capture_status_tag.dart';

/// 采集详情：状态 / 站点 + 元信息 + 原始链接
class CollectDetailPage extends StatelessWidget {
  const CollectDetailPage({super.key, this.recordId});

  final String? recordId;

  @override
  Widget build(BuildContext context) {
    final CaptureStore store = context.watch<CaptureStore>();
    final CaptureRecord? record = store.byId(recordId) ??
        (store.records.isEmpty ? null : store.records.first);
    final DeviceStore device = context.watch<DeviceStore>();

    if (record == null) {
      return const AppSubPage(
        title: '采集详情',
        child: Center(
          child: AppEmptyState(
            text: '记录不存在或已删除',
            icon: Icons.image_outlined,
          ),
        ),
      );
    }

    return AppSubPage(
      title: '采集详情',
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
                  record.title,
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
                    CaptureStatusTag(status: record.status),
                    AppTag(record.site),
                  ],
                ),
              ],
            ),
          ),
          AppKvList(
            items: <AppKv>[
              AppKv(k: '站点', v: record.site),
              AppKv(k: '登记时间', v: formatDateTime(record.createdAt)),
              AppKv(
                k: '同步目标',
                v: '${device.current?.name ?? '未登记 PC'}'
                    '（${device.lanAvailable ? '局域网通道' : '云端通道'}）',
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppTokens.pagePadding,
              AppTokens.gap14,
              AppTokens.pagePadding,
              0,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const Text(
                  '原始链接',
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w500,
                    color: AppTokens.t2,
                  ),
                ),
                const SizedBox(height: 7),
                Container(
                  height: 48,
                  padding: const EdgeInsets.only(left: 14, right: 4),
                  decoration: const BoxDecoration(
                    color: Color(0xFFF7F8FA),
                    borderRadius: AppTokens.brMd,
                  ),
                  child: Row(
                    children: <Widget>[
                      Expanded(
                        child: Text(
                          record.link,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 13,
                            color: AppTokens.t1,
                            fontFamily: 'monospace',
                          ),
                        ),
                      ),
                      AppNavIconButton(
                        icon: Icons.copy_outlined,
                        color: AppTokens.t3,
                        size: 18,
                        onTap: () => _copy(context, record.link),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: AppTokens.gap12),
                Row(
                  children: <Widget>[
                    Expanded(
                      child: AppButton(
                        label: '复制链接',
                        tone: AppButtonTone.line,
                        onPressed: () => _copy(context, record.link),
                      ),
                    ),
                    const SizedBox(width: AppTokens.gap10),
                    Expanded(
                      child: AppButton(
                        label: '删除记录',
                        tone: AppButtonTone.ghost,
                        onPressed: () => _remove(context, store, record),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const AppHint(
            text: '该记录已同步到云端账号，需在 PC 端采集页「手机端导入」中导入后才会进入草稿池。',
            margin: EdgeInsets.fromLTRB(
              AppTokens.pagePadding + 2,
              AppTokens.gap10,
              AppTokens.pagePadding + 2,
              0,
            ),
          ),
        ],
      ),
    );
  }

  static Future<void> _copy(BuildContext context, String link) async {
    await Clipboard.setData(ClipboardData(text: link));
    if (!context.mounted) return;
    showAppToast(context, '链接已复制');
  }

  static Future<void> _remove(
    BuildContext context,
    CaptureStore store,
    CaptureRecord record,
  ) async {
    await store.remove(record.id);
    if (!context.mounted) return;
    Navigator.of(context).maybePop();
  }
}
