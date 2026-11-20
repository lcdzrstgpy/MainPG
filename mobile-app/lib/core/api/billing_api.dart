import '../config/app_config.dart';
import '../http/cloud_client.dart';
import '../models/billing_models.dart';

/// 计费 / 积分 / 充值服务（auth-api `/api/customer/billing/*`）
class BillingApi {
  BillingApi(this._cloud);

  final CloudClient _cloud;

  /// 钱包 + 套餐 + 价格规则 + 近期流水/订单 + 在途订单
  Future<BillingSummary> summary() async {
    final Map<String, dynamic> data =
        await _cloud.auth.get(AppConfig.billingSummaryPath);
    return BillingSummary.fromJson(data);
  }

  /// 积分入账明细分页
  Future<BillingLedgerPage> ledger({
    String category = '',
    int limit = 20,
    int offset = 0,
  }) async {
    final Map<String, dynamic> data = await _cloud.auth.get(
      AppConfig.billingLedgerPath,
      query: <String, dynamic>{
        'limit': limit,
        'offset': offset,
        if (category.isNotEmpty) 'category': category,
      },
    );
    return BillingLedgerPage.fromJson(data);
  }

  /// 基础版每周直接领取（服务端按周幂等）
  Future<ClaimResult> claimBasic() async {
    final Map<String, dynamic> data =
        await _cloud.auth.post(AppConfig.billingClaimBasicPath);
    return ClaimResult.fromJson(data);
  }

  /// 每日签到（服务端按北京自然日幂等）
  Future<ClaimResult> claimDaily() async {
    final Map<String, dynamic> data =
        await _cloud.auth.post(AppConfig.billingClaimDailyPath);
    return ClaimResult.fromJson(data);
  }

  /// 创建充值订单（支付宝）。`idempotencyKey` 由调用方生成。
  Future<TopupOrderResult> topupOrder({
    required String packageId,
    required String idempotencyKey,
    int? amountCents,
  }) async {
    final Map<String, dynamic> data = await _cloud.auth.post(
      AppConfig.billingTopupOrdersPath,
      body: <String, dynamic>{
        'provider': 'alipay',
        'package_id': packageId,
        if (amountCents != null) 'amount_cents': amountCents,
        'idempotency_key': idempotencyKey,
      },
    );
    return TopupOrderResult.fromJson(data);
  }

  /// 自定义金额报价（到账积分由服务端计算）
  Future<BillingPackage?> topupQuote(int amountCents) async {
    final Map<String, dynamic> data = await _cloud.auth.post(
      AppConfig.billingTopupQuotePath,
      body: <String, dynamic>{'amount_cents': amountCents},
    );
    final dynamic product = data['product'];
    if (product is Map) {
      return BillingPackage.fromJson(product.cast<String, dynamic>());
    }
    return null;
  }
}
