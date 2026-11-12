import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../core/models/billing_models.dart';
import '../../../core/util/time_format.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../state/wallet_store.dart';

/// 积分明细：全部 / 充值积分 / 活动积分 分段 + 按月分组 + 分页加载
///
/// 说明：云端 `billing/ledger` 只返回入账（credit）条目，因此这里没有「支出」段。
class LedgerPage extends StatefulWidget {
  const LedgerPage({super.key});

  @override
  State<LedgerPage> createState() => _LedgerPageState();
}

class _LedgerPageState extends State<LedgerPage> {
  static const List<String> _categories = <String>['', 'topup', 'reward'];

  final ScrollController _scroll = ScrollController();
  int _seg = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      context.read<WalletStore>().loadLedger(category: '');
    });
    _scroll.addListener(_onScroll);
  }

  @override
  void dispose() {
    _scroll.removeListener(_onScroll);
    _scroll.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_scroll.hasClients) return;
    final double remaining =
        _scroll.position.maxScrollExtent - _scroll.position.pixels;
    if (remaining < 240) {
      context.read<WalletStore>().loadLedger(loadMore: true);
    }
  }

  Future<void> _refresh() async {
    final WalletStore wallet = context.read<WalletStore>();
    await Future.wait<void>(<Future<void>>[wallet.load(), wallet.loadLedger()]);
  }

  @override
  Widget build(BuildContext context) {
    final WalletStore wallet = context.watch<WalletStore>();
    final List<LedgerEntry> entries = wallet.ledger;
    final List<String> months = <String>[];
    for (final LedgerEntry entry in entries) {
      final String month = formatMonth(entry.createdAt);
      if (!months.contains(month)) months.add(month);
    }

    return AppSubPage(
      title: '积分明细',
      child: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          controller: _scroll,
          padding: const EdgeInsets.only(bottom: 24),
          physics: const AlwaysScrollableScrollPhysics(),
          children: <Widget>[
            AppSegment(
              items: const <String>['全部', '充值积分', '活动积分'],
              index: _seg,
              onChanged: (int index) {
                setState(() => _seg = index);
                context
                    .read<WalletStore>()
                    .loadLedger(category: _categories[index]);
              },
            ),
            if (wallet.ledgerError != null && entries.isEmpty)
              AppCard(
                margin: const EdgeInsets.symmetric(
                  horizontal: AppTokens.pagePadding,
                ),
                padding: EdgeInsets.zero,
                onTap: () => wallet.loadLedger(category: _categories[_seg]),
                child: AppEmptyState(
                  text: wallet.ledgerError ?? '加载失败，点击重试',
                  icon: Icons.wifi_off,
                ),
              )
            else if (wallet.ledgerLoading && entries.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 40),
                child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
              )
            else if (entries.isEmpty)
              const AppCard(
                margin: EdgeInsets.symmetric(
                  horizontal: AppTokens.pagePadding,
                ),
                padding: EdgeInsets.zero,
                child: AppEmptyState(
                  text: '暂无积分明细',
                  icon: Icons.description_outlined,
                ),
              )
            else
              for (final String month in months) ...<Widget>[
                AppSectionTitle(text: month),
                AppListCard(
                  margin: const EdgeInsets.symmetric(
                    horizontal: AppTokens.pagePadding,
                  ),
                  children: <Widget>[
                    for (final LedgerEntry entry in entries)
                      if (formatMonth(entry.createdAt) == month) _ledger(entry),
                  ],
                ),
              ],
            if (wallet.ledgerLoading && entries.isNotEmpty)
              const Padding(
                padding: EdgeInsets.only(top: AppTokens.gap12),
                child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
              )
            else if (!wallet.ledgerHasMore && entries.isNotEmpty)
              const Padding(
                padding: EdgeInsets.only(top: AppTokens.gap12),
                child: Center(
                  child: Text(
                    '没有更多了',
                    style: TextStyle(fontSize: 12, color: AppTokens.t3),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _ledger(LedgerEntry entry) {
    final String subtitle = entry.sourceId.isEmpty
        ? formatShort(entry.createdAt)
        : '${formatShort(entry.createdAt)} · ${entry.sourceId}';
    return AppRow(
      title: _sourceLabel(entry.sourceType),
      subtitle: subtitle,
      trailing: Text(
        '＋${formatThousands(entry.pointsDelta.abs())}',
        style: const TextStyle(
          fontSize: 16,
          fontWeight: FontWeight.w600,
          color: AppTokens.ok,
          fontFeatures: AppTokens.numFeatures,
        ),
      ),
    );
  }

  static String _sourceLabel(String type) {
    switch (type) {
      case 'payment_alipay':
        return '在线充值（支付宝）';
      case 'payment_wechat':
        return '在线充值（微信）';
      case 'topup_promotion_bonus':
        return '充值赠送';
      case 'daily_extra_claim':
        return '每日签到';
      case 'plan_basic_claim':
        return '基础版每周领取';
      default:
        return '积分入账';
    }
  }
}
