import 'json_utils.dart';

/// 充值套餐（`topup_products[]`）
class BillingPackage {
  const BillingPackage({
    required this.packageId,
    required this.label,
    required this.amountCents,
    required this.points,
    required this.basePoints,
    required this.promotionBonusPoints,
    required this.promotionBonusPercent,
    required this.totalPoints,
  });

  final String packageId;
  final String label;
  final int amountCents;

  /// 旧服务端字段，仅用于兼容历史订单
  final int points;
  final int basePoints;
  final int promotionBonusPoints;

  /// 赠送比例（百分比整数，非赠送档为 0）
  final int promotionBonusPercent;
  final int totalPoints;

  /// 到账积分：优先 total_points，其次旧字段
  int get displayPoints {
    if (totalPoints > 0) return totalPoints;
    if (points > 0) return points;
    return basePoints;
  }

  /// 面额（分）
  int get priceCents => amountCents;

  /// 形如 `100`（元）
  String get priceYuan => formatYuan(amountCents);

  /// 形如 `¥100`
  String get priceLabel => '¥$priceYuan';

  factory BillingPackage.fromJson(Map<String, dynamic> json) {
    return BillingPackage(
      packageId: asString(json['package_id']),
      label: asString(json['label']),
      amountCents: asInt(json['amount_cents']),
      points: asInt(json['points']),
      basePoints: asInt(json['base_points']),
      promotionBonusPoints: asInt(json['promotion_bonus_points']),
      promotionBonusPercent: asInt(json['promotion_bonus_percent']),
      totalPoints: asInt(json['total_points']),
    );
  }
}

/// 积分入账明细（`billing/ledger` → `items[]`）
class LedgerEntry {
  const LedgerEntry({
    required this.entryId,
    required this.direction,
    required this.pointsDelta,
    required this.balanceAfter,
    required this.sourceType,
    required this.sourceId,
    required this.createdAt,
  });

  final String entryId;

  /// credit / debit / lock / unlock
  final String direction;
  final int pointsDelta;
  final int balanceAfter;
  final String sourceType;
  final String sourceId;
  final String createdAt;

  bool get isIncome => pointsDelta >= 0;

  factory LedgerEntry.fromJson(Map<String, dynamic> json) {
    return LedgerEntry(
      entryId: asString(json['entry_id']),
      direction: asString(json['direction']),
      pointsDelta: asInt(json['points_delta']),
      balanceAfter: asInt(json['balance_after']),
      sourceType: asString(json['source_type']),
      sourceId: asString(json['source_id']),
      createdAt: asString(json['created_at']),
    );
  }
}

/// 充值订单（`recent_orders[]` / `pending_order`）
class BillingOrder {
  const BillingOrder({
    required this.orderId,
    required this.provider,
    required this.packageId,
    required this.amountCents,
    required this.status,
    required this.createdAt,
    required this.paidAt,
  });

  final String orderId;
  final String provider;
  final String packageId;
  final int amountCents;

  /// pending / paid / closed / failed / refunded
  final String status;
  final String createdAt;
  final String paidAt;

  factory BillingOrder.fromJson(Map<String, dynamic> json) {
    return BillingOrder(
      orderId: asString(json['order_id']),
      provider: asString(json['provider']),
      packageId: asString(json['package_id']),
      amountCents: asInt(json['amount_cents']),
      status: asString(json['status'], 'pending'),
      createdAt: asString(json['created_at']),
      paidAt: asString(json['paid_at']),
    );
  }
}

/// 支付方式
enum PaymentMode { nativeQr, pagePay, gatewayNotConfigured, unknown }

/// 下单返回的支付参数（`payment`）
class TopupPayment {
  const TopupPayment({
    required this.provider,
    required this.mode,
    required this.qrCodeUrl,
    required this.payUrl,
    required this.message,
  });

  final String provider;
  final PaymentMode mode;
  final String qrCodeUrl;
  final String payUrl;
  final String message;

  factory TopupPayment.fromJson(Map<String, dynamic> json) {
    final String raw = asString(json['mode']);
    final PaymentMode mode = switch (raw) {
      'native_qr' => PaymentMode.nativeQr,
      'page_pay' => PaymentMode.pagePay,
      'gateway_not_configured' => PaymentMode.gatewayNotConfigured,
      _ => PaymentMode.unknown,
    };
    return TopupPayment(
      provider: asString(json['provider'], 'alipay'),
      mode: mode,
      qrCodeUrl: asString(json['qr_code_url']),
      payUrl: asString(json['pay_url']),
      message: asString(json['message']),
    );
  }
}

/// 下单结果（`billing/topup-orders`）
class TopupOrderResult {
  const TopupOrderResult({
    required this.ok,
    required this.reused,
    required this.order,
    required this.payment,
  });

  final bool ok;
  final bool reused;
  final BillingOrder? order;
  final TopupPayment? payment;

  factory TopupOrderResult.fromJson(Map<String, dynamic> json) {
    return TopupOrderResult(
      ok: asBool(json['ok'], true),
      reused: asBool(json['reused']),
      order: json['order'] is Map
          ? BillingOrder.fromJson(asMap(json['order']))
          : null,
      payment: json['payment'] is Map
          ? TopupPayment.fromJson(asMap(json['payment']))
          : null,
    );
  }
}

/// 计费规则摘要（`pricing`）
class PricingInfo {
  const PricingInfo({
    required this.minClientVersion,
    required this.pointsPerCny,
    required this.ratioLabel,
  });

  final String minClientVersion;
  final int pointsPerCny;
  final String ratioLabel;

  factory PricingInfo.fromJson(Map<String, dynamic> json) {
    return PricingInfo(
      minClientVersion: asString(json['min_client_version']),
      pointsPerCny: asInt(json['points_per_cny']),
      ratioLabel: asString(json['ratio_label']),
    );
  }
}

/// 钱包计费总览（`billing/summary`）
class BillingSummary {
  const BillingSummary({
    required this.ok,
    required this.pointsBalance,
    required this.lockedPoints,
    required this.frozenPoints,
    required this.availablePoints,
    required this.planLabel,
    required this.planType,
    required this.planExpireAt,
    required this.basicClaimable,
    required this.dailyClaimable,
    required this.dailyClaimPoints,
    required this.dailyFirstClaimBonus,
    required this.dailyWeekCount,
    required this.dailyWeekMax,
    required this.dailyNextClaimAt,
    required this.extraBalance,
    required this.pricing,
    required this.packages,
    required this.recentLedger,
    required this.recentOrders,
    required this.pendingOrder,
  });

  final bool ok;
  final int pointsBalance;
  final int lockedPoints;
  final int frozenPoints;
  final int availablePoints;
  final String planLabel;
  final String planType;
  final String planExpireAt;
  final bool basicClaimable;
  final bool dailyClaimable;
  final int dailyClaimPoints;
  final int dailyFirstClaimBonus;
  final int dailyWeekCount;
  final int dailyWeekMax;
  final String dailyNextClaimAt;
  final int extraBalance;
  final PricingInfo pricing;
  final List<BillingPackage> packages;
  final List<LedgerEntry> recentLedger;
  final List<BillingOrder> recentOrders;
  final BillingOrder? pendingOrder;

  /// 展示用可用积分：available_points 优先，缺失时回落到 points_balance
  int get displayPoints =>
      availablePoints > 0 ? availablePoints : pointsBalance;

  factory BillingSummary.fromJson(Map<String, dynamic> json) {
    final Map<String, dynamic> wallet = asMap(json['wallet']);
    final Map<String, dynamic> plan = asMap(wallet['plan']);

    return BillingSummary(
      ok: asBool(json['ok'], true),
      pointsBalance: asInt(wallet['points_balance']),
      lockedPoints: asInt(wallet['locked_points']),
      frozenPoints: asInt(wallet['frozen_points']),
      availablePoints: asInt(wallet['available_points']),
      planLabel: asString(plan['plan_label']),
      planType: asString(plan['plan_type']),
      planExpireAt: asString(plan['plan_expire_at']),
      basicClaimable: asBool(plan['basic_claimable']),
      dailyClaimable: asBool(plan['daily_claimable']),
      dailyClaimPoints: asInt(plan['daily_claim_points']),
      dailyFirstClaimBonus: asInt(plan['daily_first_claim_bonus']),
      dailyWeekCount: asInt(plan['daily_week_count']),
      dailyWeekMax: asInt(plan['daily_week_max'], 7),
      dailyNextClaimAt: asString(plan['daily_next_claim_at']),
      extraBalance: asInt(plan['extra_balance']),
      pricing: PricingInfo.fromJson(asMap(json['pricing'])),
      packages: asList(json['topup_products'])
          .whereType<Map>()
          .map((Map item) => BillingPackage.fromJson(item.cast<String, dynamic>()))
          .toList(),
      recentLedger: asList(json['recent_ledger'])
          .whereType<Map>()
          .map((Map item) => LedgerEntry.fromJson(item.cast<String, dynamic>()))
          .toList(),
      recentOrders: asList(json['recent_orders'])
          .whereType<Map>()
          .map((Map item) => BillingOrder.fromJson(item.cast<String, dynamic>()))
          .toList(),
      pendingOrder: json['pending_order'] is Map
          ? BillingOrder.fromJson(asMap(json['pending_order']))
          : null,
    );
  }
}

/// 积分明细分页（`billing/ledger`）
class BillingLedgerPage {
  const BillingLedgerPage({
    required this.items,
    required this.total,
    required this.hasMore,
  });

  final List<LedgerEntry> items;
  final int total;
  final bool hasMore;

  factory BillingLedgerPage.fromJson(Map<String, dynamic> json) {
    final List<LedgerEntry> items = asList(json['items'])
        .whereType<Map>()
        .map((Map item) => LedgerEntry.fromJson(item.cast<String, dynamic>()))
        .toList();
    return BillingLedgerPage(
      items: items,
      total: asInt(json['total'], items.length),
      hasMore: asBool(json['has_more']),
    );
  }
}

/// 领取结果（`plan-basic/claim` / `daily-extra/claim`）
class ClaimResult {
  const ClaimResult({
    required this.ok,
    required this.claimedPoints,
    required this.message,
  });

  final bool ok;
  final int claimedPoints;
  final String message;

  factory ClaimResult.fromJson(Map<String, dynamic> json) {
    return ClaimResult(
      ok: asBool(json['ok'], true),
      claimedPoints: asInt(json['claimed_points']),
      message: asString(json['message']),
    );
  }
}
