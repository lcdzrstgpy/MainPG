import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../core/models/billing_models.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../points/pages/ledger_page.dart';
import '../../points/state/wallet_store.dart';
import 'topup_order_page.dart';

/// 在线充值：当前积分 + 云端套餐选择 + 底部「去支付」
class TopupPage extends StatefulWidget {
  const TopupPage({super.key});

  @override
  State<TopupPage> createState() => _TopupPageState();
}

class _TopupPageState extends State<TopupPage> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    final WalletStore wallet = context.watch<WalletStore>();
    final List<BillingPackage> packages = wallet.packages;
    if (_index >= packages.length) _index = 0;
    final BillingPackage? selected =
        packages.isEmpty ? null : packages[_index];

    return AppSubPage(
      title: '在线充值',
      actions: <Widget>[
        AppNavIconButton(
          icon: Icons.description_outlined,
          onTap: () => pushPage<void>(context, const LedgerPage()),
        ),
      ],
      bottomBar: Container(
        color: AppTokens.bg,
        padding: const EdgeInsets.fromLTRB(
          AppTokens.pagePadding,
          AppTokens.gap12,
          AppTokens.pagePadding,
          AppTokens.gap18,
        ),
        child: SafeArea(
          top: false,
          child: AppButton(
            label: '去支付',
            block: true,
            onPressed: selected == null
                ? null
                : () => pushPage<void>(
                      context,
                      TopupOrderPage(package: selected),
                    ),
          ),
        ),
      ),
      child: ListView(
        padding: const EdgeInsets.only(top: 8, bottom: 16),
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
            child: AppCard(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
              child: Row(
                children: <Widget>[
                  Container(
                    width: 40,
                    height: 40,
                    decoration: BoxDecoration(
                      color: AppTokens.brandWeak,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: const Icon(
                      Icons.star_outline,
                      size: 20,
                      color: AppTokens.brand,
                    ),
                  ),
                  const SizedBox(width: AppTokens.gap12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        const Text(
                          '当前积分',
                          style: TextStyle(fontSize: 12.5, color: AppTokens.t3),
                        ),
                        const SizedBox(height: 3),
                        Text(
                          formatThousands(wallet.points),
                          style: const TextStyle(
                            fontSize: 20,
                            fontWeight: FontWeight.w700,
                            fontFeatures: AppTokens.numFeatures,
                          ),
                        ),
                      ],
                    ),
                  ),
                  AppTag(wallet.planLabel, tone: AppTagTone.brand),
                ],
              ),
            ),
          ),
          const AppSectionTitle(
            text: '选择套餐',
            margin: EdgeInsets.fromLTRB(
              AppTokens.gap16,
              AppTokens.gap18,
              AppTokens.gap16,
              AppTokens.gap8,
            ),
          ),
          if (wallet.loading && packages.isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 24),
              child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
            )
          else if (packages.isEmpty)
            AppCard(
              margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
              padding: EdgeInsets.zero,
              child: AppEmptyState(
                text: wallet.error ?? '暂无可用套餐，请稍后重试',
                icon: Icons.account_balance_wallet_outlined,
              ),
            )
          else
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
              child: Column(
                children: <Widget>[
                  for (int i = 0; i < packages.length; i++) ...<Widget>[
                    if (i > 0) const SizedBox(height: AppTokens.gap10),
                    _planTile(packages[i], i),
                  ],
                ],
              ),
            ),
          const AppEnvNote(
            child: Text(
              '充值走支付宝。支付结果以云端回调为准，到账后再刷新积分；订单未支付 30 分钟后自动关闭。',
            ),
          ),
        ],
      ),
    );
  }

  Widget _planTile(BillingPackage plan, int index) {
    final bool on = _index == index;
    final String bonus = plan.promotionBonusPoints > 0
        ? '含赠送 ${formatThousands(plan.promotionBonusPoints)}'
        : '约 0.01 元 / 积分';

    return AppCard(
      onTap: () => setState(() => _index = index),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Text(
                      plan.label.isEmpty
                          ? '${plan.priceYuan} 元 / ${formatThousands(plan.displayPoints)} 积分'
                          : plan.label,
                      style: const TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    if (plan.promotionBonusPercent > 0) ...<Widget>[
                      const SizedBox(width: 6),
                      AppTag('赠 ${plan.promotionBonusPercent}%',
                          tone: AppTagTone.warn),
                    ],
                  ],
                ),
                const SizedBox(height: 5),
                Text(
                  '到账 ${formatThousands(plan.displayPoints)} 积分 · $bonus',
                  style: const TextStyle(fontSize: 12, color: AppTokens.t3),
                ),
              ],
            ),
          ),
          const SizedBox(width: AppTokens.gap10),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: <Widget>[
              Text(
                plan.priceLabel,
                style: TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.w700,
                  color: on ? AppTokens.brand : AppTokens.t1,
                  fontFeatures: AppTokens.numFeatures,
                ),
              ),
              const SizedBox(height: 3),
              Icon(
                on ? Icons.check_circle : Icons.circle_outlined,
                size: 18,
                color: on ? AppTokens.brand : AppTokens.t4,
              ),
            ],
          ),
        ],
      ),
    );
  }
}
