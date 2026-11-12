import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';
import '../state/wallet_store.dart';
import 'ledger_page.dart';
import 'signin_page.dart';

/// 积分中心：积分总览 · 每日任务 · 签到记录 / 积分明细入口
class PointsPage extends StatelessWidget {
  const PointsPage({super.key});

  @override
  Widget build(BuildContext context) {
    final WalletStore wallet = context.watch<WalletStore>();

    return AppSubPage(
      title: '积分中心',
      child: ListView(
        padding: const EdgeInsets.only(bottom: 24),
        children: <Widget>[
          Container(
            margin: const EdgeInsets.fromLTRB(
              AppTokens.pagePadding,
              AppTokens.gap8,
              AppTokens.pagePadding,
              0,
            ),
            padding: const EdgeInsets.all(AppTokens.gap18),
            decoration: const BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: <Color>[Color(0xFF3D7BFF), Color(0xFF2456E0)],
              ),
              borderRadius: AppTokens.brXl,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const Text(
                  '可用积分',
                  style: TextStyle(fontSize: 12.5, color: Color(0xD1FFFFFF)),
                ),
                Text(
                  formatThousands(wallet.points),
                  style: const TextStyle(
                    fontSize: 34,
                    fontWeight: FontWeight.w700,
                    letterSpacing: -0.5,
                    color: Colors.white,
                    fontFeatures: AppTokens.numFeatures,
                  ),
                ),
                const SizedBox(height: AppTokens.gap14),
                Row(
                  children: <Widget>[
                    _stat('套餐', wallet.planLabel),
                    const SizedBox(width: AppTokens.gap16),
                    _stat('有效期至', wallet.planExpireAt.isEmpty ? '—' : wallet.planExpireAt),
                  ],
                ),
              ],
            ),
          ),
          const AppSectionTitle(text: '每日任务'),
          AppListCard(
            margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
            children: <Widget>[
              AppRow(
                icon: Icons.star_outline,
                title: '每日签到',
                subtitle: '本周已签到 ${wallet.dailyWeekCount}/${wallet.weeklyMax} 天',
                trailing: wallet.dailyClaimable
                    ? AppTag('＋${wallet.dailyClaimPoints}',
                        tone: AppTagTone.brand)
                    : const AppTag('已领取', tone: AppTagTone.ok),
                onTap: wallet.dailyClaimable
                    ? () => _claimDaily(context)
                    : null,
              ),
              AppRow(
                icon: Icons.card_giftcard_outlined,
                title: '基础版每周领取',
                subtitle: '每周 1 次 · 与签到独立',
                trailing: wallet.basicClaimable
                    ? const AppTag('可领取', tone: AppTagTone.brand)
                    : const AppTag('已领取', tone: AppTagTone.ok),
                onTap: wallet.basicClaimable
                    ? () => _claimBasic(context)
                    : null,
              ),
              const AppRow(
                icon: Icons.campaign_outlined,
                title: '观看广告视频',
                subtitle: '暂未开放',
                trailing: AppTag('未开放'),
              ),
            ],
          ),
          const AppSectionTitle(text: '其他'),
          AppListCard(
            margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
            children: <Widget>[
              AppRow(
                icon: Icons.schedule,
                title: '签到记录',
                showChevron: true,
                onTap: () => pushPage<void>(context, const SigninPage()),
              ),
              AppRow(
                icon: Icons.description_outlined,
                title: '积分明细',
                showChevron: true,
                onTap: () => pushPage<void>(context, const LedgerPage()),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _claimDaily(BuildContext context) async {
    final ClaimOutcome outcome = await context.read<WalletStore>().claimDaily();
    if (!context.mounted) return;
    showAppToast(context, outcome.message);
  }

  Future<void> _claimBasic(BuildContext context) async {
    final ClaimOutcome outcome = await context.read<WalletStore>().claimBasic();
    if (!context.mounted) return;
    showAppToast(context, outcome.message);
  }

  Widget _stat(String label, String value) {
    return Flexible(
      child: Text.rich(
        TextSpan(
          style: const TextStyle(fontSize: 12, color: Color(0xD9FFFFFF)),
          children: <InlineSpan>[
            TextSpan(text: '$label '),
            TextSpan(
              text: value,
              style: const TextStyle(
                fontWeight: FontWeight.w700,
                color: Colors.white,
                fontFeatures: AppTokens.numFeatures,
              ),
            ),
          ],
        ),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
    );
  }
}
