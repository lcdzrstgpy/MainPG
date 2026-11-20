import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../core/models/billing_models.dart';
import '../../../core/util/time_format.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../state/wallet_store.dart';

/// 签到记录：本周统计 + 签到日历（来自真实入账明细）+ 最近记录
class SigninPage extends StatefulWidget {
  const SigninPage({super.key});

  @override
  State<SigninPage> createState() => _SigninPageState();
}

class _SigninPageState extends State<SigninPage> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      context.read<WalletStore>().loadRewardEntries();
    });
  }

  @override
  Widget build(BuildContext context) {
    final WalletStore wallet = context.watch<WalletStore>();
    final List<LedgerEntry> entries = wallet.signEntries;
    final String month = entries.isEmpty ? '' : formatMonth(entries.first.createdAt);
    final Set<int> checked = entries
        .where((LedgerEntry e) => formatMonth(e.createdAt) == month)
        .map((LedgerEntry e) => dayOfMonth(e.createdAt))
        .where((int day) => day > 0)
        .toSet();
    final int daysInMonth = _daysInMonth(month);

    return AppSubPage(
      title: '签到记录',
      child: ListView(
        padding: const EdgeInsets.only(top: 10, bottom: 24),
        children: <Widget>[
          Container(
            margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
            padding: const EdgeInsets.all(AppTokens.gap16),
            decoration: const BoxDecoration(
              color: AppTokens.surface,
              borderRadius: AppTokens.brLg,
              boxShadow: AppTokens.shadow1,
            ),
            child: Column(
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Expanded(
                      child: _stat(
                        '本周已签到',
                        '${wallet.dailyWeekCount}',
                        '/${wallet.weeklyMax} 天',
                      ),
                    ),
                    Expanded(
                      child: _stat('本月累计', '${checked.length}', '次', right: true),
                    ),
                  ],
                ),
                if (daysInMonth > 0) ...<Widget>[
                  const SizedBox(height: AppTokens.gap16),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      '$month 签到日历',
                      style: const TextStyle(fontSize: 12.5, color: AppTokens.t3),
                    ),
                  ),
                  const SizedBox(height: AppTokens.gap10),
                  LayoutBuilder(
                    builder: (BuildContext context, BoxConstraints c) {
                      const double gap = 6;
                      const int cols = 7;
                      final double cell =
                          (c.maxWidth - gap * (cols - 1)) / cols;
                      return Wrap(
                        spacing: gap,
                        runSpacing: gap,
                        children: <Widget>[
                          for (int day = 1; day <= daysInMonth; day++)
                            _day(cell, day, checked: checked.contains(day)),
                        ],
                      );
                    },
                  ),
                ],
              ],
            ),
          ),
          const AppSectionTitle(text: '最近记录'),
          if (wallet.rewardLoading && entries.isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 20),
              child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
            )
          else if (entries.isEmpty)
            const AppCard(
              margin: EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
              padding: EdgeInsets.zero,
              child: AppEmptyState(text: '暂无签到记录', icon: Icons.schedule),
            )
          else
            AppListCard(
              margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
              children: <Widget>[
                for (final LedgerEntry entry in entries.take(30))
                  AppRow(
                    title: '每日签到',
                    subtitle: formatDateTime(entry.createdAt),
                    trailing: Text(
                      '＋${formatThousands(entry.pointsDelta.abs())}',
                      style: const TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w600,
                        color: AppTokens.ok,
                        fontFeatures: AppTokens.numFeatures,
                      ),
                    ),
                  ),
              ],
            ),
        ],
      ),
    );
  }

  /// `2026-10` → 31（解析失败返回 0）
  static int _daysInMonth(String month) {
    if (month.length < 7) return 0;
    final int? year = int.tryParse(month.substring(0, 4));
    final int? m = int.tryParse(month.substring(5, 7));
    if (year == null || m == null || m < 1 || m > 12) return 0;
    return DateTime(year, m + 1, 0).day;
  }

  Widget _stat(String label, String value, String unit, {bool right = false}) {
    return Column(
      crossAxisAlignment:
          right ? CrossAxisAlignment.end : CrossAxisAlignment.start,
      children: <Widget>[
        Text(label, style: const TextStyle(fontSize: 12.5, color: AppTokens.t3)),
        Padding(
          padding: const EdgeInsets.only(top: 2),
          child: Text.rich(
            TextSpan(
              style: const TextStyle(
                fontSize: 26,
                fontWeight: FontWeight.w700,
                color: AppTokens.t1,
                fontFeatures: AppTokens.numFeatures,
              ),
              children: <InlineSpan>[
                TextSpan(text: value),
                TextSpan(
                  text: ' $unit',
                  style: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w500,
                    color: AppTokens.t3,
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _day(double size, int day, {required bool checked}) {
    final Color bg = checked ? AppTokens.brandWeak : const Color(0xFFF2F4F8);
    final Color fg = checked ? AppTokens.brand : AppTokens.t3;
    return Container(
      width: size,
      height: 36,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(9),
      ),
      child: Text(
        '$day',
        style: TextStyle(
          fontSize: 12.5,
          color: fg,
          fontWeight: checked ? FontWeight.w700 : FontWeight.w400,
          fontFeatures: AppTokens.numFeatures,
        ),
      ),
    );
  }
}
