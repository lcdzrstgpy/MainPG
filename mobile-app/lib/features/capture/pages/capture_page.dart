import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';
import '../../device/state/device_store.dart';
import '../state/capture_store.dart';
import '../widgets/capture_status_tag.dart';
import 'collect_detail_page.dart';

/// 采集：粘贴链接 · 分享采集引导 · 采集记录（含失败重试）
class CapturePage extends StatefulWidget {
  const CapturePage({super.key});

  @override
  State<CapturePage> createState() => _CapturePageState();
}

class _CapturePageState extends State<CapturePage> {
  static const List<String> _sites = <String>['淘宝', '1688', '天猫', 'Temu'];

  String? _link;
  String _site = '淘宝';
  bool _submitting = false;

  Future<void> _fillFromClipboard() async {
    final ClipboardData? data = await Clipboard.getData(Clipboard.kTextPlain);
    final String text = extractCaptureLink(data?.text ?? '');
    if (!mounted) return;
    if (text.isEmpty) {
      showAppToast(context, '剪贴板没有可用链接');
      return;
    }
    final CaptureStore store = context.read<CaptureStore>();
    setState(() {
      _link = text;
      _site = store.siteOf(text);
    });
    showAppToast(context, '已识别站点：$_site');
  }

  Future<void> _submit() async {
    final String? link = _link;
    if (link == null || link.isEmpty) {
      showAppToast(context, '请先粘贴商品链接');
      return;
    }
    final CaptureStore store = context.read<CaptureStore>();
    final String deviceName = context.read<DeviceStore>().current?.name ?? '';
    setState(() => _submitting = true);
    final String? error = await store.submit(
      link: link,
      site: store.siteOf(link),
      deviceName: deviceName,
    );
    if (!mounted) return;
    setState(() {
      _submitting = false;
      if (error == null) _link = null;
    });
    showAppToast(
      context,
      error ?? '已同步到云端，可在 PC 端「手机端导入」中导入',
    );
  }

  @override
  Widget build(BuildContext context) {
    final CaptureStore store = context.watch<CaptureStore>();
    final List<CaptureRecord> records = store.records;

    return AppScreen(
      navBar: AppNavBar(
        title: '采集',
        leading: const SizedBox(width: 30),
        actions: <Widget>[
          AppNavIconButton(
            icon: Icons.info_outline,
            onTap: () => pushPage<void>(
              context,
              CollectDetailPage(recordId: records.isEmpty ? null : records.first.id),
            ),
          ),
        ],
      ),
      child: ListView(
        padding: EdgeInsets.only(bottom: tabPageBottomInset(context) + AppTokens.gap16),
        children: <Widget>[
          _pasteCard(),
          _shareTip(),
          const AppSectionTitle(text: '采集记录'),
          if (records.isEmpty)
            const AppCard(
              margin: EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
              padding: EdgeInsets.zero,
              child: AppEmptyState(
                text: '暂无采集记录',
                icon: Icons.image_outlined,
              ),
            )
          else
            for (final CaptureRecord record in records) _recordCard(record),
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
            child: AppHint(
              text: '采集链接会同步到云端账号，在 PC 端采集页「手机端导入」中一键导入后进入草稿池，AI 处理与上架在 PC 完成。',
            ),
          ),
        ],
      ),
    );
  }

  Widget _pasteCard() {
    final bool filled = _link != null;
    return AppCard(
      margin: const EdgeInsets.fromLTRB(
        AppTokens.pagePadding,
        AppTokens.gap4,
        AppTokens.pagePadding,
        0,
      ),
      child: Column(
        children: <Widget>[
          Material(
            color: Colors.transparent,
            child: InkWell(
              onTap: _fillFromClipboard,
              borderRadius: AppTokens.brMd,
              child: Container(
                width: double.infinity,
                constraints: const BoxConstraints(minHeight: 84),
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 13),
                decoration: BoxDecoration(
                  color: filled ? AppTokens.brandWeak : const Color(0xFFFAFBFD),
                  borderRadius: AppTokens.brMd,
                  border: Border.all(
                    color: filled ? AppTokens.brand : AppTokens.line2,
                    width: 1.5,
                  ),
                ),
                child: filled
                    ? Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Text(
                            _link!,
                            style: const TextStyle(
                              fontSize: 13,
                              color: AppTokens.brand,
                              fontFamily: 'monospace',
                              height: 1.5,
                            ),
                          ),
                          const SizedBox(height: 6),
                          Wrap(
                            spacing: 6,
                            children: <Widget>[
                              for (final String s in _sites)
                                _siteChip(s, s == _site),
                            ],
                          ),
                        ],
                      )
                    : const Text(
                        '粘贴淘宝 / 1688 / 天猫 / Temu 商品链接…',
                        style: TextStyle(fontSize: 13.5, color: AppTokens.t3),
                      ),
              ),
            ),
          ),
          const SizedBox(height: AppTokens.gap12),
          Row(
            children: <Widget>[
              Expanded(
                child: AppButton(
                  label: '从剪贴板粘贴',
                  tone: AppButtonTone.line,
                  onPressed: _fillFromClipboard,
                ),
              ),
              const SizedBox(width: AppTokens.gap10),
              Expanded(
                child: AppButton(
                  label: '采集并同步',
                  loading: _submitting,
                  onPressed: _submit,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _siteChip(String label, bool on) {
    return Container(
      height: 22,
      padding: const EdgeInsets.symmetric(horizontal: 8),
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: on ? AppTokens.brand : AppTokens.line,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        label,
        style: TextStyle(fontSize: 11.5, color: on ? Colors.white : AppTokens.t3),
      ),
    );
  }

  Widget _shareTip() {
    return Container(
      margin: const EdgeInsets.fromLTRB(
        AppTokens.pagePadding,
        AppTokens.gap12,
        AppTokens.pagePadding,
        0,
      ),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: AppTokens.brandWeak,
        borderRadius: AppTokens.brLg,
        border: Border.all(color: AppTokens.brandWeak2),
      ),
      child: const Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(Icons.share_outlined, size: 19, color: AppTokens.brand),
          SizedBox(width: AppTokens.gap10),
          Expanded(
            child: Text.rich(
              TextSpan(
                style: TextStyle(
                  fontSize: 13,
                  color: Color(0xFF1C3F9E),
                  height: 1.55,
                ),
                children: <InlineSpan>[
                  TextSpan(text: '在淘宝 / 1688 App 内点「'),
                  TextSpan(
                    text: '分享',
                    style: TextStyle(
                      fontWeight: FontWeight.w600,
                      color: AppTokens.brand600,
                    ),
                  ),
                  TextSpan(text: '」再选「'),
                  TextSpan(
                    text: '复制链接',
                    style: TextStyle(
                      fontWeight: FontWeight.w600,
                      color: AppTokens.brand600,
                    ),
                  ),
                  TextSpan(text: '」，回到本页点「从剪贴板粘贴」即可识别。'),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _recordCard(CaptureRecord record) {
    final bool hasError = record.status == CaptureStatus.failed ||
        record.status == CaptureStatus.pendingSync;

    return AppCard(
      margin: const EdgeInsets.fromLTRB(
        AppTokens.pagePadding,
        0,
        AppTokens.pagePadding,
        AppTokens.gap10,
      ),
      padding: const EdgeInsets.fromLTRB(14, 13, 14, 13),
      onTap: () => pushPage<void>(context, CollectDetailPage(recordId: record.id)),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Container(
            width: 56,
            height: 56,
            decoration: const BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: <Color>[Color(0xFFEEF2FB), Color(0xFFE2E9F8)],
              ),
              borderRadius: AppTokens.brSm,
            ),
            child: const Icon(Icons.image_outlined, size: 22, color: AppTokens.t3),
          ),
          const SizedBox(width: AppTokens.gap12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  record.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 13.5,
                    color: AppTokens.t1,
                    height: 1.45,
                  ),
                ),
                const SizedBox(height: 6),
                Wrap(
                  spacing: 8,
                  runSpacing: 6,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: <Widget>[
                    CaptureStatusTag(status: record.status),
                    AppTag(record.site),
                    Text(
                      record.time,
                      style: const TextStyle(fontSize: 11.5, color: AppTokens.t3),
                    ),
                  ],
                ),
                if (hasError && record.message != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Text(
                      record.message!,
                      style: const TextStyle(
                        fontSize: 11.5,
                        color: AppTokens.danger,
                        height: 1.5,
                      ),
                    ),
                  ),
              ],
            ),
          ),
          if (hasError)
            Padding(
              padding: const EdgeInsets.only(left: AppTokens.gap8),
              child: AppNavIconButton(
                icon: Icons.delete_outline,
                size: 18,
                color: AppTokens.t3,
                onTap: () => _remove(record),
              ),
            ),
        ],
      ),
    );
  }

  Future<void> _remove(CaptureRecord record) async {
    await context.read<CaptureStore>().remove(record.id);
    if (!mounted) return;
    showAppToast(context, '已删除该条登记');
  }
}
