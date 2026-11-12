import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../../core/models/billing_models.dart';
import '../../../shared/theme/app_tokens.dart';
import '../../../shared/widgets/app_blocks.dart';
import '../../../shared/widgets/app_scaffold.dart';
import '../../../shared/widgets/app_toast.dart';
import '../../points/state/wallet_store.dart';

/// 确认下单：订单摘要 + 支付宝支付参数（真实下单）
class TopupOrderPage extends StatefulWidget {
  const TopupOrderPage({super.key, required this.package});

  final BillingPackage package;

  @override
  State<TopupOrderPage> createState() => _TopupOrderPageState();
}

class _TopupOrderPageState extends State<TopupOrderPage> {
  bool _busy = false;
  TopupPayment? _payment;
  BillingOrder? _order;
  bool _reused = false;

  Future<void> _confirm() async {
    if (_busy) return;
    setState(() => _busy = true);
    final OrderOutcome outcome = await context.read<WalletStore>().createTopupOrder(
          packageId: widget.package.packageId,
          amountCents: widget.package.amountCents,
        );
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (outcome.ok) {
        _payment = outcome.result!.payment;
        _order = outcome.result!.order;
        _reused = outcome.result!.reused;
      }
    });
    if (!outcome.ok) {
      showAppToast(context, outcome.error ?? '下单失败，请稍后重试');
      return;
    }
    showAppToast(
      context,
      _reused ? '已复用未支付订单' : '订单已创建，请完成支付',
    );
  }

  @override
  Widget build(BuildContext context) {
    final BillingPackage plan = widget.package;
    return AppSubPage(
      title: '确认下单',
      child: ListView(
        padding: const EdgeInsets.only(top: 10, bottom: 24),
        children: <Widget>[
          AppCard(
            margin: const EdgeInsets.symmetric(horizontal: AppTokens.pagePadding),
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            child: Column(
              children: <Widget>[
                AppKv(
                  k: '套餐',
                  v: plan.label.isEmpty
                      ? '${plan.priceYuan} 元 / ${formatThousands(plan.displayPoints)} 积分'
                      : plan.label,
                ),
                const Divider(height: 1, thickness: 1, color: AppTokens.line),
                AppKv(
                  k: '到账积分',
                  v: formatThousands(plan.displayPoints),
                  valueStyle: const TextStyle(
                    fontSize: 13.5,
                    color: AppTokens.t1,
                    fontFeatures: AppTokens.numFeatures,
                  ),
                ),
                const Divider(height: 1, thickness: 1, color: AppTokens.line),
                AppKv(
                  k: '支付金额',
                  v: plan.priceLabel,
                  valueStyle: const TextStyle(
                    fontSize: 19,
                    fontWeight: FontWeight.w700,
                    color: AppTokens.brand,
                    fontFeatures: AppTokens.numFeatures,
                  ),
                ),
                const Divider(height: 1, thickness: 1, color: AppTokens.line),
                const AppKv(k: '支付方式', v: '支付宝'),
              ],
            ),
          ),
          if (_order != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(
                AppTokens.pagePadding,
                AppTokens.gap12,
                AppTokens.pagePadding,
                0,
              ),
              child: AppHint(
                text: '订单号 ${_order!.orderId}'
                    '${_reused ? '（复用未支付订单）' : ''}',
              ),
            ),
          if (_payment != null) _paymentCard(_payment!),
          Padding(
            padding: const EdgeInsets.fromLTRB(
              AppTokens.pagePadding,
              AppTokens.gap20,
              AppTokens.pagePadding,
              0,
            ),
            child: AppButton(
              label: _payment == null ? '确认支付' : '重新获取支付参数',
              block: true,
              loading: _busy,
              onPressed: _confirm,
            ),
          ),
          const AppEnvNote(
            child: Text(
              '支付结果以云端回调为准。完成后回到「我的 → 积分中心」下拉刷新即可看到到账积分。',
            ),
          ),
        ],
      ),
    );
  }

  Widget _paymentCard(TopupPayment payment) {
    switch (payment.mode) {
      case PaymentMode.nativeQr:
        if (payment.qrCodeUrl.isEmpty) return _notice('云端未返回支付二维码，请稍后重试');
        return Padding(
          padding: const EdgeInsets.fromLTRB(
            AppTokens.pagePadding,
            AppTokens.gap14,
            AppTokens.pagePadding,
            0,
          ),
          child: AppCard(
            padding: const EdgeInsets.all(AppTokens.gap16),
            child: Column(
              children: <Widget>[
                const Text(
                  '支付宝扫码支付',
                  style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: AppTokens.gap12),
                ClipRRect(
                  borderRadius: AppTokens.brMd,
                  child: Image.network(
                    payment.qrCodeUrl,
                    width: 200,
                    height: 200,
                    fit: BoxFit.contain,
                    errorBuilder: (_, __, ___) => const SizedBox(
                      width: 200,
                      height: 200,
                      child: Center(
                        child: Text(
                          '二维码加载失败',
                          style: TextStyle(fontSize: 12.5, color: AppTokens.t3),
                        ),
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: AppTokens.gap12),
                const Text(
                  '请用支付宝扫码完成支付',
                  style: TextStyle(fontSize: 12, color: AppTokens.t3),
                ),
              ],
            ),
          ),
        );
      case PaymentMode.pagePay:
        if (payment.payUrl.isEmpty) return _notice('云端未返回收银台地址，请稍后重试');
        return Padding(
          padding: const EdgeInsets.fromLTRB(
            AppTokens.pagePadding,
            AppTokens.gap14,
            AppTokens.pagePadding,
            0,
          ),
          child: AppCard(
            padding: const EdgeInsets.all(AppTokens.gap16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const Text(
                  '网页收银台',
                  style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: AppTokens.gap10),
                SelectableText(
                  payment.payUrl,
                  style: const TextStyle(
                    fontSize: 12.5,
                    color: AppTokens.t2,
                    height: 1.5,
                  ),
                ),
                const SizedBox(height: AppTokens.gap12),
                AppButton(
                  label: '复制收银台地址',
                  tone: AppButtonTone.line,
                  block: true,
                  onPressed: () async {
                    await Clipboard.setData(ClipboardData(text: payment.payUrl));
                    if (!mounted) return;
                    showAppToast(context, '已复制，请在浏览器打开完成支付');
                  },
                ),
              ],
            ),
          ),
        );
      case PaymentMode.gatewayNotConfigured:
        return _notice(
          payment.message.isEmpty
              ? '云端支付宝网关未配置，暂不可支付，请联系客服'
              : payment.message,
        );
      case PaymentMode.unknown:
        return _notice('云端返回了未知的支付方式，请联系客服');
    }
  }

  Widget _notice(String text) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        AppTokens.pagePadding,
        AppTokens.gap14,
        AppTokens.pagePadding,
        0,
      ),
      child: AppCard(
        padding: const EdgeInsets.all(AppTokens.gap16),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            const Icon(Icons.info_outline, size: 18, color: AppTokens.warn),
            const SizedBox(width: AppTokens.gap10),
            Expanded(
              child: Text(
                text,
                style: const TextStyle(
                  fontSize: 13,
                  color: AppTokens.t2,
                  height: 1.6,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
